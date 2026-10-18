"""固定持仓的一日历史模拟 VaR。未覆盖期权不得显示全组合 VaR。"""
from __future__ import annotations

import math
from datetime import date, datetime

from .sources import ET, finite


def quantile(values, q):
    """线性插值经验分位数（type 7），损失取正数。"""
    v = sorted(values)
    n = (len(v) - 1) * q
    a, b = math.floor(n), math.ceil(n)
    return v[a] + (v[b] - v[a]) * (n - a)


def calculate(positions, history, nlv, snapshot_day=None):
    series = history["series"]
    covered, excluded, returns = [], [], {}
    for p in positions:
        why = None
        if p.get("kind") != "stock":
            why = "期权缺少可重估的波动率与合约行情"
        elif p.get("confirm"):
            why = "持仓数量或状态待核对"
        elif not finite(p.get("qty")) or p["qty"] <= 0:
            why = "持仓数量不完整"
        elif p.get("side") not in ("long", "short"):
            why = "持仓方向不完整"
        elif len(series.get(p.get("sym"), [])) < 253:
            why = "不足 253 根同口径日线"
        if why:
            excluded.append({"id": p["id"], "symbol": p.get("sym", "?"), "reason": why})
            continue
        bars = series[p["sym"]]
        # 用日期对而非仅结束日期对齐，避免把停牌多日收益与单日收益混用。
        r = {(a["day"], b["day"]): b["close"] / a["close"] - 1 for a, b in zip(bars, bars[1:])}
        returns[p["id"]] = r
        exposure = p["qty"] * bars[-1]["close"] * (1 if p["side"] == "long" else -1)
        covered.append({"id": p["id"], "symbol": p["sym"], "broker": p.get("broker", ""), "exposure": exposure, "qty": p["qty"], "price": bars[-1]["close"]})
    result = {"confidence": 0.95, "horizon": "1 个交易日", "method": "历史模拟 · 最近 252 个共同交易日 · 固定当前数量",
              "price_basis": history.get("basis", ""), "market_day": history.get("day"), "snapshot_day": snapshot_day,
              "covered": covered, "excluded": excluded, "samples": 0, "var95": None, "var99": None,
              "es95": None, "var_pct": None, "portfolio_var95": None, "losses": [], "status": "unavailable", "errors": list(history.get("errors", []))}
    if not covered:
        return result
    keys = set.intersection(*(set(returns[p["id"]]) for p in covered))
    days = sorted(keys)[-252:]
    result["samples"] = len(days)
    if len(days) < 252:
        result["errors"].append("共同的一日收益样本不足 252 个，暂不计算")
        return result
    losses = [-sum(p["exposure"] * returns[p["id"]][d] for p in covered) for d in days]
    var95 = max(0, quantile(losses, .95))
    tail = [v for v in losses if v >= quantile(losses, .95)]
    var99 = max(0, quantile(losses, .99))
    result.update(var95=round(var95, 2), var99=round(var99, 2), es95=round(max(0, sum(tail)/len(tail)), 2),
                  var_pct=round(var95/nlv*100, 3) if finite(nlv) and nlv > 0 else None,
                  status="partial" if excluded else "available",
                  portfolio_var95=round(var95, 2) if not excluded else None,
                  losses=[{"day": d[1], "loss": round(v, 2)} for d, v in zip(days, losses)])
    result["stale"] = not history.get("day") or (datetime.now(ET).date()-date.fromisoformat(history["day"])).days > 4
    if result["stale"]:
        result["status"] = "stale"
    result["exposures"] = sorted(covered, key=lambda p: abs(p["exposure"]), reverse=True)
    # 市场参考独立于账户组合，只展示 SPY 的一日损失分位数。
    spy = series.get("SPY", [])
    if len(spy) >= 253:
        r = [-(b["close"]/a["close"]-1)*100 for a,b in zip(spy[-253:], spy[-252:])]
        result["market_var95_pct"] = round(max(0, quantile(r, .95)), 2)
    return result
