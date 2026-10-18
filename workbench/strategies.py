"""Conservative option-shape recognition, separate from execution evidence.

Same-day fills and legacy group labels never prove a simultaneous operation.
Current structures are descriptive only: no position merging or ledger writes.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime, timezone

from .sources import finite


def _strategy(code="unknown", label="未知 / 自定义结构", notes=None):
    return {"code": code, "label": label, "confidence": "unknown" if code == "unknown" else "structure",
            "notes": notes or []}


def _leg(p):
    right = str(p.get("right") or p.get("call_put") or p.get("option_type") or p.get("put_call") or "").upper()
    right = {"CALL": "C", "PUT": "P"}.get(right, right)
    strike = p.get("strike")
    # Parse only an unambiguous single-contract suffix. An old basket label such
    # as '410/460/510 蝶' is not a leg and cannot establish its missing components.
    match = re.search(r"(?<![\d./])(?P<strike>\d+(?:\.\d+)?)\s*(?P<right>[CP])\s*$", str(p.get("label", "")), re.I)
    if match and "/" not in str(p.get("label", "")):
        if not finite(strike):
            strike = float(match.group("strike"))
        if right not in ("C", "P"):
            right = match.group("right").upper()
    qty = p.get("qty")
    expiry = p.get("expiry")
    try:
        date.fromisoformat(expiry)
    except (TypeError, ValueError):
        expiry = None
    return {"id": str(p.get("id", "")), "side": p.get("side"), "qty": qty,
            "right": right if right in ("C", "P") else None,
            "strike": strike if finite(strike) and strike > 0 else None,
            "expiry": expiry, "label": p.get("label", ""), "mult": p.get("mult", 100),
            "confirm": bool(p.get("confirm"))}


def _combined(legs):
    merged = {}
    for leg in legs:
        if (not leg["right"] or leg["strike"] is None or not leg["expiry"]
                or leg["side"] not in ("long", "short") or not finite(leg["qty"])
                or leg["qty"] <= 0 or not finite(leg["mult"]) or leg["mult"] <= 0):
            return None
        key = (leg["expiry"], leg["right"], leg["strike"], leg["mult"])
        merged[key] = merged.get(key, 0) + leg["qty"] * (1 if leg["side"] == "long" else -1)
    return [{"expiry": e, "right": r, "strike": k, "mult": m, "signed_qty": q}
            for (e, r, k, m), q in merged.items() if abs(q) > 1e-9]


def _same(a, b):
    return abs(a - b) <= 1e-8 * max(1, abs(a), abs(b))


def identify_structure(legs):
    """Identify payoff geometry, not entry credit, profitability or max loss."""
    combined = _combined(legs)
    if combined is None:
        return _strategy(notes=["缺少完整的 Call/Put、行权价、到期日或数量；来源标签不代替腿明细。"])
    if not combined:
        return _strategy("flat", "合约净数量为零")
    if len({x["mult"] for x in combined}) > 1:
        return _strategy(notes=["合约乘数不同，不能按标准策略数量比例认证。"])
    legs = sorted(combined, key=lambda x: (x["strike"], x["expiry"], x["right"]))
    n = len(legs)
    same_expiry = len({x["expiry"] for x in legs}) == 1
    same_right = len({x["right"] for x in legs}) == 1
    q = [x["signed_qty"] for x in legs]
    if n == 1:
        return _strategy("single", ("买入" if q[0] > 0 else "卖出") + (" Call" if legs[0]["right"] == "C" else " Put"))
    if n == 2:
        a, b = legs
        equal_qty = _same(abs(q[0]), abs(q[1]))
        opposite = q[0] * q[1] < 0
        same_strike = _same(a["strike"], b["strike"])
        if same_right and opposite and equal_qty:
            if same_expiry and not same_strike:
                return _strategy("vertical", "垂直价差（Vertical）")
            if not same_expiry and same_strike:
                return _strategy("calendar", "日历价差（Calendar）")
            if not same_expiry and not same_strike:
                return _strategy("diagonal", "对角价差（Diagonal）")
        if same_expiry and not same_right and not opposite and equal_qty:
            return _strategy("straddle" if same_strike else "strangle",
                             ("买入" if q[0] > 0 else "卖出") + ("跨式（Straddle）" if same_strike else "宽跨式（Strangle）"))
        if same_expiry and same_right and opposite and not same_strike:
            ratio = max(abs(x) for x in q) / min(abs(x) for x in q)
            if _same(ratio, 2):
                long_qty, short_qty = sum(x for x in q if x > 0), -sum(x for x in q if x < 0)
                label = "2:1 买方比例价差（Backspread）" if long_qty > short_qty else "1:2 卖方比例价差（Ratio spread）"
                return _strategy("ratio_2_1", label, ["仅确认 2:1 数量形态；缺少入场内外在价值证据，不能认证 ZEBRA。"])
            return _strategy("ratio", "不等量比例价差（Ratio spread）")
    if n == 3 and same_expiry and same_right:
        if q[0] * q[1] < 0 and q[0] * q[2] > 0 and _same(q[0], q[2]) and _same(abs(q[1]), 2 * abs(q[0])):
            equal_wings = _same(legs[1]["strike"] - legs[0]["strike"], legs[2]["strike"] - legs[1]["strike"])
            return _strategy("butterfly" if equal_wings else "broken_wing_butterfly",
                             ("买入" if q[0] > 0 else "卖出") + ("蝶式（Butterfly）" if equal_wings else "不等翼蝶式（Broken-wing butterfly）"))
    if n == 4 and same_expiry and all(_same(abs(x), abs(q[0])) for x in q):
        puts = sorted((x for x in legs if x["right"] == "P"), key=lambda x: x["strike"])
        calls = sorted((x for x in legs if x["right"] == "C"), key=lambda x: x["strike"])
        if len(puts) == len(calls) == 2:
            outer = puts[0]["signed_qty"]
            if (puts[0]["strike"] < puts[1]["strike"] <= calls[0]["strike"] < calls[1]["strike"]
                    and _same(puts[1]["signed_qty"], -outer) and _same(calls[0]["signed_qty"], -outer)
                    and _same(calls[1]["signed_qty"], outer)):
                butterfly = _same(puts[1]["strike"], calls[0]["strike"])
                return _strategy("iron_butterfly" if butterfly else "iron_condor",
                                 ("正向" if outer > 0 else "反向") + ("铁蝶（Iron butterfly）" if butterfly else "铁鹰（Iron condor）"))
        if same_right and q[0] * q[1] < 0 and _same(q[1], q[2]) and _same(q[0], q[3]):
            return _strategy("condor", "秃鹰价差（Condor）")
    return _strategy(notes=["当前腿组合不符合已支持的标准形态，保留为自定义结构。"])


def _timestamp(p):
    value = p.get("executed_at") or p.get("execution_timestamp")
    # A date, minute-level clock, imported-at value or generic asof is not an
    # exact execution timestamp. Explicit operation IDs are handled separately.
    if not isinstance(value, str) or not re.search(r"[T ]\d{2}:\d{2}:\d{2}", value):
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            return None
        return dt.astimezone(timezone.utc).isoformat()
    except ValueError:
        return None


def _identity(p):
    return (str(p.get("broker") or ""), str(p.get("account_id") or p.get("account") or p.get("broker_account") or ""),
            str(p.get("underlying") or p.get("sym") or p.get("symbol") or "?").upper())


def _id(key):
    return hashlib.sha256(json.dumps(key, ensure_ascii=False).encode()).hexdigest()[:16]


def recognize_operations(positions):
    groups, current, ungrouped = {}, {}, []
    for index, p in enumerate(positions):
        if p.get("kind") != "option":
            continue
        p = dict(p, id=str(p.get("id", f"option-{index}")))
        broker, account, symbol = _identity(p)
        legacy = str(p.get("group") or "")
        current.setdefault((broker, account, symbol, legacy), []).append(p)
        operation_id = str(p.get("operation_id") or "").strip()
        timestamp = _timestamp(p)
        if not broker or not account or symbol == "?":
            reason = "券商、账户或标的不完整，不能证明属于同一操作"
        elif operation_id:
            groups.setdefault((broker, account, symbol, "operation_id", operation_id), []).append(p)
            continue
        elif timestamp:
            groups.setdefault((broker, account, symbol, "execution_timestamp", timestamp), []).append(p)
            continue
        else:
            reason = "没有明确操作 ID 或带时区的精确成交时间；同日/来源分组不能证明同时建仓"
        ungrouped.append({"id": p["id"], "position_id": p.get("position_id", p["id"]), "symbol": symbol, "broker": broker, "label": p.get("label", ""),
                          "reason": reason, "source_label": legacy})
    output = []
    for key, members in groups.items():
        broker, account, symbol, evidence, evidence_value = key
        legs = [_leg(p) for p in members]
        output.append({"id": _id(key), "symbol": symbol, "broker": broker, "account": account,
                       "evidence": evidence, "operation_id": evidence_value if evidence == "operation_id" else None,
                       "executed_at": evidence_value if evidence == "execution_timestamp" else members[0].get("executed_at"),
                       "strategy": identify_structure(legs), "legs": legs,
                       "source_labels": sorted({str(p["group"]) for p in members if p.get("group")}),
                       "notes": [str(p["operation_source"]) for p in members if p.get("operation_source")]
                                + (["至少一腿持仓待核对；形态识别不确认数量正确。"] if any(p.get("confirm") for p in members) else [])})
    structures = []
    for key, members in current.items():
        broker, account, symbol, legacy = key
        ids = {p["id"] for p in members}
        operations = [g for g in output if any(leg["id"] in ids for leg in g["legs"])]
        notes = ["仅描述当前持仓形态；不代表同一时间建仓，不合并原始交易或重置风险预算。"]
        if not account:
            notes.append("账户标识缺失，当前结构可能跨账户，需核对。")
        if legacy:
            notes.append("来源分组标签：" + legacy + "；标签本身不是成交证据。")
        if any(p.get("confirm") for p in members):
            notes.append("至少一腿持仓待核对；识别结果只供复核。")
        structures.append({"id": _id(key), "symbol": symbol, "broker": broker, "account": account,
                           "source_label": legacy, "leg_ids": [p["id"] for p in members],
                           "legs": [_leg(p) for p in members], "operation_count": len(operations),
                           "strategy": identify_structure([_leg(p) for p in members]), "notes": notes})
    return {"groups": output, "ungrouped": ungrouped, "current_structures": structures,
            "notices": ["策略识别只描述合约形态；没有波动或一段时间没有价格变化，不构成违规。",
                        "同一操作需同券商、同账户、同标的，并有明确操作 ID 或精确成交时间；不同时间保留为不同批次。",
                        "当前结构可能由多次操作形成；来源分组、同一天成交与相近时间不证明同时建仓。"]}
