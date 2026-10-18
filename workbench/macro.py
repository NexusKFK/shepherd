"""Macro panel light (~/tools/macro_panel/out/state.json) -> heat cap and bucket-cap scale.

The panel only sizes *new* risk: one light, a heat cap, a bucket-cap multiplier. It never
blocks a plan and never tells existing positions to sell (stops own those). A missing or
stale file leaves the static caps in place and says so; it is never read as green.
"""
from __future__ import annotations

import json
import os
from datetime import date, datetime
from pathlib import Path

DEFAULT_PATH = Path.home() / "tools" / "macro_panel" / "out" / "state.json"
STALE_DAYS = 10  # light confirms weekly (trends refresh each weekday); >10 days means the job stopped


def _path():
    return Path(os.environ.get("SHEPHERD_MACRO_STATE", DEFAULT_PATH))


def load(path=None, today=None):
    """Return the panel summary, or a dict with status 'missing'/'stale'/'invalid' and no knobs."""
    path = Path(path) if path else _path()
    today = today or date.today()
    try:
        body = json.loads(path.read_text())
    except FileNotFoundError:
        return {"status": "missing", "path": str(path), "notice": f"没有找到宏观面板文件（{path}），桶上限与热度上限按静态值。"}
    except (OSError, ValueError):
        return {"status": "invalid", "path": str(path), "notice": "宏观面板文件读取失败，桶上限与热度上限按静态值。"}
    knobs = body.get("knobs") or {}
    try:
        day = datetime.strptime(body["data_day"], "%Y-%m-%d").date()
        heat, scale = float(knobs["heat_cap_r"]), float(knobs["bucket_scale"])
    except (KeyError, TypeError, ValueError):
        return {"status": "invalid", "path": str(path), "notice": "宏观面板文件缺字段，桶上限与热度上限按静态值。"}
    age = (today - day).days
    summary = {"status": "ok", "path": str(path), "light": body.get("light"), "stage": body.get("stage"),
               "raw_today": body.get("raw_today"), "data_day": body["data_day"], "age_days": age,
               "fuel_high": body.get("fuel_high"), "vix_cheap": body.get("vix_cheap"),
               "heat_cap_r": heat, "bucket_scale": scale, "unconfirmed": knobs.get("unconfirmed"),
               "insurance": knobs.get("insurance"), "families": body.get("families") or [],
               "history": body.get("history") or [], "disarm": body.get("disarm") or {},
               "notes": body.get("notes") or [], "trends": body.get("trends") or [],
               "trend_rule": body.get("trend_rule") or {}, "trend_evidence": body.get("trend_evidence"),
               "breadth": body.get("breadth") or {}}
    if age > STALE_DAYS:
        summary.update(status="stale", notice=f"宏观面板数据停在 {body['data_day']}（{age} 天前），未采用；桶上限与热度上限按静态值。")
    return summary


def scaled_buckets(buckets, macro):
    """Bucket caps times the light's scale; the static cap is kept as cap_base_r."""
    if not macro or macro.get("status") != "ok":
        return buckets
    scale = macro["bucket_scale"]
    return {name: dict(cfg, cap_base_r=cfg.get("cap_r"),
                       cap_r=round(cfg["cap_r"] * scale, 2) if cfg.get("cap_r") else cfg.get("cap_r"))
            for name, cfg in buckets.items()}


def heat_cap(default, macro):
    return macro["heat_cap_r"] if macro and macro.get("status") == "ok" else default
