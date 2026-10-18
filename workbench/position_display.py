"""Separate explicitly declared source summaries from their component positions.

This never merges similar trades or alters the source/ledger. Older collectors
put component IDs in delta_missing_reason; only an explicit combo marker permits
that compatibility path. Unresolved summaries remain visible and unmeasured.
"""
from __future__ import annotations

import re


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


def display_positions(positions):
    """Retain all rows and annotate parent/child links for the native UI."""
    active, summaries = partition_positions(positions)
    annotated = {str(p.get("id")): p for p in active + summaries}
    return [annotated[str(p.get("id"))] for p in positions]
