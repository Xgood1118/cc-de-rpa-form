# -*- coding: utf-8 -*-
"""断点续跑：每行结果落 SQLite，重启后跳过已成功的行。"""
import sqlite3
import threading

# 终态：本行已被服务端接受，重启直接跳过
STATUS_SUBMITTED = "submitted"
# 本地校验拦截，未发往服务端（仅本次运行内存统计，不落库）
STATUS_REJECTED = "rejected"
# 重试耗尽或服务端返回不可恢复错误
STATUS_FAILED = "failed"
# 批内重复或上轮已成功，未再提交
STATUS_SKIPPED = "skipped"


class StateStore(object):
    def __init__(self, path):
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS results ("
            "biz_id TEXT PRIMARY KEY, status TEXT NOT NULL, "
            "attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT)"
        )
        self._conn.commit()

    def is_submitted(self, biz_id):
        with self._lock:
            cur = self._conn.execute(
                "SELECT 1 FROM results WHERE biz_id = ? AND status = ?",
                (biz_id, STATUS_SUBMITTED),
            )
            return cur.fetchone() is not None

    def get_status(self, biz_id):
        with self._lock:
            cur = self._conn.execute(
                "SELECT status FROM results WHERE biz_id = ?", (biz_id,)
            )
            row = cur.fetchone()
            return row[0] if row else None

    def upsert(self, biz_id, status, attempts, last_error=None):
        with self._lock:
            self._conn.execute(
                "INSERT INTO results(biz_id, status, attempts, last_error) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(biz_id) DO UPDATE SET "
                "status = excluded.status, attempts = excluded.attempts, "
                "last_error = excluded.last_error",
                (biz_id, status, attempts, last_error),
            )
            self._conn.commit()

    def close(self):
        with self._lock:
            self._conn.close()
