"""Paper book (~/tools/paper_book/out/state.json): observation-only simulated trades.

The book and the daily marks are owned by ~/tools/paper_book (mark.py runs after the US close).
This module only reads the marked state. Paper trades never enter heat, exposure, VaR or the
real ledger. A missing or old file is reported as such, never shown as a flat book.
"""
from __future__ import annotations

import json
import os
from datetime import date, datetime
from pathlib import Path

DEFAULT_PATH = Path.home() / "tools" / "paper_book" / "out" / "state.json"
STALE_DAYS = 5  # marks run every weekday; five calendar days covers a long weekend


def _path():
    return Path(os.environ.get("SHEPHERD_PAPER_STATE", DEFAULT_PATH))


def load(path=None, today=None):
    path = Path(path) if path else _path()
    today = today or date.today()
    try:
        body = json.loads(path.read_text())
    except FileNotFoundError:
        return {"status": "missing", "path": str(path), "trades": [], "notice": f"没有找到纸盘估值文件（{path}）。格式见 docs/data-formats.md。"}
    except (OSError, ValueError):
        return {"status": "invalid", "path": str(path), "trades": [], "notice": "纸盘估值文件读取失败。"}
    try:
        day = datetime.strptime(body["data_day"], "%Y-%m-%d").date()
        trades = list(body["trades"])
    except (KeyError, TypeError, ValueError):
        return {"status": "invalid", "path": str(path), "trades": [], "notice": "纸盘估值文件缺字段。"}
    age = (today - day).days
    summary = dict(body, status="ok", path=str(path), age_days=age, trades=trades)
    if age > STALE_DAYS:
        summary.update(status="stale", notice=f"纸盘估值停在 {body['data_day']}（{age} 天前），以下是旧数。")
    return summary


def attention(paper):
    """Lines for the overview: an exit the marker just executed, a stale book, a review date that has come."""
    if not paper or paper.get("status") == "missing":
        return []
    if paper.get("status") != "ok":
        return [("amber", "纸盘数据：" + (paper.get("notice") or "不可用"), "")]
    out = []
    for item in paper.get("closed_this_run") or []:
        name = next((t.get("name") for t in paper["trades"] if t.get("id") == item.get("id")), item.get("id"))
        out.append(("amber", f"纸盘 {name} 已触发离场", f"{item.get('day')} · {item.get('why')}"))
    data_day = paper.get("data_day") or ""
    for t in paper["trades"]:
        if t.get("status") == "open" and t.get("review_on") and t["review_on"] <= data_day:
            out.append(("info", f"纸盘 {t.get('name')} 到复核日", f"复核日 {t['review_on']}"))
    return out
