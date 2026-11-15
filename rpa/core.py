# -*- coding: utf-8 -*-
"""表单批量录入。

Recorder 保留骨架时期的方法签名（fetch_form / submit），内部补齐：
- CookieJar 保持 sid 会话，HTML 解析 csrf token；
- submit 自动带上会话 cookie，缺省注入已取到的 csrf token；
- 每个 Recorder 实例是一条独立连接，并发时按线程各持一个，会话互不串用。

批量编排（record_all）负责本地去重、校验前置、403 换会话重试、
限速、并发与 SQLite 断点续跑。
"""
import csv
import threading
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser
from http.cookiejar import CookieJar

from .ratelimit import RateLimiter
from .state import STATUS_FAILED, STATUS_REJECTED, STATUS_SKIPPED, STATUS_SUBMITTED
from .validation import validate_row


class _CsrfParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.csrf = None

    def handle_starttag(self, tag, attrs):
        if tag != "input":
            return
        attrs = dict(attrs)
        if attrs.get("name") == "csrf":
            self.csrf = attrs.get("value")


def parse_csrf(html):
    parser = _CsrfParser()
    parser.feed(html)
    return parser.csrf


class Recorder(object):
    """一条到表单站点的连接，会话（sid + csrf）在实例内隔离。"""

    def __init__(self, base):
        self.base = base.rstrip("/")
        self.cookiejar = CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.cookiejar)
        )
        self.csrf = None

    def fetch_form(self, form_id):
        """GET /form/<id>，保存 sid cookie 与页面中的 csrf token。"""
        url = "%s/form/%s" % (self.base, form_id)
        with self.opener.open(url, timeout=10) as r:
            html = r.read().decode("utf-8")
        self.csrf = parse_csrf(html)
        return html

    def submit(self, form_id, data):
        """POST /submit/<id>。

        自动携带本连接的 sid cookie；data 未显式给 csrf 时注入当前 token，
        显式给了则原样发送（骨架测试依赖此行为）。
        """
        payload = dict(data)
        if "csrf" not in payload and self.csrf is not None:
            payload["csrf"] = self.csrf
        body = urllib.parse.urlencode(payload).encode("utf-8")
        req = urllib.request.Request(
            "%s/submit/%s" % (self.base, form_id), data=body, method="POST"
        )
        try:
            with self.opener.open(req, timeout=10) as r:
                return r.status, r.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode("utf-8")


def load_rows(path):
    with open(path, "r", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


class BatchResult(object):
    """一次批量运行的计数与逐行结果。"""

    def __init__(self):
        self.total = 0
        self.submitted = 0
        self.duplicate_skipped = 0
        self.rejected = 0
        self.failed = 0
        self.retried = 0
        self.rows = []

    def as_dict(self):
        return {
            "total": self.total,
            "submitted": self.submitted,
            "duplicate_skipped": self.duplicate_skipped,
            "rejected": self.rejected,
            "failed": self.failed,
            "retried": self.retried,
        }


def _submit_with_session(rec, form_id, row, max_retries, limiter=None):
    """单行提交：确保连接已有会话后 POST；403 时重新取表单换新 token 重试同一行。

    返回 (status, attempts, retried, last_error)。attempts 为实际 POST 次数，
    每次 POST 前都经过限流器（重试产生的提交同样受限）。
    所有网络异常都被收敛成失败结果，不向调用方抛出。
    """
    attempts = 0
    retried = False
    last_error = None

    if rec.csrf is None:
        # 该连接的首次取表单，与 403 后的换会话走同一套异常保护
        try:
            rec.fetch_form(form_id)
        except (urllib.error.URLError, OSError) as exc:
            return None, 0, False, "获取表单失败: %s" % exc
        if rec.csrf is None:
            return None, 0, False, "表单页中没有 csrf token"

    while True:
        attempts += 1
        if limiter is not None:
            limiter.acquire()
        try:
            status, body = rec.submit(form_id, row)
        except (urllib.error.URLError, OSError) as exc:
            return None, attempts, retried, "网络错误: %s" % exc

        if status == 403 and attempts <= max_retries:
            # 会话失效（每 5 次提交服务端清会话）：换新会话后重试同一行
            retried = True
            last_error = "403 会话失效: %s" % body[:80]
            try:
                rec.fetch_form(form_id)
            except (urllib.error.URLError, OSError) as exc:
                return None, attempts, retried, "重新获取会话失败: %s" % exc
            if rec.csrf is None:
                return None, attempts, retried, "重新获取表单后仍无 csrf token"
            continue

        if status == 200:
            return status, attempts, retried, None
        if status == 403:
            return status, attempts, retried, "403 重试已达上限(%d 次): %s" % (max_retries, body[:80])
        return status, attempts, retried, "服务端返回 %s: %s" % (status, body[:80])


def record_all(rec, form_id, rows, config=None, store=None,
               concurrency=1, rate_limit=0, max_403_retries=3):
    """批量录入。

    rec 仅提供 base 地址；实际提交按工作线程各建独立 Recorder，
    保证并发时会话与 token 按连接隔离。
    """
    result = BatchResult()
    result.total = len(rows)

    rules = config or {}
    limiter = RateLimiter(rate_limit)
    local = threading.local()

    def worker_rec():
        # 每条连接（每个工作线程）一个独立 Recorder，sid 与 csrf 按连接隔离；
        # 首次取表单与 403 后的换会话都在 _submit_with_session 内完成。
        if not hasattr(local, "rec"):
            local.rec = Recorder(rec.base)
        return local.rec

    # 提交前去重：同一 biz_id 只放行第一次出现的行
    seen = set()
    pending = []  # (原始行号, row)
    for index, row in enumerate(rows):
        biz_id = row.get("biz_id", "")
        if biz_id in seen:
            result.duplicate_skipped += 1
            result.rows.append({
                "index": index, "biz_id": biz_id, "status": "duplicate",
                "attempts": 0, "reason": "批内重复 biz_id，已跳过",
            })
            continue
        seen.add(biz_id)

        if store is not None and store.is_submitted(biz_id):
            # 断点续跑：上轮已成功，不重复提交
            result.duplicate_skipped += 1
            result.rows.append({
                "index": index, "biz_id": biz_id, "status": STATUS_SKIPPED,
                "attempts": 0, "reason": "上次运行已提交成功，跳过",
            })
            continue

        errors = validate_row(row, rules)
        if errors:
            reason = "; ".join(errors)
            result.rejected += 1
            if store is not None:
                store.upsert(biz_id, STATUS_REJECTED, 0, reason)
            result.rows.append({
                "index": index, "biz_id": biz_id, "status": STATUS_REJECTED,
                "attempts": 0, "reason": reason,
            })
            continue

        pending.append((index, row))

    def process(item):
        index, row = item
        biz_id = row.get("biz_id", "")
        status, attempts, retried, last_error = _submit_with_session(
            worker_rec(), form_id, row, max_403_retries, limiter
        )
        return index, row, biz_id, status, attempts, retried, last_error

    if concurrency and concurrency > 1:
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            outcomes = list(pool.map(process, pending))
    else:
        outcomes = [process(item) for item in pending]

    for index, row, biz_id, status, attempts, retried, last_error in outcomes:
        if retried:
            result.retried += 1
        if status == 200:
            result.submitted += 1
            if store is not None:
                store.upsert(biz_id, STATUS_SUBMITTED, attempts, None)
            result.rows.append({
                "index": index, "biz_id": biz_id, "status": STATUS_SUBMITTED,
                "attempts": attempts, "reason": None,
            })
        else:
            result.failed += 1
            if store is not None:
                store.upsert(biz_id, STATUS_FAILED, attempts, last_error)
            result.rows.append({
                "index": index, "biz_id": biz_id, "status": STATUS_FAILED,
                "attempts": attempts, "reason": last_error,
            })

    result.rows.sort(key=lambda r: r["index"])
    return result
