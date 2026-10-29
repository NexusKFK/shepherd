"""已过到期日的期权计划自动结算。

期权到了到期日没有平仓成交，就是自然作废或按到期价结算：
用到期日标的收盘价算内在价值，登记一笔退出成交，计划转为已平仓。
  内在价值 = 看涨 max(收盘 − 行权价, 0) / 看跌 max(行权价 − 收盘, 0)
到期当天的日线收盘价取不到、合约里读不出行权价，就什么都不做，仍由持仓页标「待核对」。
这笔成交可以在复盘里作废（void_fill），所以自动结算是可撤销的。
"""
from __future__ import annotations

import re

from .store import Invalid

_CONTRACT = re.compile(r"(\d+(?:\.\d+)?)\s*(C(?:all)?|P(?:ut)?)\b", re.I)


def settle_expired(store, plans, history, today):
    """返回本次结算了的计划列表（每项一个字典）；没有需要结算的就返回空列表。"""
    series = (history or {}).get("series") or {}
    settled = []
    for p in plans:
        expiry = p.get("expiry")
        if p.get("status") != "holding" or p.get("kind") != "option" or not expiry or expiry >= today.isoformat():
            continue
        m = _CONTRACT.search(f"{p.get('contract') or ''} {p.get('title') or ''}")
        close = next((pt["close"] for pt in series.get(p.get("symbol"), []) if pt.get("day") == expiry), None)
        if not m or close is None or p.get("open_qty", 0) <= 0:
            continue
        strike, right = float(m.group(1)), m.group(2)[0].upper()
        intrinsic = max(close - strike, 0.0) if right == "C" else max(strike - close, 0.0)
        # 接入期初余额的成交日可能晚于到期日（到期后才核对），退出成交的日期不能早于它。
        day = max([expiry] + [f["day"] for f in p["fills"] if not f["voided"]])
        note = (f"{expiry} 到期自动结算：到期收盘 {close:g}，行权 {strike:g}，"
                f"{'作废' if intrinsic == 0 else '内在价值 %.2f' % intrinsic}；没有平仓成交，按到期收盘价算。")
        try:
            store.fill(p["id"], {"request_id": f"expiry-{p['id']}-{expiry}", "action": "exit", "qty": p["open_qty"],
                                 "price": round(intrinsic, 4), "fee": 0, "day": day, "note": note,
                                 "version": p["version"]})
        except Invalid:
            continue
        settled.append({"id": p["id"], "symbol": p["symbol"], "expiry": expiry, "price": round(intrinsic, 4), "note": note})
    return settled
