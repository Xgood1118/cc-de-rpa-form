# -*- coding: utf-8 -*-
import os
import sys
import threading
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from rpa.core import Recorder, load_rows
from rpa.mock_server import serve

PORT = 8123


class LiveTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = serve(PORT)
        cls.th = threading.Thread(target=cls.srv.serve_forever, daemon=True)
        cls.th.start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def test_form_has_csrf(self):
        rec = Recorder("http://127.0.0.1:%d" % PORT)
        html = rec.fetch_form("f001")
        self.assertIn("csrf", html)

    def test_submit_without_session_is_403(self):
        rec = Recorder("http://127.0.0.1:%d" % PORT)
        status, body = rec.submit("f001", {"biz_id": "T1", "csrf": "wrong"})
        self.assertEqual(status, 403)


class LoadTest(unittest.TestCase):
    def test_rows(self):
        rows = load_rows(os.path.join(ROOT, "rows.csv"))
        self.assertEqual(len(rows), 12)
        ids = [r["biz_id"] for r in rows]
        self.assertEqual(ids.count("B001"), 2)


if __name__ == "__main__":
    unittest.main()
