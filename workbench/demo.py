"""Demo mode: write a complete set of invented sources and a seeded ledger, dated around today.

    python3 -m workbench.demo --out /tmp/shepherd-demo      # then point the app at it:
    SHEPHERD_DATA_DIR=/tmp/shepherd-demo/data SHEPHERD_SOURCE_HOME=/tmp/shepherd-demo/sources open Shepherd.app

The app's 「文件 → 打开演示」 does the same in a temporary folder. Every price, position, account and
note here is made up by a seeded random walk; nothing is read from the network or from a real account.
Standard library only, so it runs inside the app's bundled Python.
"""
from __future__ import annotations

import argparse
import json
import math
import random
import uuid
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

from .sources import ET

DEMO = "演示数据（虚构）"
SEED = 19  # picked for a normal-looking tape: up about 40% over three years, a few percent off the high


def market_day(now):
    """Last weekday whose close has happened (holidays not modelled)."""
    day = now.date()
    if day.weekday() >= 5 or now.time() < time(16, 5):
        day -= timedelta(days=1)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


def weekdays(end, count):
    out, day = [], end
    while len(out) < count:
        if day.weekday() < 5:
            out.append(day)
        day -= timedelta(days=1)
    return out[::-1]


def third_friday(year, month):
    d = date(year, month, 15)
    while d.weekday() != 4:
        d += timedelta(days=1)
    return d


def option_label(expiry, strike, right):
    return f"{expiry.strftime('%b')}{expiry.day:02d}'{expiry.strftime('%y')} {strike:g}{right}"


# symbol: (exchange, last close, beta to the market factor, own daily vol)
UNIVERSE = {
    "SPX": ("TVC", 6480.0, 1.0, 0.0), "SPY": ("AMEX", 646.0, 1.0, 0.0005), "VOO": ("AMEX", 594.0, 1.0, 0.0006),
    "IWM": ("AMEX", 231.0, 1.2, 0.006), "QQQ": ("NASDAQ", 562.0, 1.15, 0.004),
    "XLF": ("AMEX", 52.4, 1.05, 0.006), "XLP": ("AMEX", 81.2, 0.55, 0.006), "XLV": ("AMEX", 146.0, 0.7, 0.007),
    "XLE": ("AMEX", 91.5, 0.8, 0.012), "XLI": ("AMEX", 141.0, 1.05, 0.006), "XLY": ("AMEX", 214.0, 1.15, 0.007),
    "IGV": ("AMEX", 101.0, 1.2, 0.010), "SMH": ("NASDAQ", 281.0, 1.5, 0.012), "GLD": ("AMEX", 301.0, 0.1, 0.009),
    "TLT": ("NASDAQ", 89.5, -0.2, 0.008),
    "KO": ("NYSE", 68.0, 0.5, 0.009), "JPM": ("NYSE", 290.0, 1.1, 0.012), "UNH": ("NYSE", 520.0, 0.6, 0.015),
    "META": ("NASDAQ", 700.0, 1.3, 0.017), "AMD": ("NASDAQ", 165.0, 1.6, 0.022), "XOM": ("NYSE", 115.0, 0.7, 0.012),
    "IAU": ("AMEX", 60.0, 0.1, 0.009), "BIL": ("AMEX", 91.5, 0.0, 0.0002), "HD": ("NYSE", 400.0, 1.0, 0.012),
    "DE": ("NYSE", 440.0, 1.0, 0.014), "PG": ("NYSE", 160.0, 0.45, 0.008),
}


def price_history(days, rng):
    market = [rng.gauss(0.0004, 0.0105) for _ in days]
    series = {}
    for sym, (_, last, beta, vol) in UNIVERSE.items():
        logp, path = 0.0, []
        for m in market:
            logp += beta * m + rng.gauss(0, vol)
            path.append(logp)
        scale = last / math.exp(path[-1])
        bars = []
        for i, day in enumerate(days):
            close = scale * math.exp(path[i])
            prev = scale * math.exp(path[i - 1]) if i else close
            spread = abs(rng.gauss(0, max(vol, 0.004))) * close
            o = prev * (1 + rng.gauss(0, 0.002))
            bars.append({"day": day, "o": round(o, 2), "h": round(max(o, close) + spread, 2),
                         "l": round(min(o, close) - spread, 2), "c": round(close, 2)})
        series[sym] = bars
    return series


def ema(values, span):
    k, out, e = 2 / (span + 1), [], None
    for v in values:
        e = v if e is None else e + k * (v - e)
        out.append(e)
    return out


def weekly(bars):
    weeks, current = [], None
    for b in bars:
        friday = b["day"] + timedelta(days=4 - b["day"].weekday())
        if current is None or current["t"] != friday:
            current = {"t": friday, "o": b["o"], "h": b["h"], "l": b["l"], "c": b["c"]}
            weeks.append(current)
        else:
            current.update(h=max(current["h"], b["h"]), l=min(current["l"], b["l"]), c=b["c"])
    return weeks


def write(path, body):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body, ensure_ascii=False, indent=1, default=str))


def stop(sid, sym, level, level0=None, moves=0):
    return [{"id": sid, "kind": "止损", "basis": "und", "level": level, "dir": "<=", "locked": False, "pending": False,
             "text": f"{sym} 收盘 ≤ {level:g} → 卖出（收盘口径）", "note": DEMO,
             "level0": level0 if level0 is not None else level, "moves": [{}] * moves}]


def build(out, now=None, seed=SEED):
    out = Path(out)
    now = now or datetime.now(ET)
    rng = random.Random(seed)
    day = market_day(now)
    days = weekdays(day, 820)
    hist = price_history(days, rng)
    last = {s: bars[-1]["c"] for s, bars in hist.items()}
    src = out / "sources" / "tools"
    stamp = datetime.combine(day, time(16, 5), ET).astimezone(timezone.utc).isoformat()

    # ---------------------------------------------------------------- daily bars (risk page, Beta, VaR)
    def tv_bars(bars):
        return [{"t": int(datetime.combine(b["day"], time(20), timezone.utc).timestamp()),
                 "o": b["o"], "h": b["h"], "l": b["l"], "c": b["c"], "v": 1_000_000} for b in bars[-300:]]
    bundle = {"price_basis": "regular_split_adjusted", "signal_day": day.isoformat(), "collected_at": stamp,
              "source": DEMO,
              "series": {f"{UNIVERSE[s][0]}:{s}": {"daily": {"success": True, "interval": "1D", "bars": tv_bars(b)}}
                         for s, b in hist.items()}}
    bars_dir = src / "daily_digest" / "cache" / "addscore"
    write(bars_dir / f"{day.isoformat()}.bundle.json", bundle)

    # ---------------------------------------------------------------- holdings snapshot
    near = day + timedelta(days=2)
    while near.weekday() != 4:
        near += timedelta(days=1)
    month = third_friday(*(lambda d: (d.year, d.month))(day + timedelta(days=35)))
    leap = third_friday(day.year + 1, 1) if day.month < 10 else third_friday(day.year + 1, 6)
    def stock(i, sym, qty, open_, trigs=None, group="", broker="示例券商 A"):
        return {"id": f"d{i}", "broker": broker, "sym": sym, "label": f"正股 {qty:g} 股", "group": group, "role": "",
                "kind": "stock", "side": "long", "qty": qty, "mult": 1, "open": open_, "mark": last[sym], "expiry": "",
                "tail": None, "tailNote": DEMO, "confirm": False, "trigs": trigs or [], "mark_asof": stamp, "mark_source": DEMO}
    def option(i, sym, expiry, strike, right, side, qty, open_, mark, delta, group="", broker="示例券商 A", trigs=None):
        return {"id": f"d{i}", "broker": broker, "sym": sym, "label": option_label(expiry, strike, right), "group": group,
                "role": "", "kind": "option", "side": side, "qty": qty, "mult": 100, "open": open_, "mark": mark,
                "expiry": expiry.isoformat(), "right": right, "strike": strike, "tail": None, "tailNote": DEMO,
                "confirm": False, "trigs": trigs or [], "delta": delta, "delta_status": "ok", "delta_asof": stamp,
                "delta_source": DEMO, "mark_asof": stamp, "mark_source": DEMO}
    positions = [
        stock(1, "KO", 120, 62.10, stop("t1", "KO", round(last["KO"] * .95, 2), 58.9, 1)),
        stock(2, "JPM", 25, 262.40, stop("t2", "JPM", round(last["JPM"] * .94, 2), 249.0, 2)),
        stock(3, "UNH", 10, 498.00, stop("t3", "UNH", 485.0)),
        stock(4, "META", 6, 655.00, broker="示例券商 B"),
        stock(5, "AMD", 40, 150.20, stop("t5", "AMD", 147.0), broker="示例券商 B"),
        stock(6, "XOM", 40, 108.50, stop("t6", "XOM", round(last["XOM"] + 1.0, 2))),
        stock(7, "IAU", 150, 55.40, stop("t7", "IAU", 54.0)),
        stock(8, "BIL", 300, 91.40, group="现金管理 · 闲钱停放（免判据）"),
        option(9, "AMD", leap, 180, "C", "long", 2, 9.40, 7.10, 0.38, broker="示例券商 B",
               trigs=stop("t9", "AMD", 150.0)),
        option(10, "IWM", month, 215, "P", "short", 2, 3.10, 2.20, -0.28, group="IWM 215/205 卖出看跌价差"),
        option(11, "IWM", month, 205, "P", "long", 2, 1.40, 0.90, -0.15, group="IWM 215/205 卖出看跌价差"),
        option(12, "META", near, 720, "C", "long", 1, 4.20, 1.10, 0.21, broker="示例券商 B"),
    ]
    snapshot = {"asof": f"{DEMO} ｜ 收盘价 {day.isoformat()}",
                "accounts": [{"id": "示例券商 A", "name": "示例券商 A（虚构）", "nlv": 64000.0, "cash": 18200.0},
                             {"id": "示例券商 B", "name": "示例券商 B（虚构）", "nlv": 36000.0, "cash": 8400.0}],
                "positions": positions, "quotes": {s: last[s] for s in ("KO", "JPM", "UNH", "META", "AMD", "XOM", "IAU", "BIL", "IWM", "SPY", "HD", "DE", "PG")},
                "benchmarkQuote": {"symbol": "SPX", "price": last["SPX"], "day": day.isoformat(), "asof": stamp, "source": DEMO},
                "plans": []}
    write(src / "holdings_local" / "state.json", snapshot)

    # ---------------------------------------------------------------- premarket notes, calendar, earnings
    from .overview import expected_brief_day
    brief_day = expected_brief_day(now)
    rows = [{"symbol": f"{UNIVERSE[s][0]}:{s}", "quiet": False, "close": last[s], "facts": facts}
            for s, facts in (
                ("AMD", {"rsi6": "连续 2 天低位，RSI6 24.8", "confluence": "收盘回到 50 日 EMA 附近", "market_day": "同日大盘 +0.3%"}),
                ("JPM", {"rsi6": "RSI6 回升到 41.2", "market_day": "金融板块同日 +0.6%"}),
                ("XOM", {"confluence": "收盘跌破止损价，按规则今天处理", "market_day": "能源板块同日 −1.4%"}),
                ("UNH", {"rsi6": "RSI6 31.5，第 1 天进入低位"}))]
    for r in rows:
        r["facts"]["context_unknown"] = "演示数据：这些观察是虚构的"
    write(bars_dir / f"{brief_day.isoformat()}.editorial.json",
          {"report_day": (brief_day + timedelta(days=1)).isoformat(), "reviewed_at": stamp, "error": None,
           "sheet": {"signal_day": brief_day.isoformat(), "rows": rows}})
    events = []
    for k in range(-1, 5):  # one NFP (first Friday), one CPI (first weekday from the 12th), FOMC every other month
        y, m = divmod(day.month - 1 + k, 12)
        first = date(day.year + y, m + 1, 1)
        nfp = first + timedelta(days=(4 - first.weekday()) % 7)
        cpi = first.replace(day=12)
        while cpi.weekday() >= 5:
            cpi += timedelta(days=1)
        events += [{"date": nfp.isoformat(), "name": "非农就业（演示）", "time": "08:30", "kind": "NFP"},
                   {"date": cpi.isoformat(), "name": "CPI（演示）", "time": "08:30", "kind": "CPI"}]
        if (m + 1) % 2 == 1:
            fomc = first.replace(day=15) + timedelta(days=(2 - first.replace(day=15).weekday()) % 7)
            events.append({"date": fomc.isoformat(), "name": "议息会议（演示）", "time": "14:00", "kind": "FOMC"})
    write(src / "daily_digest" / "calendar_data.json", {"source": DEMO, "events": events})
    write(src / "daily_digest" / "cache" / "earnings.json",
          {"source": DEMO, "data": {"JPM": (day + timedelta(days=6)).isoformat(), "KO": (day + timedelta(days=13)).isoformat(),
                                    "META": (day + timedelta(days=20)).isoformat(), "AMD": (day + timedelta(days=16)).isoformat()}})

    # ---------------------------------------------------------------- macro light
    def ma(sym, n):
        closes = [b["c"] for b in hist[sym][-n:]]
        return sum(closes) / len(closes)
    def trend(name, sym):
        m200, m200_old = ma(sym, 200), sum(b["c"] for b in hist[sym][-220:-20]) / 200
        vs = (last[sym] / m200 - 1) * 100
        state = "顺风" if vs > 2 and m200 > m200_old else "逆风" if vs < -2 and m200 < m200_old else "过渡"
        return {"name": name, "proxy": sym, "state": state, "day": day.isoformat(), "close": last[sym],
                "vs_ma200_pct": round(vs, 1), "ma200_rising": m200 > m200_old,
                "rule": {"顺风": "逢低买（回调到位再确认）", "逆风": "反弹时减多仓", "过渡": "等方向；已有仓位按止损"}[state]}
    weeks_back = [day - timedelta(days=day.weekday() - 4 + 7 * i) for i in range(12)][::-1]
    write(src / "macro_panel" / "out" / "state.json", {
        "schema": "macro-panel/1", "generated_at": stamp, "data_day": day.isoformat(), "light": "黄", "raw_today": "黄",
        "stage": "normal", "fuel_high": True, "vix_cheap": True,
        "knobs": {"heat_cap_r": 6, "bucket_scale": 0.75, "unconfirmed": "科技桶 0，其他桶 ≤ 1/3",
                  "insurance": "各桶上限打 75 折"},
        "families": [{"key": "F1", "name": "估值", "role": "出事时能跌多深", "grade": "红", "reading": "演示读数"},
                     {"key": "F2", "name": "信用", "role": "资金链有没有绷紧", "grade": "绿", "reading": "演示读数"},
                     {"key": "F3", "name": "广度", "role": "上涨是不是少数股票撑着", "grade": "黄", "reading": "演示读数"},
                     {"key": "F4", "name": "利率", "role": "估值的地心引力", "grade": "黄", "reading": "演示读数"},
                     {"key": "F5", "name": "波动", "role": "火苗：恐慌有没有起来", "grade": "绿", "reading": "演示读数"}],
        "history": [{"week": w.isoformat(), "raw": "黄" if i > 3 else "绿", "effective": "黄" if i > 4 else "绿"}
                    for i, w in enumerate(weeks_back)],
        "disarm": {"rel1": "波动回落且信用不再走阔，或标普站上 50 日线", "rel2": "标普站上 200 日线",
                   "ma50": round(ma("SPX", 50), 1), "ma200": round(ma("SPX", 200), 1), "spx": last["SPX"]},
        "trends": [trend(n, s) for n, s in (("宽基", "SPY"), ("科技", "QQQ"), ("金融", "XLF"), ("能源", "XLE"),
                                             ("医疗", "XLV"), ("黄金", "GLD"), ("利率", "TLT"))],
        "trend_rule": {"顺风": "逢低买（回调到位再确认）", "逆风": "反弹时减多仓", "过渡": "等方向；已有仓位按止损"},
        "trend_evidence": "演示数据：没有历史回测依据", "breadth": {}, "notes": ["演示数据：灯和趋势都是虚构的。"]})

    # ---------------------------------------------------------------- weekly watch
    symbols = {}
    for sym in ("VOO", "IWM"):
        wk = weekly(hist[sym])[-156:]
        closes = [w["c"] for w in wk]
        lines = {span: ema(closes, span) for span in (20, 50, 100, 200)}
        rows_ = []
        for i, w in enumerate(wk):
            rows_.append(dict({k: round(v, 2) for k, v in w.items() if k != "t"}, t=w["t"].isoformat(),
                              **{f"e{span}": round(lines[span][i], 2) for span in lines}))
        atr = sum(w["h"] - w["l"] for w in wk[-14:]) / 14
        close, hi = closes[-1], max(w["h"] for w in wk[-52:])
        tiers = []
        for span, reach, deep in ((20, 78, 41), (50, 56, 12), (100, 31, 5), (200, 14, 2)):
            e = lines[span][-1]
            tiers.append({"span": span, "ema": round(e, 2), "dist_pct": round((e / close - 1) * 100, 1),
                          "dist_atr": round((e - close) / atr, 1), "close_below": close < e,
                          "touched_this_week": wk[-1]["l"] <= e <= wk[-1]["h"], "first_touch": False,
                          "hist_reach": reach, "hist_deepest": deep})
        # old highs: weekly swing highs (highest within four weeks either side) below the close, at least 2 ATR apart
        swings = [wk[i]["h"] for i in range(4, len(wk) - 20) if wk[i]["h"] == max(w["h"] for w in wk[i - 4:i + 5]) and wk[i]["h"] < close]
        highs = []
        for h in sorted(swings, reverse=True):
            if all(abs(h - x) >= 2 * atr for x in highs):
                highs.append(round(h, 2))
        highs = highs[:3]
        lows = sorted(wk[-26:], key=lambda w: w["l"])[:2]
        e50 = lines[50][-1]
        symbols[sym] = {"symbol": sym, "last_day": day.isoformat(), "week": wk[-1]["t"].isoformat(), "partial_week": day.weekday() != 4,
                        "close": close, "week_change_pct": round((closes[-1] / closes[-2] - 1) * 100, 2), "atr": round(atr, 2),
                        "high_52w": hi, "from_high_pct": round((close / hi - 1) * 100, 1), "done_week": wk[-1]["t"].isoformat(),
                        "done_close": close, "done_e50": round(e50, 2), "tiers": tiers,
                        "old_highs": [{"price": h, "week": None, "breakout_week": None} for h in highs],
                        "swing_lows": [{"price": w["l"], "week": w["t"].isoformat()} for w in lows],
                        "zone": {"low": round(e50 * .995, 2), "high": round(e50 * 1.005, 2), "label": "50 周 EMA 一带"},
                        "weeks": rows_}
    voo50 = symbols["VOO"]["done_e50"]
    write(src / "weekly_watch" / "out" / "state.json", {
        "schema": "weekly-watch/2", "generated_at": stamp, "data_day": day.isoformat(), "order": ["VOO", "IWM"], "symbols": symbols,
        "triggers": [
            {"key": "warning", "severity": "amber", "status": "off", "label": "预警：VOO 周收盘跌破 50 周 EMA",
             "detail": f"演示条件。VOO {symbols['VOO']['close']:g} 对 50 周 EMA {voo50:g}",
             "armed": {"title": f"重点观察：VOO 周收盘低于 {voo50:g} 就出预警",
                       "detail": f"还差 {abs(symbols['VOO']['tiers'][1]['dist_pct']):g}%（演示数据）"}},
            {"key": "confirm", "severity": "red", "status": "off", "label": "确认：标普 500 周线反弹失败且距高点 ≥ 8%",
             "detail": "演示条件，尚未满足"},
            {"key": "credit", "severity": "red", "status": "off", "label": "信用：高收益债利差 63 个交易日走阔 ≥ 90bp",
             "detail": "演示条件，尚未满足"}],
        "slow_bear": [{"name": "标普 500", "peak_week": None, "peak_close": None, "week": day.isoformat(), "close": last["SPX"],
                       "drop_pct": round((last["SPX"] / max(b["c"] for b in hist["SPX"][-260:]) - 1) * 100, 1),
                       "weeks_since_peak": 3, "m2": {"status": "off", "detail": "峰值后还没有确认的反弹高点"},
                       "m4": {"status": "off", "stage": 0, "detail": "还没跌到 50 周 EMA 下方", "drop_pct": None}}],
        "light_note": "演示数据：条件和数字都是虚构的，只用来展示页面。",
        "slow_note": "慢熊信号说明（演示）。", "history_note": "历史比例（演示）。", "source": DEMO})

    # ---------------------------------------------------------------- paper book
    ratio = [b1["c"] / b2["c"] for b1, b2 in zip(hist["XLV"][-8:], hist["XLY"][-8:])]
    entry_xlv, entry_xly = hist["XLV"][-8]["c"], hist["XLY"][-8]["c"]
    pnl = 100 * (last["XLV"] - entry_xlv) - 70 * (last["XLY"] - entry_xly)
    stop_line = round(ratio[0] * .955, 6)
    write(src / "paper_book" / "out" / "state.json", {
        "schema": "paper-book-state/1", "generated_at": stamp, "data_day": day.isoformat(), "one_r": 2000.0,
        "r_source": "演示：定仓净值 100,000 × 2%", "costs_note": "演示：未计费用", "source": DEMO,
        "totals": {"open": 1, "closed": 0, "pnl": round(pnl, 2), "pnl_r": round(pnl / 2000, 3), "budget_r_open": 0.5},
        "trades": [{"id": "P001", "name": "多 XLV 空 XLY", "thesis": "演示：防御板块跑赢可选消费。",
                    "opened_at": hist["XLV"][-8]["day"].isoformat(), "entry_day": hist["XLV"][-8]["day"].isoformat(),
                    "entry_source": DEMO, "budget_r": 0.5, "sizing": "两条腿等金额（演示）。", "review_on": (day + timedelta(days=60)).isoformat(),
                    "status": "open", "closed_day": None, "close_reason": None,
                    "stop_text": "比值 XLV÷XLY 收盘低于止损线就离场（演示）。", "stop_kind": "ratio",
                    "legs": [{"sym": "XLV", "side": "long", "qty": 100, "entry": entry_xlv, "last": last["XLV"],
                              "pnl": round(100 * (last["XLV"] - entry_xlv), 2), "ret_pct": round((last["XLV"] / entry_xlv - 1) * 100, 2)},
                             {"sym": "XLY", "side": "short", "qty": 70, "entry": entry_xly, "last": last["XLY"],
                              "pnl": round(-70 * (last["XLY"] - entry_xly), 2), "ret_pct": round(-(last["XLY"] / entry_xly - 1) * 100, 2)}],
                    "events": [{"day": hist["XLV"][-8]["day"].isoformat(), "kind": "open", "text": "开仓（演示）。"}],
                    "day": day.isoformat(), "pnl": round(pnl, 2), "pnl_r": round(pnl / 2000, 3),
                    "gross": round(100 * last["XLV"] + 70 * last["XLY"], 2), "ret_on_gross_pct": None, "budget_usd": 1000.0,
                    "ratio_name": "XLV÷XLY", "ratio_entry": round(ratio[0], 6), "ratio": round(ratio[-1], 6),
                    "ratio_vs_entry_pct": round((ratio[-1] / ratio[0] - 1) * 100, 2), "stop_line": stop_line,
                    "held_days": 7, "max_pnl_r": None, "min_pnl_r": None,
                    "stop_gap_pct": round((ratio[-1] / stop_line - 1) * 100, 2),
                    "series": [{"day": a["day"].isoformat(), "pnl": round(p, 2), "pnl_r": round(p / 2000, 3), "ratio": round(r, 6), "stop_line": stop_line}
                               for a, b, r in zip(hist["XLV"][-8:], hist["XLY"][-8:], ratio)
                               for p in [100 * (a["c"] - entry_xlv) - 70 * (b["c"] - entry_xly)]]}],
        "closed_this_run": []})

    # ---------------------------------------------------------------- option plan card
    spot = last["DE"]
    k1, k2 = round(spot / 10) * 10 + 10, round(spot / 10) * 10 + 40
    write(src / "option_plans" / "demo-de-call-spread.json", {
        "id": "demo-de-call-spread", "name": f"DE {month.month}/{month.day} {k1}/{k2} 看涨价差（演示）", "underlying": "DE",
        "spot": spot, "asof": day.isoformat(), "status": "plan",
        "thesis": "演示：回踩支撑后反弹到前高。数字全部虚构。", "entry_price": 9.80, "contracts": 1, "horizons": [3, 5],
        "legs": [{"side": "buy", "right": "C", "strike": k1, "expiry": month.isoformat(), "qty": 1, "bid": 16.20, "ask": 16.80},
                 {"side": "sell", "right": "C", "strike": k2, "expiry": month.isoformat(), "qty": 1, "bid": 6.60, "ask": 7.00}],
        "levels": [{"price": round(spot * .965, 1), "label": "作废线（收盘跌破）", "kind": "invalid"},
                   {"price": round(spot * .99, 1), "label": "入场区", "kind": "entry"},
                   {"price": round(spot * 1.06, 1), "label": "目标", "kind": "target"}],
        "scenario_spots": [round(spot * .96), round(spot * 1.03), round(spot * 1.08)],
        "order_draft": "演示：限价 9.80 买入 1 张。", "notes": ["演示计划卡：报价和隐含波动率都是虚构的。"]})

    # ---------------------------------------------------------------- broker receipt inbox
    inbox = src / "schwab_confirms" / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    (inbox / "demo-alert.txt").write_text(
        "From: Schwab Alerts <donotreply@mail.schwab.com>\n"
        "Subject: Your trade was executed in your Schwab account ending in 000\n\n"
        f"Account ending: 000\n{day.strftime('%b %d, %Y')}\n\nWe executed a trade in your account.\n\n"
        f"Here are the details for the trade executed on {day.strftime('%m/%d/%Y')} in your account ending in 000:\n"
        "Action:\nBought\nQuantity:\n20\nSymbol:\nPG\nUnit price:\n$158.40\nPrincipal amount\n$3,168.00*\n\n"
        "（演示：虚构的成交通知）\n", encoding="utf-8")

    # ---------------------------------------------------------------- ledger: R, modules, three plans
    from .application import Application
    from .store import et_today
    app = Application(out / "data", out / "sources", inbox)
    app.post("/api/risk-policy", {"version": 0, "nlv": 100000, "risk_pct": 2})
    app.post("/api/modules", {"enabled": {k: True for k in ("calendar", "optionPlans", "preparation", "macro", "weekly", "paper", "econf")},
                              "onboarded": False})
    base = {"kind": "stock", "side": "long", "broker": "示例券商 A"}
    held = app.post("/api/plans", dict(base, symbol="HD", title="回踩 50 日线", entry=396.0, stop=372.0,
                                       thesis="演示：回踩 50 日 EMA 后放量收回。", invalidation="收盘跌破 372",
                                       exit_rule="前高 430 附近减半，其余跟踪止损", review_day=(day + timedelta(days=30)).isoformat()))
    held = app.post("/api/status", {"id": held["id"], "version": held["version"], "status": "ready"})
    app.post("/api/fills", {"id": held["id"], "version": held["version"], "request_id": uuid.uuid4().hex, "action": "entry",
                            "qty": 83, "price": 396.4, "fee": 1.0,
                            "day": min(day, et_today()).isoformat(), "note": "演示成交"})
    ready = app.post("/api/plans", dict(base, symbol="DE", title="突破回踩", entry=440.0, stop=425.0,
                                        thesis="演示：突破箱体后第一次回踩。", invalidation="收盘跌回箱体 425 以下",
                                        exit_rule="目标 480，跌破 20 日 EMA 走一半", review_day=(day + timedelta(days=14)).isoformat()))
    app.post("/api/status", {"id": ready["id"], "version": ready["version"], "status": "ready"})
    app.post("/api/plans", dict(base, symbol="PG", title="观察，未定入场", entry=None, stop=None,
                                thesis="演示：等回到 150 一带再看。", invalidation="", exit_rule=""))
    return out


def main():
    parser = argparse.ArgumentParser(description="写一套虚构的演示数据和账本")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    print(build(args.out))


if __name__ == "__main__":
    main()
