# -*- coding: utf-8 -*-
"""全局限速器：每秒最多 N 次提交（N <= 0 表示不限速）。"""
import threading
import time


class RateLimiter(object):
    def __init__(self, per_second):
        self.per_second = per_second
        self.min_interval = 1.0 / per_second if per_second and per_second > 0 else 0.0
        self._lock = threading.Lock()
        self._next_at = 0.0

    def acquire(self):
        """阻塞到下一个被允许发起提交的时刻。"""
        if self.min_interval <= 0:
            return
        with self._lock:
            now = time.monotonic()
            wait = self._next_at - now
            if wait > 0:
                time.sleep(wait)
                now = time.monotonic()
            self._next_at = max(now, self._next_at) + self.min_interval
