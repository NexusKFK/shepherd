"""Read-only execution accounting against a plan's frozen risk budget.

Small partial fills and reduced balances are facts, never requests to top up.
The original budget survives subsequent global-policy changes and reductions.
"""
from __future__ import annotations

import math


def _positive(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0


def _unit_risk(plan, price):
    if plan["kind"] == "stock":
        stop = plan.get("stop")
        if not _positive(stop):
            return None
        distance = (price - stop) * (1 if plan["side"] == "long" else -1)
        # An execution through the original invalidation level has no valid
        # forward stop-distance estimate. It is not a zero-risk entry.
        return distance if distance > 0 else None
    if plan["side"] == "long":
        return price * plan["mult"]
    maximum = plan.get("max_loss")
    return maximum if _positive(maximum) else None


def execution_discipline(plan):
    sizing = plan.get("sizing") or {}
    budget = sizing.get("budget")
    budget = budget if _positive(budget) else None
    target = sizing.get("planned_qty", sizing.get("qty"))
    fills = [f for f in plan.get("fills", []) if not f["voided"]]
    entries = [f for f in fills if f["action"] == "entry"]
    exits = [f for f in fills if f["action"] == "exit"]
    entry_qty = sum(f["qty"] for f in entries)
    opening = any(f.get("opening_balance") for f in entries)
    risk_known = all(_unit_risk(plan, f["price"]) is not None for f in entries)
    entry_risk = sum(_unit_risk(plan, f["price"]) * f["qty"] + f["fee"]
                     for f in entries) if risk_known else None
    unit = _unit_risk(plan, plan["avg_entry"]) if plan.get("avg_entry") is not None else None
    current_risk = unit * plan["open_qty"] if unit is not None else (0 if not plan["open_qty"] else None)
    planned_r = sizing.get("planned_risk", 0) / budget if budget else None
    actual_r = entry_risk / budget if budget and entry_risk is not None else None
    current_r = current_risk / budget if budget and current_risk is not None else None
    warnings = []
    state = plan["status"]
    if opening and not budget:
        state = "opening_balance"
        warnings.append("期初持仓没有事前冻结的 1R，保留真实数量；不能据此判定开仓纪律")
    elif entries and not budget:
        state = "unplanned_execution"
        warnings.append("这笔已成事实的交易没有事前冻结的 1R，风险未知；保留成交并补充复盘")
    elif entries:
        if plan["status"] == "closed":
            state = "closed"
        elif exits:
            state = "reduced"
        elif target and entry_qty < target - 1e-8:
            state = "partial_entry"
        else:
            state = "holding"
        # Entry risk is cumulative, so exits, rolls or another tranche cannot
        # silently reset historical execution deviations back to zero.
        if actual_r is None:
            state = "unknown_risk"
            warnings.append("实际入场风险未知（原失效价可能已被穿越），无法判断是否超过原始 1R")
        elif entry_risk > budget + .01:
            warnings.append(f"累计入场风险（含入场费用）为 {actual_r:.2f}R，超过原始 1R；实际成交已如实保留")
            state = "over_budget"
        if target is not None and entry_qty > target + 1e-8:
            warnings.append("累计入场数量超过原完整开仓计划；分批或减仓不会重置原计划")
        if current_r is not None and current_r > 1 + 1e-8:
            warnings.append(f"剩余持仓按原失效条件估算风险为 {current_r:.2f}R，超过原始 1R")
    labels = {"draft": "观察草稿", "ready": "已冻结标准 1R", "partial_entry": "部分成交／分批入场",
              "holding": "已按计划建立持仓", "reduced": "已减仓的剩余持仓", "closed": "已平仓",
              "cancelled": "已取消", "over_budget": "实际执行超过原预算",
              "unknown_risk": "已成交，原风险依据需要复核",
              "unplanned_execution": "已成交，缺少事前风险基准", "opening_balance": "已核对期初持仓"}
    legacy = bool(sizing and sizing.get("schema") != "fixed-r/1")
    label = "沿用旧计划风险基准" if legacy and state == "ready" else labels.get(state, state)
    return {"state": state, "label": label, "baseline_risk": budget,
            "planned_r": planned_r, "actual_entry_r": actual_r, "current_r": current_r,
            "execution_deviation_r": actual_r - planned_r if actual_r is not None and planned_r is not None else None,
            "realized_r": plan["realized_pnl"] / budget if budget else None,
            "entry_qty": entry_qty, "target_qty": target,
            "remaining_planned_qty": max(0, target - entry_qty) if target is not None else None,
            "legacy": legacy, "warnings": warnings}
