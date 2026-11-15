# -*- coding: utf-8 -*-
"""表单批量录入。

骨架：直接对 /submit 发 POST，不保持会话，不取 CSRF token，不重试。
"""
import urllib.parse
import urllib.request


class Recorder(object):
    def __init__(self, base):
        self.base = base.rstrip("/")
        self.opener = urllib.request.build_opener()

    def fetch_form(self, form_id):
        url = "%s/form/%s" % (self.base, form_id)
        with self.opener.open(url, timeout=10) as r:
            return r.read().decode("utf-8")

    def submit(self, form_id, data):
        body = urllib.parse.urlencode(data).encode("utf-8")
        req = urllib.request.Request("%s/submit/%s" % (self.base, form_id),
                                     data=body, method="POST")
        try:
            with self.opener.open(req, timeout=10) as r:
                return r.status, r.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode("utf-8")


def load_rows(path):
    import csv
    with open(path, "r", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def record_all(rec, form_id, rows):
    results = []
    for row in rows:
        status, body = rec.submit(form_id, row)
        results.append({"row": row, "status": status, "body": body})
    return results
