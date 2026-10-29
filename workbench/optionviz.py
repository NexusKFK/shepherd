"""Option payoff charts: plan cards (JSON files you write), plus the options already held.

Cards live as JSON files in ~/tools/option_plans/ (override with SHEPHERD_OPTION_PLANS). The app only
draws them: a card is a plan the user may or may not take, so nothing here enters heat, exposure, VaR or
the real ledger. Prices are Black-Scholes estimates from each leg's implied volatility (no skew, no early
exercise); a missing or unreadable card is reported, never drawn as an empty chart.
"""
from __future__ import annotations

import json
import math
import os
import re
from datetime import date, datetime, timedelta
from pathlib import Path

DEFAULT_DIR = Path.home() / "tools" / "option_plans"
RATE = 0.045
GRID_POINTS = 161
DEFAULT_HORIZONS = (3, 5)  # trading days held
IV_SHIFT = -0.05           # one extra curve: volatility falls 5 points once the selling calms down
STALE_DAYS = 3
MONTHS = {m: i + 1 for i, m in enumerate(["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"])}


def _dir():
    return Path(os.environ.get("SHEPHERD_OPTION_PLANS", DEFAULT_DIR))


# ---------------------------------------------------------------- pricing

def _ncdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _npdf(x):
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def bs_price(right, S, K, T, iv, r=RATE):
    """European Black-Scholes. T in years; at or past expiry the value is intrinsic."""
    if T <= 0 or iv <= 0 or S <= 0:
        return max(S - K, 0.0) if right == "C" else max(K - S, 0.0)
    d1 = (math.log(S / K) + (r + iv * iv / 2.0) * T) / (iv * math.sqrt(T))
    d2 = d1 - iv * math.sqrt(T)
    if right == "C":
        return S * _ncdf(d1) - K * math.exp(-r * T) * _ncdf(d2)
    return K * math.exp(-r * T) * _ncdf(-d2) - S * _ncdf(-d1)


def bs_greeks(right, S, K, T, iv, r=RATE):
    """Per share: delta, gamma, theta per calendar day, vega per volatility point."""
    if T <= 0 or iv <= 0 or S <= 0:
        itm = (S > K) if right == "C" else (S < K)
        return {"delta": (1.0 if right == "C" else -1.0) if itm else 0.0, "gamma": 0.0, "theta": 0.0, "vega": 0.0}
    sq = iv * math.sqrt(T)
    d1 = (math.log(S / K) + (r + iv * iv / 2.0) * T) / sq
    d2 = d1 - sq
    pdf = _npdf(d1)
    delta = _ncdf(d1) if right == "C" else _ncdf(d1) - 1.0
    gamma = pdf / (S * sq)
    decay = -S * pdf * iv / (2.0 * math.sqrt(T))
    if right == "C":
        theta = decay - r * K * math.exp(-r * T) * _ncdf(d2)
    else:
        theta = decay + r * K * math.exp(-r * T) * _ncdf(-d2)
    return {"delta": delta, "gamma": gamma, "theta": theta / 365.0, "vega": S * pdf * math.sqrt(T) / 100.0}


def implied_vol(right, S, K, T, price, r=RATE):
    """Bisection; None when the price sits outside what any volatility can produce."""
    if T <= 0 or price is None or price <= 0 or S <= 0:
        return None
    lo, hi = 0.01, 5.0
    if price < bs_price(right, S, K, T, lo, r) - 1e-9 or price > bs_price(right, S, K, T, hi, r) + 1e-9:
        return None
    for _ in range(80):
        mid = (lo + hi) / 2.0
        if bs_price(right, S, K, T, mid, r) < price:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


# ---------------------------------------------------------------- shared curve maths

def _cal_days(trading_days):
    return int(round(trading_days * 7.0 / 5.0))


def _grid(spot, strikes, extra):
    low = min([spot * 0.90] + [k * 0.95 for k in strikes] + [p * 0.98 for p in extra])
    high = max([spot * 1.10] + [k * 1.05 for k in strikes] + [p * 1.02 for p in extra])
    low = max(low, 0.01)
    step = (high - low) / (GRID_POINTS - 1)
    return [low + i * step for i in range(GRID_POINTS)]


def _leg_value(leg, S, valuation_day, shift=0.0):
    """Per-share value of one leg on `valuation_day` with the spot at S."""
    T = max((leg["expiry_date"] - valuation_day).days, 0) / 365.0
    iv = max(leg["iv"] + shift, 0.05)
    return bs_price(leg["right"], S, leg["strike"], T, iv)


def _position_value(legs, S, valuation_day, shift=0.0):
    """Signed dollars for the whole structure (qty is signed, multiplier included)."""
    return sum(leg["qty"] * leg["mult"] * _leg_value(leg, S, valuation_day, shift) for leg in legs)


def _breakevens(points):
    out = []
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        if y0 == 0:
            out.append(x0)
        elif y0 * y1 < 0:
            out.append(x0 + (x1 - x0) * (0 - y0) / (y1 - y0))
    return [round(x, 2) for x in out]


def _breakevens_wide(legs, day, cost, spot):
    """Zero crossings of the payoff at `day`, searched far beyond the drawn range so a distant breakeven is not lost."""
    lo, hi, n = spot * 0.05, spot * 3.0, 900
    pts = [(lo + (hi - lo) * i / n, _position_value(legs, lo + (hi - lo) * i / n, day) - cost) for i in range(n + 1)]
    return _breakevens(pts)


def _extremes(legs, valuation_day, cost):
    """Max profit and max loss at the first expiry; None marks an open-ended side."""
    probe = [0.0] + sorted({leg["strike"] for leg in legs}) + [1e6, 2e6]
    vals = {p: _position_value(legs, p, valuation_day) - cost for p in probe}
    top, top2 = vals[1e6], vals[2e6]
    unlimited_up = top2 > top + 1e-6
    unlimited_down = top2 < top - 1e-6
    finite = [v for p, v in vals.items() if p < 1e5]
    max_profit = None if unlimited_up else max(max(finite), top)
    max_loss = None if unlimited_down else min(min(finite), top)
    return max_profit, max_loss


# ---------------------------------------------------------------- plan cards

def _num(value, name, positive=False):
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} 不是数字")
    if not math.isfinite(number) or (positive and number <= 0):
        raise ValueError(f"{name} 必须是正数" if positive else f"{name} 不是有限数字")
    return number


def _parse_card_legs(card, today):
    legs = []
    for i, raw in enumerate(card.get("legs") or [], 1):
        try:
            side = {"buy": 1, "sell": -1}[str(raw.get("side")).lower()]
            right = str(raw.get("right")).upper()[:1]
            if right not in ("C", "P"):
                raise ValueError("right 只能是 C 或 P")
            expiry = datetime.strptime(str(raw.get("expiry")), "%Y-%m-%d").date()
            qty = int(_num(raw.get("qty", 1), "qty", positive=True))
            leg = {"side": raw["side"], "right": right, "strike": _num(raw.get("strike"), "strike", positive=True),
                   "expiry": str(raw["expiry"]), "expiry_date": expiry, "qty": side * qty, "mult": int(raw.get("mult", 100)),
                   "iv": None, "iv_source": None}
            for key in ("bid", "ask", "price"):
                if raw.get(key) is not None:
                    leg[key] = _num(raw[key], key)
            if raw.get("iv") is not None:
                leg["iv"], leg["iv_source"] = _num(raw["iv"], "iv", positive=True), "卡片给定"
            legs.append(leg)
        except (KeyError, ValueError, TypeError) as error:
            raise ValueError(f"第 {i} 条腿：{error}")
    if not legs:
        raise ValueError("没有任何一条腿")
    if min(leg["expiry_date"] for leg in legs) <= today:
        raise ValueError("最近的到期日已经过去，卡片需要更新")
    return legs


def _fill_iv(legs, spot, today, default_iv):
    for leg in legs:
        if leg["iv"] is not None:
            continue
        T = (leg["expiry_date"] - today).days / 365.0
        price = leg.get("price")
        if price is None and leg.get("bid") is not None and leg.get("ask") is not None:
            price = (leg["bid"] + leg["ask"]) / 2.0
        iv = implied_vol(leg["right"], spot, leg["strike"], T, price) if price is not None else None
        if iv is not None:
            leg["iv"], leg["iv_source"] = iv, "由报价反推"
        elif default_iv:
            leg["iv"], leg["iv_source"] = default_iv, "沿用卡片整体波动率"
        else:
            raise ValueError(f"{leg['strike']:g}{leg['right']} 没有波动率，也无法由报价反推")


def _mid(leg):
    if leg.get("price") is not None:
        return leg["price"]
    if leg.get("bid") is not None and leg.get("ask") is not None:
        return (leg["bid"] + leg["ask"]) / 2.0
    return None


def analyze_card(card, today, one_r=None):
    base = {"id": str(card.get("id") or "?"), "name": card.get("name") or str(card.get("id") or "?"), "source": "plan"}
    try:
        spot = _num(card.get("spot"), "标的现价", positive=True)
        legs = _parse_card_legs(card, today)
        default_iv = float(card["iv"]) if card.get("iv") else None
        _fill_iv(legs, spot, today, default_iv)
        contracts = int(_num(card.get("contracts", 1), "contracts", positive=True))
        for leg in legs:
            leg["qty"] *= contracts
        first_expiry = min(leg["expiry_date"] for leg in legs)
        entry = card.get("entry_price")
        if entry is None:
            mids = [_mid(leg) for leg in legs]
            if any(m is None for m in mids):
                raise ValueError("缺少 entry_price，且各腿没有 price 或 bid/ask 可合成")
            entry = sum((1 if leg["qty"] > 0 else -1) * abs(leg["qty"]) / contracts * m for leg, m in zip(legs, mids))
        entry = _num(entry, "entry_price")
        cost = entry * contracts * legs[0]["mult"]
        levels = [{"price": _num(l.get("price"), "价位", positive=True), "label": str(l.get("label") or ""), "kind": str(l.get("kind") or "note")}
                  for l in (card.get("levels") or [])]
        grid = _grid(spot, [leg["strike"] for leg in legs], [l["price"] for l in levels])
        horizons = [int(h) for h in (card.get("horizons") or DEFAULT_HORIZONS)]
        specs = [("expiry", f"到期（{first_expiry.isoformat()[5:]}）", first_expiry, 0.0)]
        for h in horizons:
            day = min(today + timedelta(days=_cal_days(h)), first_expiry)
            specs.append((f"t{h}", f"持有 {h} 个交易日", day, 0.0))
        last = horizons[-1] if horizons else None
        if last:
            day = min(today + timedelta(days=_cal_days(last)), first_expiry)
            specs.append((f"t{last}iv", f"持有 {last} 个交易日，波动率降 {int(abs(IV_SHIFT) * 100)} 点", day, IV_SHIFT))
        curves = []
        for key, label, day, shift in specs:
            pts = [[round(S, 2), round(_position_value(legs, S, day, shift) - cost, 2)] for S in grid]
            curves.append({"key": key, "label": label, "day": day.isoformat(), "points": pts})
        expiry_pts = [tuple(p) for p in curves[0]["points"]]
        max_profit, max_loss = _extremes(legs, first_expiry, cost)
        now = {"delta": 0.0, "gamma": 0.0, "theta": 0.0, "vega": 0.0}
        for leg in legs:
            g = bs_greeks(leg["right"], spot, leg["strike"], (leg["expiry_date"] - today).days / 365.0, leg["iv"])
            for k in now:
                now[k] += leg["qty"] * leg["mult"] * g[k]
        spread_cost = None
        natural = None
        if all(leg.get("bid") is not None and leg.get("ask") is not None for leg in legs):
            spread_cost = sum(abs(leg["qty"]) * leg["mult"] * (leg["ask"] - leg["bid"]) for leg in legs)
            natural = sum(abs(leg["qty"]) / contracts * (leg["ask"] if leg["qty"] > 0 else -leg["bid"]) for leg in legs)
        spots = sorted({round(spot, 2)} | {round(l["price"], 2) for l in levels} | set(card.get("scenario_spots") or []))
        grid_rows = []
        for S in spots:
            row = {"spot": S}
            for key, label, day, shift in specs:
                row[key] = round(_position_value(legs, S, day, shift) - cost, 0)
            grid_rows.append(row)
        for l in levels:
            l["pnl_expiry"] = round(_position_value(legs, l["price"], first_expiry) - cost, 0)
            if last:
                l["pnl_hold"] = round(_position_value(legs, l["price"], min(today + timedelta(days=_cal_days(last)), first_expiry)) - cost, 0)
        calc = {"spot": spot, "cost": round(cost, 2), "contracts": contracts, "first_dte": (first_expiry - today).days,
                "default_days": min(_cal_days(last), (first_expiry - today).days) if last else 0,
                "legs": [{"right": l["right"], "strike": l["strike"], "dte": (l["expiry_date"] - today).days, "qty": l["qty"],
                          "mult": l["mult"], "iv": round(l["iv"], 6)} for l in legs]}
        asof = card.get("asof")
        age = (today - datetime.strptime(asof, "%Y-%m-%d").date()).days if asof else None
        return dict(base, status="ok", underlying=card.get("underlying") or "", spot=spot, asof=asof, age_days=age,
                    stale=age is not None and age > STALE_DAYS, plan_status=card.get("status") or "plan",
                    thesis=card.get("thesis") or "", notes=list(card.get("notes") or []), order_draft=card.get("order_draft") or "",
                    contracts=contracts, entry_price=round(entry, 4), natural_price=None if natural is None else round(natural, 4),
                    net_debit_usd=round(cost, 0), max_profit=None if max_profit is None else round(max_profit, 0),
                    max_loss=None if max_loss is None else round(max_loss, 0),
                    max_loss_r=None if (max_loss is None or not one_r) else round(-max_loss / one_r, 2), one_r=one_r,
                    breakevens=_breakevens_wide(legs, first_expiry, cost, spot), expiry=first_expiry.isoformat(), dte=(first_expiry - today).days,
                    greeks={"delta_shares": round(now["delta"], 1), "delta_usd": round(now["delta"] * spot, 0),
                            "gamma": round(now["gamma"], 4), "theta_usd_day": round(now["theta"], 1),
                            "vega_usd_pt": round(now["vega"], 1)},
                    friction_usd=None if spread_cost is None else round(spread_cost, 0),
                    friction_pct=None if (spread_cost is None or not cost) else round(spread_cost / abs(cost) * 100, 1),
                    legs=[{"side": l["side"], "right": l["right"], "strike": l["strike"], "expiry": l["expiry"],
                           "qty": abs(l["qty"]), "bid": l.get("bid"), "ask": l.get("ask"), "iv": round(l["iv"], 4), "iv_source": l["iv_source"]}
                          for l in legs],
                    calc=calc, levels=levels, curves=curves, grid=grid_rows, grid_keys=[{"key": k, "label": lab} for k, lab, _, _ in specs],
                    r_note=None if one_r else "1R 不可用，未折算 R")
    except (ValueError, TypeError, KeyError) as error:
        return dict(base, status="error", error=str(error))


# ---------------------------------------------------------------- options already held

def _parse_label(label):
    """"Mar19'27 420C" -> (2027-03-19, 420.0, "C"); anything else -> None."""
    m = re.search(r"([A-Z][a-z]{2})(\d{1,2})'(\d{2})\s+([\d.]+)\s*([CP])", str(label or ""))
    if not m or m.group(1) not in MONTHS:
        return None
    return date(2000 + int(m.group(3)), MONTHS[m.group(1)], int(m.group(2))), float(m.group(4)), m.group(5)


def _number(value):
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    return n if math.isfinite(n) else None


def held_groups(positions, today, one_r=None, quotes=None):
    """One chart per (account, underlying, expiry); component legs only, never the combo summary rows.

    标的价格优先取仓位的 underlying_price（modelGreeks 顺带），缺失时退回快照顶层 quotes
    （走冻结希腊字母路径的腿没有 undPrice，不该因此整卡报错）。"""
    # plan-* 合成行是「交易计划」未关联快照时的聚合占位（app 约定其风险也不计入），不是腿；
    # 真腿在快照里，画图只用真腿，合成行不能把整组拖成「无法识别」。
    rows = [p for p in positions or [] if p.get("kind") == "option" and not p.get("display_only")
            and p.get("delta_status") != "combo" and not p.get("plan_id")]
    groups = {}
    for p in rows:
        parsed = _parse_label(p.get("label"))
        expiry = None
        try:
            expiry = datetime.strptime(str(p.get("expiry")), "%Y-%m-%d").date()
        except (TypeError, ValueError):
            expiry = parsed[0] if parsed else None
        strike = _number(p.get("strike")) or (parsed[1] if parsed else None)
        right = str(p.get("right") or p.get("call_put") or (parsed[2] if parsed else "")).upper()[:1]
        groups.setdefault((p.get("broker"), p.get("sym"), expiry), []).append((p, strike, right))
    out = []
    for (broker, sym, expiry), members in sorted(groups.items(), key=lambda kv: (str(kv[0][1]), str(kv[0][2]))):
        gid = f"held:{broker}:{sym}:{expiry}"
        name = f"{sym} 持仓结构（{broker}，{expiry.isoformat() if expiry else '到期日未知'}）"
        base = {"id": gid, "name": name, "source": "held", "underlying": sym}
        try:
            if expiry is None or expiry <= today:
                raise ValueError("到期日缺失或已过")
            spot = next((s for s in (_number(p.get("underlying_price")) for p, _, _ in members) if s), None)
            if spot is None and quotes:
                q = quotes.get(str(sym))
                spot = _number(q.get("close") if isinstance(q, dict) else q)
            if spot is None:
                raise ValueError("持仓快照里没有标的价格（仓位与 quotes 都没给）")
            legs, cost, now_value = [], 0.0, 0.0
            for p, strike, right in members:
                if not strike or right not in ("C", "P"):
                    raise ValueError(f"{p.get('label')} 的行权价或方向无法识别")
                qty = _number(p.get("qty")) or 0.0
                if qty == 0:
                    continue
                # the app's position rows carry an unsigned quantity and a side; the ledger itself signs the quantity
                qty = -abs(qty) if str(p.get("side") or "").lower() == "short" else (qty if qty < 0 else abs(qty))
                mult = int(_number(p.get("mult")) or 100)
                mark, open_price = _number(p.get("mark")), _number(p.get("open"))
                T = (expiry - today).days / 365.0
                iv = implied_vol(right, spot, strike, T, mark) if mark else None
                source = "由持仓标价反推"
                if iv is None:
                    iv, source = 0.5, "标价无法反推，暂按 50% 占位"
                legs.append({"side": "buy" if qty > 0 else "sell", "right": right, "strike": strike, "expiry": expiry.isoformat(),
                             "expiry_date": expiry, "qty": qty, "mult": mult, "iv": iv, "iv_source": source,
                             "open": open_price, "mark": mark})
                cost += qty * mult * (open_price if open_price is not None else (mark or 0.0))
                now_value += qty * mult * (mark or 0.0)
            if not legs:
                raise ValueError("没有数量不为 0 的腿")
            strikes = [l["strike"] for l in legs]
            grid = _grid(spot, strikes, [])
            specs = [("expiry", f"到期（{expiry.isoformat()[5:]}）", expiry), ("now", "今天", today)]
            curves = [{"key": k, "label": lab, "day": d.isoformat(),
                       "points": [[round(S, 2), round(_position_value(legs, S, d) - cost, 2)] for S in grid]} for k, lab, d in specs]
            max_profit, max_loss = _extremes(legs, expiry, cost)
            now = {"delta": 0.0, "gamma": 0.0, "theta": 0.0, "vega": 0.0}
            for l in legs:
                g = bs_greeks(l["right"], spot, l["strike"], (expiry - today).days / 365.0, l["iv"])
                for k in now:
                    now[k] += l["qty"] * l["mult"] * g[k]
            calc = {"spot": spot, "cost": round(cost, 2), "contracts": 1, "first_dte": (expiry - today).days, "default_days": 0,
                    "legs": [{"right": l["right"], "strike": l["strike"], "dte": (l["expiry_date"] - today).days, "qty": l["qty"],
                              "mult": l["mult"], "iv": round(l["iv"], 6)} for l in legs]}
            out.append(dict(base, status="ok", calc=calc, spot=spot, one_r=one_r, expiry=expiry.isoformat(), dte=(expiry - today).days,
                            net_debit_usd=round(cost, 0), unrealized=round(now_value - cost, 0),
                            max_profit=None if max_profit is None else round(max_profit, 0),
                            max_loss=None if max_loss is None else round(max_loss, 0),
                            max_loss_r=None if (max_loss is None or not one_r) else round(-max_loss / one_r, 2),
                            breakevens=_breakevens_wide(legs, expiry, cost, spot),
                            greeks={"delta_shares": round(now["delta"], 1), "delta_usd": round(now["delta"] * spot, 0),
                                    "gamma": round(now["gamma"], 4), "theta_usd_day": round(now["theta"], 1), "vega_usd_pt": round(now["vega"], 1)},
                            legs=[{"side": l["side"], "right": l["right"], "strike": l["strike"], "expiry": l["expiry"], "qty": abs(l["qty"]),
                                   "iv": round(l["iv"], 4), "iv_source": l["iv_source"], "open": l["open"], "mark": l["mark"],
                                   "pnl_usd": None if l["open"] is None or l["mark"] is None
                                   else round(l["qty"] * l["mult"] * (l["mark"] - l["open"]), 0)} for l in legs],
                            levels=[], curves=curves, grid=[], grid_keys=[], notes=[], order_draft="", contracts=1))
        except ValueError as error:
            out.append(dict(base, status="error", error=str(error)))
    return out


# ---------------------------------------------------------------- state entry point

def load_cards(directory=None):
    directory = Path(directory) if directory else _dir()
    if not directory.is_dir():
        return None, []
    cards = []
    for path in sorted(directory.glob("*.json")):
        try:
            body = json.loads(path.read_text())
            if not isinstance(body, dict):
                raise ValueError("顶层不是对象")
            body.setdefault("id", path.stem)
            cards.append(body)
        except (OSError, ValueError) as error:
            cards.append({"id": path.stem, "name": path.stem, "_unreadable": f"读取失败：{error}"})
    return directory, cards


def build(positions, today=None, one_r=None, directory=None, quotes=None):
    today = today or datetime.now().date()
    folder, cards = load_cards(directory)
    result = {"status": "ok", "dir": str(folder or (Path(directory) if directory else _dir())), "one_r": one_r,
              "today": today.isoformat(), "cards": [], "held": [], "notices": []}
    if folder is None:
        result["status"] = "missing"
        result["notices"].append("临时卡文件夹还不存在，目前只显示持仓结构。")
    for card in cards:
        if card.get("_unreadable"):
            result["cards"].append({"id": card["id"], "name": card["name"], "source": "plan", "status": "error", "error": card["_unreadable"]})
        else:
            result["cards"].append(analyze_card(card, today, one_r))
    result["cards"].sort(key=lambda c: str(c.get("asof") or ""), reverse=True)
    result["cards"].sort(key=lambda c: c.get("status") != "ok")
    try:
        result["held"] = held_groups(positions, today, one_r, quotes=quotes)
    except Exception as error:  # the page must say it failed, not show an empty list
        result["notices"].append(f"持仓结构图生成失败：{type(error).__name__}")
    if one_r is None:
        result["notices"].append("1R 不可用，图表没有折算 R。")
    return result
