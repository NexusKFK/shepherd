"""Native app protocol: one JSON request/reply per line over private stdio pipes.

No HTTP server, web renderer, sockets or external dependencies are used here.
"""
from __future__ import annotations

import argparse
import json
import sys

from .application import Application
from .store import Invalid

METHODS = frozenset(("appearance", "refresh", "econf-scan", "econf-fetch", "econf-record", "econf-dismiss", "econf-restore",
                    "size", "risk-policy", "exposure-input", "plans", "status", "fills", "void-fill", "review",
                    "adopt", "preparation", "events", "event-delete", "backup", "import-snapshot", "use-local-source", "modules"))


def dispatch(app, request):
    if not isinstance(request, dict) or not isinstance(request.get("id"), str):
        raise Invalid("请求必须带字符串编号")
    method, params = request.get("method"), request.get("params", {})
    if not isinstance(params, dict):
        raise Invalid("请求参数必须为对象")
    if method == "state":
        return app.state()
    if method == "snapshot-history":
        value = app.store.saved_snapshot(params.get("id"))
        if value is None:
            raise Invalid("找不到该历史快照")
        return value
    if method not in METHODS:
        raise Invalid("不支持的原生操作")
    return app.post("/api/" + method, params)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--source-home")
    parser.add_argument("--econf-inbox")
    args = parser.parse_args()
    app = Application(args.data_dir, args.source_home, args.econf_inbox)
    while True:
        line = sys.stdin.buffer.readline(131073)
        if not line:
            break
        request = None
        try:
            if len(line) > 131072:
                # Do not parse the remaining fragment as a second request.
                while line and not line.endswith(b"\n"):
                    line = sys.stdin.buffer.readline(131073)
                raise Invalid("请求过大")
            request = json.loads(line, parse_constant=lambda _: (_ for _ in ()).throw(Invalid("非有限数值")))
            reply = {"id": request.get("id") if isinstance(request, dict) else None,
                     "result": dispatch(app, request)}
        except (Invalid, ValueError, TypeError) as error:
            reply = {"id": request.get("id") if isinstance(request, dict) else None, "error": str(error)}
        except Exception as error:
            print(type(error).__name__ + ": native engine request failed", file=sys.stderr, flush=True)
            reply = {"id": request.get("id") if isinstance(request, dict) else None,
                     "error": "本机操作失败；未提交的写入已回滚，请重试。"}
        print(json.dumps(reply, ensure_ascii=False, allow_nan=False), flush=True)


if __name__ == "__main__":
    main()
