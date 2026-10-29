"""Separate explicitly declared source summaries from their component positions.

This never merges similar trades or alters the source/ledger. Older collectors
put component IDs in delta_missing_reason; only an explicit combo marker permits
that compatibility path. Unresolved summaries remain visible and unmeasured.

The one derived case: option legs the owner put under the same named `group` in the
ledger, same broker, account, underlying and expiry, with at least one long and one
short leg, get a display-only summary row. IBKR reports each leg of a spread on its own,
so without this a spread adopted from the gateway shows as loose legs.
"""
from __future__ import annotations

import hashlib
import math
import re
from functools import reduce


def partition_positions(positions):
    positions = list(positions or [])
    by_id = {str(p.get("id")): p for p in positions}
    active, summaries = [], []
    for original in positions:
        p = dict(original)
        if p.get("delta_status") != "combo":
            active.append(p)
            continue
        declared = p.get("component_ids")
        if isinstance(declared, list) and declared and all(isinstance(x, str) for x in declared):
            ids = list(dict.fromkeys(declared))
        else:
            reason = str(p.get("delta_missing_reason") or "")
            match = re.search(r"见各腿\s+([A-Za-z0-9_-]+(?:\s*[/,、]\s*[A-Za-z0-9_-]+)+)", reason)
            ids = re.split(r"\s*[/,、]\s*", match.group(1)) if match else []
        def same_identity(leg):
            return (leg.get("delta_status") != "combo" and leg.get("kind") == "option"
                    and all(str(leg.get(k) or "") == str(p.get(k) or "")
                            for k in ("broker", "sym", "expiry"))
                    and str(leg.get("account_id") or leg.get("account") or leg.get("broker_account") or "")
                    == str(p.get("account_id") or p.get("account") or p.get("broker_account") or ""))
        complete = len(ids) >= 2 and all(key in by_id and same_identity(by_id[key]) for key in ids)
        p.update(display_only=complete, component_ids=ids if complete else [],
                 summary_note="组合汇总，仅展示；风险按合约腿计量" if complete
                 else "组合汇总的合约腿关联不完整，需核对；未计入风险")
        summaries.append(p)
        if not complete:
            p.update(confirm=True, reconcile_note=p["summary_note"])
            active.append(p)
    return active, summaries


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _spread_summary(legs):
    """Display-only summary for one named group of option legs, or None when the legs do not fit."""
    qtys = []
    for leg in legs:
        q = leg.get("qty")
        if not _num(q) or q <= 0 or q != int(q) or leg.get("side") not in ("long", "short") or not _num(leg.get("open")):
            return None
        qtys.append(int(q))
    if len({leg["side"] for leg in legs}) < 2:
        return None
    unit = reduce(math.gcd, qtys)
    sign = lambda leg: 1 if leg["side"] == "long" else -1
    net_open = sum(sign(l) * l["open"] * (q // unit) for l, q in zip(legs, qtys))
    flip = 1 if net_open >= 0 else -1
    marks = [l.get("mark") for l in legs]
    net_mark = (flip * sum(sign(l) * l["mark"] * (q // unit) for l, q in zip(legs, qtys))
                if all(_num(m) for m in marks) else None)
    ids = [str(l.get("id")) for l in legs]
    first = legs[0]
    return {"id": "spread:" + hashlib.sha1("|".join(sorted(ids)).encode()).hexdigest()[:10],
            "broker": first.get("broker"), "sym": first.get("sym"), "expiry": first.get("expiry"),
            "account_id": first.get("account_id"), "account": first.get("account"), "broker_account": first.get("broker_account"),
            "label": first.get("group"), "group": "", "role": "", "kind": "option",
            "side": "long" if flip > 0 else "short", "qty": unit, "mult": first.get("mult") or 100,
            "open": round(abs(net_open), 4), "mark": None if net_mark is None else round(net_mark, 4),
            "mark_source": "由各腿现价合成", "tail": None, "tailNote": "", "confirm": False, "trigs": [],
            "delta_status": "combo", "component_ids": ids, "source": "derived"}


def with_spread_summaries(positions):
    """Append a summary row for each named option group that has no explicit summary yet."""
    positions = list(positions or [])
    _, summaries = partition_positions(positions)
    covered = {i for s in summaries for i in s.get("component_ids", [])}
    groups = {}
    for p in positions:
        if (p.get("kind") != "option" or p.get("delta_status") == "combo" or not p.get("group")
                or str(p.get("id")) in covered):
            continue
        key = tuple(str(p.get(k) or "") for k in ("broker", "account_id", "account", "broker_account", "sym", "expiry", "group"))
        groups.setdefault(key, []).append(p)
    derived = [s for legs in groups.values() if len(legs) >= 2 and (s := _spread_summary(legs))]
    return positions + derived


def display_positions(positions):
    """Retain all rows and annotate parent/child links for the native UI."""
    positions = with_spread_summaries(positions)
    active, summaries = partition_positions(positions)
    annotated = {str(p.get("id")): p for p in active + summaries}
    return [annotated[str(p.get("id"))] for p in positions]
