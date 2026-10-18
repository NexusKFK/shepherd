"""Read-only enrichment of positions; annotations never rewrite trade facts."""
from __future__ import annotations

import json
import re
from datetime import datetime

from .econf import parse_option_description
from .sources import finite


def enrich_positions(positions, snapshot, annotations):
    result = []
    for original in positions:
        p = dict(original)
        # Single-contract descriptions are evidence for contract identity only.
        description = f"{p.get('sym', '')} {p.get('contract') or p.get('label', '')}"
        description = re.sub(r"(\d)([CP])\b", r"\1 \2", description)
        contract = parse_option_description(description)
        if contract:
            p.setdefault("strike", contract.get("strike"))
            p.setdefault("call_put", contract.get("call_put"))
        quote = snapshot.get("quotes", {}).get(p.get("sym"))
        if not finite(p.get("underlying_price")) and finite(quote) and quote > 0:
            p["underlying_price"] = quote
            p["price_asof"] = snapshot.get("day")
            p["underlying_price_source"] = "原持仓快照标的行情"
        # Bound snapshot positions keep their annotation when adopted into the ledger.
        note = annotations.get(p["id"], annotations.get(p.get("snapshot_id"), {}))
        for key in ("delta", "underlying_price", "sector", "theme", "factor", "operation_id", "executed_at", "account_id"):
            if note.get(key) not in (None, ""):
                p[key] = note[key]
        if note.get("delta") is not None:
            p["delta_asof"] = note.get("asof")
            p["delta_source"] = note.get("source")
        if note.get("underlying_price") is not None:
            p["price_asof"] = note.get("asof")
        if note.get("operation_id"):
            p["operation_source"] = "人工核对批次：" + note.get("source", "")
        if note:
            p["risk_input"] = note
        result.append(p)
    return result


def operation_positions(positions, plans, receipt_states):
    """Attribute surviving entry lots to confirmed receipt operations using FIFO.

    A common calendar day, source label, or import time is not execution evidence.
    Unknown opening balances remain unknown. Actual cashflows are left untouched.
    """
    evidence = {}
    for state in receipt_states:
        proposal = state.get("proposal", {})
        clock = proposal.get("executed_clock")
        proposal_day = proposal.get("trade_day")
        for rec in state.get("recorded", []):
            day = rec.get("day") or proposal_day
            index = rec.get("leg")
            legs = proposal.get("legs", [])
            if type(index) is not int or not 0 <= index < len(legs):
                continue
            meta = {"broker_account": proposal.get("account", "")}
            option = legs[index].get("option") or parse_option_description(legs[index].get("description", ""))
            if option:
                meta.update(strike=option.get("strike"), call_put=option.get("call_put"), expiry=option.get("expiry"))
            # A receipt's explicit common clock is minute-precision evidence;
            # don't merge separate receipts just because they share that minute.
            if day and day == proposal_day and clock and "未注明" not in clock:
                rid = rec.get("request_id", "")
                receipt_id = rid.rsplit("-", 1)[0]
                meta.update(operation_id=receipt_id + ":" + day + ":" + clock,
                            execution_day=day,
                            operation_source=f"已核对同一回单 · {day} {clock}（分钟精度）")
            evidence[rec.get("request_id")] = meta
    plan_map = {p["id"]: p for p in plans}
    output = []
    for p in positions:
        plan = plan_map.get(p.get("plan_id"))
        if not plan:
            output.append(p)
            continue
        lots = []
        entries = [f for f in plan.get("fills", []) if not f.get("voided") and f.get("action") == "entry"]
        base = dict(p)
        if len(entries) != 1:
            for key in ("operation_id", "executed_at", "execution_timestamp", "operation_source"):
                base.pop(key, None)
        for f in plan.get("fills", []):
            if f.get("voided"):
                continue
            if f.get("action") == "entry":
                meta = dict(evidence.get(f.get("request_id"), {}))
                if f.get("day") and meta.get("execution_day") and f["day"] != meta["execution_day"]:
                    for key in ("operation_id", "executed_at", "operation_source"):
                        meta.pop(key, None)
                lots.append(dict(base, **meta, qty=f["qty"], id=p["id"] + ":" + f["id"], position_id=p["id"]))
            else:
                remaining = f["qty"]
                for lot in lots:
                    used = min(lot["qty"], remaining)
                    lot["qty"] -= used
                    remaining -= used
        output.extend(lot for lot in lots if lot["qty"] > 1e-8)
    return output


SUPPLEMENT_BENCHMARKS = ("SPX", "^GSPC", "$SPX", "SP:SPX", "SPY")


def supplemental_history(history, path):
    """Optional provider-neutral local daily bars, aligned to the primary cutoff.

    Design choice: when the supplement carries a benchmark close newer than the
    primary cutoff, the risk cutoff advances to that day and every series comes from the
    supplement alone — primary bars are never mixed with a later cutoff.
    """
    if not path.exists():
        return history
    result = dict(history, series=dict(history.get("series", {})), errors=list(history.get("errors", [])))
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if raw.get("schema") != "shepherd-market-data/1" or raw.get("price_basis") != "regular_split_adjusted":
            raise ValueError("行情口径不匹配")
        if not isinstance(raw.get("series"), dict) or not raw.get("source"):
            raise ValueError("缺少来源或日线")
        parsed = {}
        for symbol, bars in raw["series"].items():
            clean, last = [], ""
            for bar in bars:
                day = bar["day"]
                datetime.strptime(day, "%Y-%m-%d")
                if day <= last or not finite(bar["close"]) or bar["close"] <= 0:
                    raise ValueError("日线日期或价格不正确")
                last = day
                clean.append({"day": day, "close": bar["close"]})
            parsed[symbol] = clean
        primary_day = history.get("day")
        bench_day = next((parsed[k][-1]["day"] for k in SUPPLEMENT_BENCHMARKS if parsed.get(k)), None)
        if bench_day and (not primary_day or bench_day > primary_day):
            result["series"] = {s: b for s, b in parsed.items() if b and b[-1]["day"] == bench_day}
            result["day"] = bench_day
            result["day_source"] = "supplement"
            result["primary_day"] = primary_day
        else:
            for symbol, bars in parsed.items():
                if symbol in result["series"]:
                    continue
                clean = [b for b in bars if primary_day and b["day"] <= primary_day]
                if clean and clean[-1]["day"] == primary_day:
                    result["series"][symbol] = clean
        result["supplement_source"] = raw["source"]
        result["supplement_collected_at"] = raw.get("collected_at")
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        # No partial acceptance from a malformed supplement.
        result = dict(history, errors=list(history.get("errors", [])))
        result["errors"].append("补充行情 market-data.json 无效；未将其计入风险")
    return result
