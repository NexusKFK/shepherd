from __future__ import annotations

import argparse
import hmac
import hashlib
import json
import mimetypes
import os
import secrets
import signal
import threading
from datetime import datetime
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .risk import calculate
from .sources import ET, Sources, merged_positions
from .store import Invalid, Store, iso_day, now, text
from .econf import EconfBox
from .context import enrich_positions, operation_positions, supplemental_history
from .exposure import analyze_exposure
from .strategies import recognize_operations
from .store import number

STATIC = Path(__file__).parent / "static"


# Compatibility for the optional browser development tool.
from .application import Application


def make_server(app, port=0, token=None):
    session_token = token or secrets.token_urlsafe(32)

    class Handler(BaseHTTPRequestHandler):
        server_version = "TradingDesk"

        def log_message(self, *args):
            pass  # 本机请求不记录持仓、表单或会话凭据。

        def send(self, status, body, content_type="application/json; charset=utf-8", headers=None):
            if not isinstance(body, bytes):
                body = json.dumps(body, ensure_ascii=False, allow_nan=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
            for k,v in (headers or {}).items():
                self.send_header(k,v)
            self.end_headers()
            self.wfile.write(body)

        def host_ok(self):
            return self.headers.get("Host") == f"127.0.0.1:{self.server.server_port}"

        def authenticated(self):
            try:
                cookies = SimpleCookie(self.headers.get("Cookie", ""))
                return "desk" in cookies and hmac.compare_digest(cookies["desk"].value, session_token)
            except Exception:
                return False

        def do_GET(self):
            if not self.host_ok():
                return self.send(403, {"error": "仅支持本机访问"})
            url = urlsplit(self.path)
            if url.path == "/health":
                return self.send(200, {"service": "shepherd-workbench", "ready": True})
            if url.path == "/session":
                supplied = parse_qs(url.query).get("token", [""])[0]
                if not hmac.compare_digest(supplied, session_token):
                    return self.send(403, {"error": "会话已过期，请重新打开 App"})
                return self.send(303, b"", headers={"Set-Cookie": f"desk={session_token}; Path=/; HttpOnly; SameSite=Strict", "Location": "/"})
            if not self.authenticated():
                return self.send(401, {"error": "请通过 App 或启动器打开工作台"})
            try:
                if url.path == "/api/state":
                    return self.send(200, app.state())
                if url.path == "/api/export":
                    return self.send(200, app.store.export())
                if url.path == "/api/econf":
                    return self.send(200, app.econf.summary())
                allowed = {"/": "index.html", "/app.js": "app.js", "/style.css": "style.css", "/icon.png": "icon.png"}
                if url.path in allowed:
                    f = STATIC / allowed[url.path]
                    mime = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
                    return self.send(200, f.read_bytes(), mime + ("; charset=utf-8" if mime.startswith("text/") or mime == "application/javascript" else ""))
                return self.send(404, {"error": "页面不存在"})
            except Exception:
                return self.send(500, {"error": "读取失败，原始数据保留。请检查本机来源文件后重试。"})

        def do_POST(self):
            origin = self.headers.get("Origin")
            expected = f"http://127.0.0.1:{self.server.server_port}"
            if not self.host_ok() or not self.authenticated() or origin not in (None, expected) or self.headers.get("X-Desk-Request") != "1":
                return self.send(403, {"error": "请求来源无效"})
            if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                return self.send(415, {"error": "需要 JSON 格式"})
            try:
                size = int(self.headers.get("Content-Length", 0))
                if not 0 < size <= 131072:
                    return self.send(413, {"error": "请求大小超出限制"})
                data = json.loads(self.rfile.read(size), parse_constant=lambda _: (_ for _ in ()).throw(Invalid("数值不能是 NaN 或 Infinity")))
                if not isinstance(data, dict):
                    raise Invalid("请求格式不正确")
                return self.send(200, app.post(urlsplit(self.path).path, data))
            except (Invalid, ValueError, TypeError) as e:
                return self.send(400, {"error": str(e)})
            except Exception:
                return self.send(500, {"error": "保存失败，未提交的操作已回滚。请重试。"})

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    server.session_token = session_token
    return server


def main():
    parser = argparse.ArgumentParser(description="Shepherd 本机交易工作台")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--data-dir", default=os.environ.get("TRADING_DATA_DIR", str(Path.home()/"Library/Application Support/Shepherd/TradingDesk")))
    parser.add_argument("--source-home")
    parser.add_argument("--ready-file")
    parser.add_argument("--open", action="store_true")
    args = parser.parse_args()
    app = Application(args.data_dir, args.source_home)
    server = make_server(app, args.port, os.environ.get("TRADING_SESSION_TOKEN"))
    base = f"http://127.0.0.1:{server.server_port}"
    if args.ready_file:
        path = Path(args.ready_file)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps({"url": base, "pid": os.getpid()}))
        os.chmod(temp, 0o600)
        temp.replace(path)
    if args.open:
        import webbrowser
        webbrowser.open(base+"/session?token="+server.session_token)
    print("交易工作台已启动："+base, flush=True)
    server.serve_forever()
