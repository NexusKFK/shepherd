"""Weekly watch (state.json from an outside job): weekly bars of a few ETFs with the 20/50/100/200-week EMAs.

The author's file is built after the US close by a separate job (RSP and VUG); any symbols in the file are shown.
The format is in docs/data-formats.md. This module only reads it. It reports the pre-agreed warning (amber) and the two conditions for raising the
macro light to red, plus slow-bear markers, but never changes the light itself. A missing or old file is reported as such.
"""
from __future__ import annotations

import json
import os
from datetime import date, datetime
from pathlib import Path

DEFAULT_PATH = Path.home() / "tools" / "weekly_watch" / "out" / "state.json"
STALE_DAYS = 5  # the job runs after each US close; five calendar days covers a long weekend


def _path():
    return Path(os.environ.get("SHEPHERD_WEEKLY_STATE", DEFAULT_PATH))


def load(path=None, today=None):
    path = Path(path) if path else _path()
    today = today or date.today()
    try:
        body = json.loads(path.read_text())
    except FileNotFoundError:
        return {"status": "missing", "path": str(path), "symbols": {}, "triggers": [],
                "notice": f"没有找到周线观察数据文件（{path}）。格式见 docs/data-formats.md。"}
    except (OSError, ValueError):
        return {"status": "invalid", "path": str(path), "symbols": {}, "triggers": [], "notice": "周线观察文件读取失败。"}
    try:
        day = datetime.strptime(body["data_day"], "%Y-%m-%d").date()
        symbols = dict(body["symbols"])
        triggers = list(body["triggers"])
    except (KeyError, TypeError, ValueError):
        return {"status": "invalid", "path": str(path), "symbols": {}, "triggers": [], "notice": "周线观察文件缺字段。"}
    age = (today - day).days
    summary = dict(body, status="ok", path=str(path), age_days=age, symbols=symbols, triggers=triggers)
    if age > STALE_DAYS:
        summary.update(status="stale", notice=f"周线观察停在 {body['data_day']}（{age} 天前），以下是旧数。")
    return summary


def attention(watch):
    """Overview lines: a red-light condition met, a weekly EMA tier touched for the first time in this pullback, a stale file."""
    if not watch or watch.get("status") == "missing":
        return []
    if watch.get("status") != "ok":
        return [("amber", "周线观察：" + (watch.get("notice") or "不可用"), "")]
    out = []
    for t in watch.get("triggers") or []:
        if t.get("status") == "on" and t.get("severity") == "amber":
            out.append(("amber", "周线预警：" + str(t.get("label")), str(t.get("detail") or "")))
        elif t.get("status") == "on":
            out.append(("red", "升红条件满足：" + str(t.get("label")), str(t.get("detail") or "") + " · 按约定提请把宏观灯升红"))
        elif t.get("status") == "unknown":
            out.append(("amber", "周线条件读不到：" + str(t.get("label")), str(t.get("detail") or "")))
        elif t.get("armed"):
            a = t["armed"]
            if a.get("title"):  # files may phrase the watch line themselves
                out.append(("info", str(a["title"]), str(a.get("detail") or "")))
                continue
            out.append(("info", f"重点观察：VUG 周收盘低于 {a.get('price'):g} 就出预警",
                        f"RSP 和 CAPE 已符合 · 还要再跌 {-a.get('dist_pct'):g}%（约 {-a.get('dist_atr'):g} 个周 ATR）"
                        f" · 同一周 RSP 要收在 {a.get('rsp_floor'):g} 以上"))
    for sym, s in (watch.get("symbols") or {}).items():
        for tier in s.get("tiers") or []:
            if tier.get("first_touch"):
                out.append(("info", f"{sym} 本周第一次碰到 {tier.get('span')} 周 EMA {tier.get('ema'):g}",
                            f"这段回调里第一次 · 历史上上涨回调碰到这一档的比例 {tier.get('hist_reach')}%"))
    return out
