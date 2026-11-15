# -*- coding: utf-8 -*-
"""本地模拟表单站点。

规则：/form 返回带 CSRF token 的 HTML；/submit 需要 cookie 会话 + 正确 token；
同一业务主键重复提交返回 409；每 5 次提交会话失效一次，需要重新取表单。
"""
import json
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

STATE = {"counter": 0, "sessions": {}, "records": {}}
LOCK = threading.Lock()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json", cookies=None):
        self.send_response(code)
        self.send_header("content-type", ctype)
        for c in cookies or []:
            self.send_header("set-cookie", c)
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/form/"):
            form_id = self.path.split("/")[-1]
            with LOCK:
                STATE["counter"] += 1
                token = "tk%d" % STATE["counter"]
                sid = "s%d" % STATE["counter"]
                STATE["sessions"][sid] = token
            html = ('<html><form method="post" action="/submit/%s">'
                    '<input name="csrf" value="%s"/></form></html>' % (form_id, token))
            self._send(200, html.encode("utf-8"), "text/html", ["sid=%s; Path=/" % sid])
            return
        self._send(404, b"{}")

    def do_POST(self):
        form_id = self.path.split("/")[-1]
        n = int(self.headers.get("content-length") or 0)
        body = self.rfile.read(n).decode("utf-8")
        form = dict(urllib.parse.parse_qsl(body))
        cookie = self.headers.get("cookie") or ""
        sid = ""
        for part in cookie.split(";"):
            part = part.strip()
            if part.startswith("sid="):
                sid = part[4:]
        with LOCK:
            token = STATE["sessions"].get(sid)
            if not token:
                self._send(403, json.dumps({"error": "no session"}).encode())
                return
            if form.get("csrf") != token:
                self._send(403, json.dumps({"error": "bad csrf"}).encode())
                return
            key = form.get("biz_id") or ""
            if key in STATE["records"]:
                self._send(409, json.dumps({"error": "duplicate"}).encode())
                return
            STATE["records"][key] = form
            STATE["counter"] += 1
            if STATE["counter"] % 5 == 0:
                STATE["sessions"].pop(sid, None)
            self._send(200, json.dumps({"ok": True, "id": key}).encode())


def serve(port=8099):
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    return srv


if __name__ == "__main__":
    import sys
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8099
    print("mock form server on %d" % port)
    serve(port).serve_forever()
