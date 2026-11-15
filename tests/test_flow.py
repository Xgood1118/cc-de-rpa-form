# -*- coding: utf-8 -*-
"""进程内 mock_server 集成测试。

覆盖：取 token 后提交成功、会话失效后自动恢复、409 去重、
本地校验拦截、断点续跑、并发会话隔离、限速与 CLI 退出码。
"""
import json
import os
import sys
import tempfile
import threading
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from rpa import mock_server
from rpa.cli import main as cli_main
from rpa.core import Recorder, record_all
from rpa.mock_server import serve
from rpa.ratelimit import RateLimiter
from rpa.state import StateStore

PORT = 8234

RULES = {
    "required": ["biz_id", "name", "city", "amount", "status"],
    "max_length": {"biz_id": 32, "name": 20, "city": 10},
    "enum": {"status": ["normal", "vip"]},
    "numeric": ["amount"],
}


def reset_server():
    with mock_server.LOCK:
        mock_server.STATE["counter"] = 0
        mock_server.STATE["sessions"].clear()
        mock_server.STATE["records"].clear()


def make_row(biz_id, **over):
    row = {"biz_id": biz_id, "name": "n" + biz_id, "city": "长春",
           "amount": "100", "status": "normal"}
    row.update(over)
    return row


class FlowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = serve(PORT)
        cls.th = threading.Thread(target=cls.srv.serve_forever, daemon=True)
        cls.th.start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def setUp(self):
        reset_server()
        self.tmp = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmp, "state.db")
        self.store = StateStore(self.db_path)
        self.base = "http://127.0.0.1:%d" % PORT

    def tearDown(self):
        self.store.close()

    def run_batch(self, rows, **kw):
        rec = Recorder(self.base)
        return record_all(rec, "f001", rows, config=RULES, store=self.store, **kw)

    def test_submit_success_after_fetching_token(self):
        result = self.run_batch([make_row("T1")])
        self.assertEqual(result.submitted, 1)
        self.assertEqual(result.failed, 0)
        # 服务端确实落库，且带的是当次表单返回的 token
        self.assertIn("T1", mock_server.STATE["records"])
        self.assertEqual(mock_server.STATE["records"]["T1"]["name"], "nT1")

    def test_session_expiry_auto_recovers_within_batch(self):
        # 10 行：服务端每 5 次成功 POST 清一次会话，第 6 次提交必撞 403，
        # 必须靠自动重新取表单、换新 token 后重试同一行来恢复
        rows = [make_row("S%d" % i) for i in range(10)]
        result = self.run_batch(rows)
        self.assertEqual(result.submitted, 10)
        self.assertEqual(result.failed, 0)
        self.assertGreaterEqual(result.retried, 1)
        self.assertEqual(len(mock_server.STATE["records"]), 10)

    def test_local_dedup_before_submit(self):
        rows = [make_row("D1"), make_row("D1")]
        result = self.run_batch(rows)
        self.assertEqual(result.total, 2)
        self.assertEqual(result.submitted, 1)
        self.assertEqual(result.duplicate_skipped, 1)
        self.assertEqual(len(mock_server.STATE["records"]), 1)
        # 重复行根本没有发往服务端，服务端没有机会返回 409
        self.assertIn("D1", mock_server.STATE["records"])

    def test_server_side_409_treated_as_failure(self):
        # 模拟上轮由别的途径写入、本地状态库不知道的记录：服务端 409
        with mock_server.LOCK:
            mock_server.STATE["records"]["X1"] = {"biz_id": "X1", "csrf": "x"}
        result = self.run_batch([make_row("X1")])
        self.assertEqual(result.submitted, 0)
        self.assertEqual(result.failed, 1)
        self.assertIn("409", result.rows[0]["reason"])

    def test_local_validation_blocks_dirty_rows(self):
        rows = [
            make_row("V1", name=""),                       # 必填为空
            make_row("V2", name="超" * 21),                # 超长
            make_row("V3", status="unknown"),              # 枚举越界
            make_row("V4", amount="abc"),                  # 数值非法
            make_row("V5"),                                # 合法
        ]
        result = self.run_batch(rows)
        self.assertEqual(result.rejected, 4)
        self.assertEqual(result.submitted, 1)
        self.assertEqual(result.failed, 0)
        for biz in ("V1", "V2", "V3", "V4"):
            self.assertNotIn(biz, mock_server.STATE["records"])
        self.assertIn("V5", mock_server.STATE["records"])
        # 被拒行同样落库，可追溯原因
        self.assertEqual(self.store.get_status("V1"), "rejected")

    def test_resume_skips_already_submitted_rows(self):
        rows1 = [make_row("R1"), make_row("R2"), make_row("R3")]
        first = self.run_batch(rows1)
        self.assertEqual(first.submitted, 3)

        # 模拟中断重启：新的 store 指向同一个 SQLite
        self.store.close()
        self.store = StateStore(self.db_path)
        before = len(mock_server.STATE["records"])

        rows2 = [make_row("R1"), make_row("R2"), make_row("R4")]
        second = self.run_batch(rows2)
        self.assertEqual(second.submitted, 1)
        self.assertEqual(second.duplicate_skipped, 2)
        self.assertEqual(second.failed, 0)
        # R1/R2 没有再次提交，服务端记录数只增加 1
        self.assertEqual(len(mock_server.STATE["records"]), before + 1)
        self.assertTrue(self.store.is_submitted("R1"))

    def test_concurrency_uses_isolated_sessions(self):
        rows = [make_row("C%d" % i) for i in range(12)]
        result = self.run_batch(rows, concurrency=4)
        self.assertEqual(result.submitted, 12)
        self.assertEqual(result.failed, 0)
        self.assertEqual(len(mock_server.STATE["records"]), 12)

    def test_403_retry_cap_marks_failure_with_reason(self):
        import rpa.core as core

        # 让所有 POST 恒为 403：重试 max 次后必须记失败，不能无限重取表单
        original_submit = core.Recorder.submit

        def always_403(self, form_id, data):
            return 403, '{"error": "no session"}'

        core.Recorder.submit = always_403
        try:
            result = self.run_batch([make_row("F1")], max_403_retries=2)
        finally:
            core.Recorder.submit = original_submit

        self.assertEqual(result.submitted, 0)
        self.assertEqual(result.failed, 1)
        row = result.rows[0]
        self.assertEqual(row["attempts"], 3)  # 首次 + 2 次重试
        self.assertEqual(self.store.get_status("F1"), "failed")
        self.assertIn("重试已达上限", row["reason"])
        self.assertNotIn("F1", mock_server.STATE["records"])

    def test_unreachable_server_becomes_failure_not_crash(self):
        import socket
        # 找一个确定空闲的端口
        probe = socket.socket()
        probe.bind(("127.0.0.1", 0))
        free_port = probe.getsockname()[1]
        probe.close()

        dead = Recorder("http://127.0.0.1:%d" % free_port)
        result = record_all(dead, "f001", [make_row("U1")], config=RULES,
                            store=self.store)
        self.assertEqual(result.submitted, 0)
        self.assertEqual(result.failed, 1)
        self.assertIn("获取表单失败", result.rows[0]["reason"])
        self.assertEqual(self.store.get_status("U1"), "failed")

    def test_rate_limiter_spaces_requests(self):
        lim = RateLimiter(10)  # 每 100ms 一次
        start = time.monotonic()
        for _ in range(4):
            lim.acquire()
        elapsed = time.monotonic() - start
        self.assertGreaterEqual(elapsed, 0.28)

    def test_rate_limiter_disabled(self):
        lim = RateLimiter(0)
        start = time.monotonic()
        for _ in range(100):
            lim.acquire()
        self.assertLess(time.monotonic() - start, 0.1)

    def test_rate_limit_covers_retry_posts(self):
        # 用计数限流器：每次 POST（含 403 重试）都必须取一次令牌。
        # 干净服务端上 6 行 -> 第 5 次提交后会话失效 -> 第 6 行 403 后重试，共 7 次 POST。
        class CountingLimiter(object):
            def __init__(self):
                self.count = 0

            def acquire(self):
                self.count += 1

        import rpa.core as core
        limiter = CountingLimiter()
        original = core.RateLimiter
        core.RateLimiter = lambda per_second: limiter
        try:
            rows = [make_row("Q%d" % i) for i in range(6)]
            result = self.run_batch(rows)
        finally:
            core.RateLimiter = original

        self.assertEqual(result.submitted, 6)
        self.assertEqual(result.retried, 1)
        self.assertEqual(limiter.count, 7)


class CliTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = serve(PORT + 1)
        cls.th = threading.Thread(target=cls.srv.serve_forever, daemon=True)
        cls.th.start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def setUp(self):
        reset_server()
        self.tmp = tempfile.mkdtemp()

    def _write_rows(self, rows):
        import csv
        path = os.path.join(self.tmp, "rows.csv")
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["biz_id", "name", "city", "amount", "status"])
            w.writeheader()
            w.writerows(rows)
        return path

    def _write_config(self, rules):
        path = os.path.join(self.tmp, "config.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(rules, f)
        return path

    def test_cli_exit_codes(self):
        base = "http://127.0.0.1:%d" % (PORT + 1)
        db = os.path.join(self.tmp, "cli.db")
        cfg_path = self._write_config(RULES)

        # 含脏数据与重复 -> 退出码 1，六项统计齐全
        dirty_rows = [make_row("K1"), make_row("K2", name=""), make_row("K1")]
        dirty_path = self._write_rows(dirty_rows)
        code = cli_main([dirty_path, "--form", "f001", "--base", base,
                         "--config", cfg_path, "--db", db])
        self.assertEqual(code, 1)

        # 干净数据再跑：全部已成功过 -> 跳过，无失败无拒绝 -> 退出码 0
        clean_path = self._write_rows([make_row("K1")])
        code = cli_main([clean_path, "--form", "f001", "--base", base,
                         "--config", cfg_path, "--db", db])
        self.assertEqual(code, 0)

        # 配置文件不存在 -> 退出码 2
        code = cli_main([clean_path, "--config", os.path.join(self.tmp, "nope.json"),
                         "--db", db, "--base", base])
        self.assertEqual(code, 2)

        # 非法 JSON -> 退出码 2
        bad_cfg = os.path.join(self.tmp, "bad.json")
        with open(bad_cfg, "w", encoding="utf-8") as f:
            f.write("{not json")
        code = cli_main([clean_path, "--config", bad_cfg, "--db", db, "--base", base])
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
