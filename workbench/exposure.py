"""Read-only Delta and market-factor exposure. Missing inputs never mean zero risk.

Dollar Delta, beta regression and the 1% linear scenario are not VaR. This module
does not fetch prices, modify positions, or impose portfolio allocation limits.
"""
from __future__ import annotations

import math
from datetime import date, datetime

from .sources import ET, finite
from .position_display import partition_positions


# Coarse, editable labels, not holdings-level ETF look-through or a factor model.
DEFAULT_METADATA = {
    **{s: {"sector": "信息技术", "theme": "科技成长"} for s in
       ("AAPL", "MSFT", "NVDA", "AMD", "ARM", "AVGO", "ORCL", "CRM", "ADBE", "PLTR", "TSM", "SMH", "XLK")},
    **{s: {"sector": "通信服务", "theme": "科技成长"} for s in ("GOOG", "GOOGL", "META")},
    **{s: {"sector": "可选消费", "theme": "科技成长"} for s in ("AMZN", "TSLA")},
    "QQQ": {"sector": "ETF · 行业穿透未知", "theme": "科技成长主题（非纯科技）", "asset_type": "etf"},
    "QQQM": {"sector": "ETF · 行业穿透未知", "theme": "科技成长主题（非纯科技）", "asset_type": "etf"},
    **{s: {"sector": "ETF · 行业穿透未知", "theme": "宽基指数", "asset_type": "etf"}
       for s in ("SPY", "VOO", "IVV", "IWM", "DIA", "VTI")},
    **{s: {"sector": "ETF · 行业穿透未知", "theme": "黄金", "asset_type": "etf"}
       for s in ("GLD", "IAU")},
}
for _symbol in ("SMH", "XLK"):
    DEFAULT_METADATA[_symbol].update(sector="ETF · 行业穿透未知", asset_type="etf")


# Factor buckets: two levels. A bucket (科技 / 非科技 / 黄金 / 利率 / 宽基) carries the
# prompt cap; its sleeves (软件 / 硬件 / M7, 金融 / 消费 / ...) keep their own proxy and
# shock but have no cap of their own. Per-symbol labels are sleeve names, editable via
# the risk-input `factor` field. Shock = the proxy ETF's worst single day since 1999
# (yfinance adjusted closes, computed 2026-09-25); proxy_vol_pct = 2-year realized vol.
# It models an overnight gap where stops cannot fill, not a forecast. Caps only prompt.
# Design: per-trade cap 2R is not a per-cluster cap; buckets 4R, macro separate.
FACTOR_BUCKETS = {
    "科技": {"cap_r": 4, "note": "软件、硬件、M7 合并计上限；三者在真崩盘时一起跌"},
    "非科技": {"cap_r": 4, "note": "金融/能源最差单日比 QQQ 还深，不是防御"},
    "黄金": {"cap_r": 2, "note": "宏观，单独计"},
    "利率": {"cap_r": 2, "note": "宏观，单独计"},
    "宽基": {"cap_r": 3, "note": "大盘因子本身；全组合最坏情况看大盘情景"},
}
FACTOR_CLUSTERS = {
    "软件": {"bucket": "科技", "proxy": "IGV", "shock": -.118, "proxy_vol_pct": 29.5,
             "shock_source": "IGV 最差单日 −11.8%（2020-03-16）；2026 年与半导体相关仅 0.2"},
    "硬件": {"bucket": "科技", "proxy": "SMH", "shock": -.144, "proxy_vol_pct": 38.5,
             "shock_source": "SMH 最差单日 −14.4%（2020-03-16）；半导体、存储与硬件"},
    "M7": {"bucket": "科技", "proxy": "QQQ", "shock": -.120, "proxy_vol_pct": 21.7,
           "shock_source": "QQQ 最差单日 −12.0%（2020-03-16）；MAGS 历史太短，用 QQQ 代理"},
    "金融": {"bucket": "非科技", "proxy": "XLF", "shock": -.167, "proxy_vol_pct": 17.5,
             "shock_source": "XLF 最差单日 −16.7%（2008-12-01）"},
    "消费": {"bucket": "非科技", "proxy": "XLP", "shock": -.094, "proxy_vol_pct": 13.9,
             "shock_source": "XLP 最差单日 −9.4%（2020-03-12）；可选消费无 XLP 行情时按波动率比放大"},
    "医疗": {"bucket": "非科技", "proxy": "XLV", "shock": -.099, "proxy_vol_pct": 16.4,
             "shock_source": "XLV 最差单日 −9.9%"},
    "能源": {"bucket": "非科技", "proxy": "XLE", "shock": -.201, "proxy_vol_pct": 23.6,
             "shock_source": "XLE 最差单日 −20.1%（2020-03-09）"},
    "工业": {"bucket": "非科技", "proxy": "XLI", "shock": -.113, "proxy_vol_pct": 18.1,
             "shock_source": "XLI 最差单日 −11.3%（2020-03-16）"},
    "黄金": {"bucket": "黄金", "proxy": "GLD", "shock": -.103, "proxy_vol_pct": 24.3,
             "shock_source": "GLD 最差单日 −10.3%"},
    "利率": {"bucket": "利率", "proxy": "TLT", "shock": -.067, "proxy_vol_pct": 11.6,
             "shock_source": "TLT 最差单日 −6.7%（2020-03-17）"},
    "宽基": {"bucket": "宽基", "proxy": "SPY", "shock": -.109, "proxy_vol_pct": 16.4,
             "shock_source": "SPY 最差单日 −10.9%（2020-03-16）"},
}
# Labels used before the 2026-09-25 regrouping still resolve.
FACTOR_ALIASES = {"科技": "硬件", "半导体": "硬件", "防御": "消费", "可选消费": "消费", "必需消费": "消费"}
FACTOR_DEFAULTS = {
    **{s: "金融" for s in ("XLF", "KRE", "GS", "MS", "JPM", "BAC", "WFC", "C", "SCHW", "BRK.B", "V", "MA")},
    **{s: "M7" for s in ("AAPL", "MSFT", "NVDA", "GOOG", "GOOGL", "META", "AMZN", "TSLA",
                         "QQQ", "QQQM", "XLK", "VUG")},
    **{s: "硬件" for s in ("AMD", "ARM", "AVGO", "TSM", "SMH", "TXN", "WDC", "INTC", "MU", "SNDK", "DRAM")},
    **{s: "软件" for s in ("IGV", "NOW", "CRM", "ADBE", "BUG", "ORCL", "PLTR", "NFLX")},
    **{s: "医疗" for s in ("XLV", "LLY", "UNH", "JNJ", "ABBV")},
    **{s: "消费" for s in ("XLP", "WMT", "COST", "MO", "PG", "KO", "PEP", "WM", "XLU",
                           "XLY", "HD", "MCD", "NKE")},
    **{s: "工业" for s in ("XLI", "CAT", "GE", "RTX", "DAL")},
    **{s: "能源" for s in ("XLE", "XOM", "CVX", "PBR")},
    **{s: "宽基" for s in ("SPY", "VOO", "IVV", "RSP", "IWM", "DIA", "VTI", "SCHD")},
    **{s: "黄金" for s in ("GLD", "IAU")},
    "TLT": "利率",
}
# Portfolio heat: loss if every stop hits at once, from current prices. Prompt only.
HEAT_CAP_R = 8
# R budget: keep a core of 3-5R working; options expiring within two weeks get their own
# small allowance and do not count toward the core, so a few expiries cannot empty the book overnight.
R_TARGET_BAND = (3.0, 5.0)
SHORT_DTE_DAYS = 14
SHORT_BUCKET_CAP_R, SHORT_SINGLE_CAP_R = .5, .2
LOTTERY_DELTA = .30            # long leg below this |Delta| = out-of-the-money lottery
LOTTERY_BUCKET_CAP_R, LOTTERY_SINGLE_CAP_R = .2, .1
EXPIRY_WARN_DAYS = 7
EXEMPT_MARK = "免判据"  # in a stock group name: the user chose no stop by design (cash parking, long-hold baskets)
UNCLUSTERED = "未分簇"
PAIR_LINKED, PAIR_WEAK = .7, .3


def _round(value, digits=6):
    return round(value, digits) if finite(value) else None


def _asof_day(value):
    if not isinstance(value, str):
        return None
    try:
        if len(value) == 10:
            return date.fromisoformat(value)
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(ET).date() if parsed.tzinfo else parsed.date()
    except ValueError:
        return None


def _freshness(value):
    day = _asof_day(value)
    if day is None:
        return "时间未知"
    age = (datetime.now(ET).date() - day).days
    if age < 0:
        return "时间在未来"
    if age > 4:
        return "已超过 4 天"
    return None


def _bars(points):
    """Reject malformed series, including duplicate dates and unsorted data."""
    if not isinstance(points, list):
        return []
    out, previous = [], ""
    for p in points:
        if not isinstance(p, dict):
            return []
        day, close = p.get("day"), p.get("close")
        try:
            date.fromisoformat(day)
        except (TypeError, ValueError):
            return []
        if day <= previous or not finite(close) or close <= 0:
            return []
        out.append({"day": day, "close": float(close)})
        previous = day
    return out


def _returns(bars):
    # Match both endpoints so a multi-day suspension cannot masquerade as 1 day.
    return {(a["day"], b["day"]): b["close"] / a["close"] - 1
            for a, b in zip(bars, bars[1:])}


def estimate_beta(bars, benchmark_bars, minimum=60, window=252):
    """OLS beta with intercept, using common daily-return date pairs."""
    stock, market = _returns(_bars(bars)), _returns(_bars(benchmark_bars))
    keys = sorted(stock.keys() & market.keys())[-window:]
    result = {"beta": None, "samples": len(keys), "start": keys[0][0] if keys else None,
              "end": keys[-1][1] if keys else None, "correlation": None,
              "r_squared": None, "annualized_vol_pct": None, "reason": None}
    if len(keys) < minimum:
        result["reason"] = f"共同日收益不足 {minimum} 个（现有 {len(keys)}）"
        return result
    x, y = [market[k] for k in keys], [stock[k] for k in keys]
    mx, my = sum(x) / len(x), sum(y) / len(y)
    xx = sum((v - mx) ** 2 for v in x)
    yy = sum((v - my) ** 2 for v in y)
    xy = sum((a - mx) * (b - my) for a, b in zip(x, y))
    result["annualized_vol_pct"] = _round(math.sqrt(yy / (len(y) - 1) * 252) * 100)
    if xx <= 1e-20:
        result["reason"] = "基准收益无足够方差，无法回归 Beta"
        return result
    result["beta"] = _round(xy / xx)
    if yy > 1e-20:
        correlation = max(-1, min(1, xy / math.sqrt(xx * yy)))
        result.update(correlation=_round(correlation), r_squared=_round(correlation ** 2))
    return result


def _factor(symbol, explicit, sector):
    if explicit:
        return FACTOR_ALIASES.get(str(explicit), str(explicit))
    if symbol in FACTOR_DEFAULTS:
        return FACTOR_DEFAULTS[symbol]
    return "M7" if sector in ("信息技术", "通信服务") else UNCLUSTERED


def _correlation(a, b, minimum=60, window=252):
    keys = sorted(a.keys() & b.keys())[-window:]
    if len(keys) < minimum:
        return None
    x, y = [a[k] for k in keys], [b[k] for k in keys]
    mx, my = sum(x) / len(x), sum(y) / len(y)
    xx = sum((v - mx) ** 2 for v in x)
    yy = sum((v - my) ** 2 for v in y)
    if xx <= 1e-20 or yy <= 1e-20:
        return None
    return max(-1, min(1, sum((p - mx) * (q - my) for p, q in zip(x, y)) / math.sqrt(xx * yy)))


def _benchmark(series):
    candidates = [(key, _bars(series.get(key))) for key in ("SPX", "^GSPC", "$SPX", "SP:SPX")]
    valid = next(((key, bars) for key, bars in candidates if len(bars) >= 61), None)
    if valid:
        key, bars = valid
        return key, bars, False
    spy = _bars(series.get("SPY"))
    if spy:
        return "SPY", spy, True
    return next(((key, bars, False) for key, bars in candidates if bars), (None, [], False))


def _intraday_benchmark(benchmark, quote):
    """Same-moment benchmark level from the holdings snapshot.

    The open calibration prices holdings intraday, while the daily history deliberately
    drops the unfinished bar, so every row failed the same-day check all evening.
    A snapshot SPX level that is newer than the last daily close replaces only the
    benchmark's price and day; Beta is still regressed on completed daily bars.
    """
    if not isinstance(quote, dict) or benchmark["proxy"] or benchmark["symbol"] is None:
        return benchmark
    price, day = quote.get("price"), _asof_day(quote.get("day"))
    if str(quote.get("symbol", "")).upper() != "SPX" or not finite(price) or price <= 0 or day is None:
        return benchmark
    if _freshness(quote.get("day")) or (benchmark["day"] and day <= _asof_day(benchmark["day"])):
        return benchmark
    return dict(benchmark, price=float(price), day=day.isoformat(), intraday=True,
                close_day=benchmark["day"], source=quote.get("source") or "持仓快照 SPX 点位",
                asof=quote.get("asof"))


def analyze_exposure(positions, history, nlv, metadata=None, benchmark_quote=None):
    """Analyze normalized positions with optional per-symbol classification overrides.

    `delta` is per-unit LONG option Delta (puts negative); quantity and position
    side are applied exactly once. Option `mark` is never an underlying price.
    Summary totals describe only covered rows unless all rows are covered.
    """
    positions, summaries = partition_positions(positions)
    history = history or {}
    cutoff = _asof_day(history.get("day"))
    series = {key: [p for p in _bars(points) if cutoff is None or p["day"] <= cutoff.isoformat()]
              for key, points in (history.get("series") or {}).items()}
    metadata = metadata or {}
    benchmark_symbol, benchmark_bars, proxy = _benchmark(series)
    benchmark_price = benchmark_bars[-1]["close"] if benchmark_bars else None
    benchmark = {"symbol": benchmark_symbol, "label": "SPY 代理" if proxy else "SPX" if benchmark_symbol else "基准未知",
                 "proxy": proxy, "price": benchmark_price,
                 "day": benchmark_bars[-1]["day"] if benchmark_bars else None}
    benchmark = _intraday_benchmark(benchmark, benchmark_quote)
    benchmark_price = benchmark["price"]
    rows, excluded, beta_cache = [], [], {}
    for index, p in enumerate(positions):
        symbol = str(p.get("underlying") or p.get("sym") or p.get("symbol") or "?").upper()
        bars = _bars(series.get(symbol))
        meta = dict(DEFAULT_METADATA.get(symbol, {}))
        if isinstance(metadata.get(symbol), dict):
            meta.update(metadata[symbol])
        row = {"id": str(p.get("id", f"row-{index}")), "symbol": symbol,
               "broker": p.get("broker", ""), "kind": p.get("kind"),
               "position_delta": None, "dollar_delta": None, "nlv_pct": None, "gross_share_pct": None,
               "beta": None, "beta_dollar_delta": None, "beta_samples": 0,
               "beta_start": None, "beta_end": None, "correlation": None, "r_squared": None,
               "annualized_vol_pct": None, "sector": p.get("sector") or meta.get("sector") or "行业未知",
               "theme": p.get("theme") or meta.get("theme") or "主题未知",
               "factor": _factor(symbol, p.get("factor") or meta.get("factor"), p.get("sector") or meta.get("sector")),
               "delta_source": p.get("delta_source"), "delta_asof": p.get("delta_asof"),
               "price_source": None, "price_asof": None, "underlying_price": None,
               "issues": [], "status": "unavailable"}
        qty, side, kind = p.get("qty"), p.get("side"), p.get("kind")
        if p.get("confirm"):
            row["issues"].append("持仓数量或状态待核对")
        elif not finite(qty) or qty <= 0 or side not in ("long", "short"):
            row["issues"].append("持仓数量或方向不完整")
        elif kind == "stock":
            row["position_delta"] = qty * (1 if side == "long" else -1)
            row["delta_source"] = "股票股数"
        elif kind == "option":
            delta, mult = p.get("delta"), p.get("mult", 100)
            if not finite(delta) or not -1 <= delta <= 1:
                row["issues"].append("缺少有效的每份期权 Delta（不得当作零）")
            elif not finite(mult) or mult <= 0:
                row["issues"].append("期权合约乘数不完整")
            elif str(p.get("right") or p.get("call_put") or "").upper() in ("P", "PUT") and delta > 0:
                row["issues"].append("Put 的 long-contract Delta 应为负值，需核对符号口径")
            elif str(p.get("right") or p.get("call_put") or "").upper() in ("C", "CALL") and delta < 0:
                row["issues"].append("Call 的 long-contract Delta 应为正值，需核对符号口径")
            else:
                row["position_delta"] = qty * mult * delta * (1 if side == "long" else -1)
        else:
            row["issues"].append("资产类型暂不支持 Delta 换算")
        delta_issue = _freshness(row["delta_asof"]) if kind == "option" else None
        if kind == "option" and row["position_delta"] is not None and delta_issue:
            row["issues"].append("期权 Delta " + delta_issue + "，未纳入当前敞口")
        price = p.get("underlying_price")
        supplied_asof = p.get("underlying_price_asof") or p.get("price_asof")
        supplied_day = _asof_day(supplied_asof)
        newer_history = bool(bars and supplied_day and _asof_day(bars[-1]["day"]) > supplied_day)
        if finite(price) and price > 0 and _freshness(supplied_asof) is None and not newer_history:
            row.update(price_source=p.get("underlying_price_source") or "持仓行情",
                       price_asof=supplied_asof)
        elif bars:
            price = bars[-1]["close"]
            row.update(price_source="日线收盘", price_asof=bars[-1]["day"])
        elif kind == "stock" and finite(p.get("mark")) and p["mark"] > 0:
            price = p["mark"]
            row.update(price_source="股票持仓市价", price_asof=p.get("mark_asof"))
        else:
            price = None
            row["issues"].append("缺少标的价格")
        row["underlying_price"] = price
        price_issue = _freshness(row["price_asof"]) if price is not None else None
        if price_issue:
            row["issues"].append("标的价格 " + price_issue + "，未纳入当前敞口")
        dates_mismatch = (kind == "option" and _asof_day(row["delta_asof"]) is not None
                          and _asof_day(row["price_asof"]) is not None
                          and _asof_day(row["delta_asof"]) != _asof_day(row["price_asof"]))
        if dates_mismatch:
            row["issues"].append("Delta 与标的价格不在同一天，未纳入当前敞口")
        if p.get("currency", "USD") != "USD":
            row["issues"].append("非美元持仓缺少统一汇率，未纳入美元敞口")
        elif (row["position_delta"] is not None and price is not None
              and not delta_issue and not price_issue and not dates_mismatch):
            row["dollar_delta"] = row["position_delta"] * price
            row["nlv_pct"] = row["dollar_delta"] / nlv * 100 if finite(nlv) and nlv > 0 else None
        if symbol not in beta_cache:
            beta_cache[symbol] = estimate_beta(bars, benchmark_bars)
        fit = beta_cache[symbol]
        row.update(beta=fit["beta"], beta_samples=fit["samples"], beta_start=fit["start"],
                   beta_end=fit["end"], correlation=fit["correlation"], r_squared=fit["r_squared"],
                   annualized_vol_pct=fit["annualized_vol_pct"])
        if fit["reason"]:
            row["issues"].append(fit["reason"])
        beta_freshness = _freshness(fit["end"])
        if fit["beta"] is not None and beta_freshness:
            row["issues"].append("Beta 样本 " + beta_freshness + "，仅供历史参考")
        benchmark_freshness = _freshness(benchmark["day"])
        benchmark_date_matches = (_asof_day(row["price_asof"]) is not None
                                  and _asof_day(row["price_asof"]) == _asof_day(benchmark["day"]))
        if row["dollar_delta"] is not None and benchmark["day"] and not benchmark_date_matches:
            row["issues"].append("标的价格与基准价格不在同一天，保留该日 Dollar Delta，未纳入当前 Beta 加权汇总")
        if (row["dollar_delta"] is not None and row["beta"] is not None
                and not beta_freshness and not benchmark_freshness and benchmark_date_matches):
            row["beta_dollar_delta"] = row["dollar_delta"] * row["beta"]
        row["status"] = "available" if row["beta_dollar_delta"] is not None else "partial" if row["dollar_delta"] is not None else "unavailable"
        if row["dollar_delta"] is None or row["beta_dollar_delta"] is None:
            excluded.append({"id": row["id"], "symbol": symbol, "reason": "；".join(row["issues"])})
        rows.append(row)

    delta_rows = [r for r in rows if r["dollar_delta"] is not None]
    beta_rows = [r for r in rows if r["beta_dollar_delta"] is not None]
    net = sum(r["dollar_delta"] for r in delta_rows) if delta_rows else None
    gross = sum(abs(r["dollar_delta"]) for r in delta_rows) if delta_rows else None
    beta_net = sum(r["beta_dollar_delta"] for r in beta_rows) if beta_rows else None
    beta_gross = sum(abs(r["beta_dollar_delta"]) for r in beta_rows) if beta_rows else None
    delta_complete = bool(rows) and len(delta_rows) == len(rows)
    beta_complete = bool(rows) and len(beta_rows) == len(rows)
    concentrations = {}
    for row in rows:
        if row["dollar_delta"] is None:
            continue
        row["gross_share_pct"] = abs(row["dollar_delta"]) / gross * 100 if gross else 0
        for kind in ("sector", "theme"):
            label = row[kind]
            key = (kind, label)
            group = concentrations.setdefault(key, {"kind": kind, "label": label, "net_dollar_delta": 0,
                                                       "gross_dollar_delta": 0, "gross_share_pct": None})
            group["net_dollar_delta"] += row["dollar_delta"]
            group["gross_dollar_delta"] += abs(row["dollar_delta"])
    for group in concentrations.values():
        group["gross_share_pct"] = group["gross_dollar_delta"] / gross * 100 if gross else 0
    benchmark_delta = beta_net / benchmark_price if beta_net is not None and benchmark_price else None
    summary = {"net_dollar_delta": net, "gross_dollar_delta": gross,
               "net_nlv_pct": net / nlv * 100 if net is not None and finite(nlv) and nlv > 0 else None,
               "gross_nlv_pct": gross / nlv * 100 if gross is not None and finite(nlv) and nlv > 0 else None,
               "beta_net_dollar_delta": beta_net, "beta_gross_dollar_delta": beta_gross,
               "benchmark_delta": benchmark_delta, "spx_delta": benchmark_delta if not proxy else None,
               "benchmark_1pct_pnl": beta_net * .01 if beta_net is not None else None,
               "benchmark_down1pct_pnl": -beta_net * .01 if beta_net is not None else None,
               "scope": "portfolio" if beta_complete and delta_complete else "covered_subset" if delta_rows else "unavailable"}
    notices = ["Dollar Delta 是方向性敞口，不是资金占用；跨标的用美元敞口汇总，不把股数直接相加。",
               "Beta 是市场敏感度，不等于个股波动；请结合相关性、R² 和年化波动率。",
               "±1% 是当前 Delta 的线性市场情景，不是 VaR、最大亏损或收益预测；未反映 Gamma、Vega 和跳空。",
               "行业/主题为可编辑的示例标签，仅用于集中风险提示，不限制投机仓位；ETF 未获得成分权重时不声称已穿透。",
               "持仓、Delta 与价格可能来自不同时间；以下保留各行来源和时间，缺失时间不可视作实时行情。"]
    if proxy:
        notices.append("缺少足够的真实 SPX 日线，使用 SPY 收益代理；等效 Delta 单位为 SPY 股，不是 SPX 点，不做固定十倍换算。")
    if not delta_complete or not beta_complete:
        notices.append("覆盖不完整：合计和集中比例仅代表已覆盖持仓，缺失项不是零风险。")
    if not benchmark_bars:
        notices.append("无可用 SPX/SPY 基准历史，市场加权风险暂不可用。")
    if benchmark.get("intraday"):
        notices.append(f"基准用持仓快照里同一时刻的 SPX 点位（{benchmark['day']}，盘中），"
                       f"Beta 仍按截至 {benchmark['close_day']} 的完整日线回归。")
    for row in rows:
        for key, value in row.items():
            if isinstance(value, float):
                row[key] = _round(value)
    status = "available" if beta_complete else "partial" if delta_rows else "unavailable"
    if _freshness(history.get("day")):
        status = "stale"
        notices.append("行情历史日期未知、在未来或超过 4 天，不能视作当前风险。")
    return {"status": status, "summary": {k: _round(v) if isinstance(v, float) else v for k, v in summary.items()},
            "benchmark": benchmark, "coverage": {"total": len(rows), "delta_covered": len(delta_rows),
                "beta_covered": len(beta_rows), "delta_complete": delta_complete,
                "beta_complete": beta_complete, "excluded": excluded},
            "rows": rows, "display_summaries": summaries,
            "concentrations": sorted(concentrations.values(), key=lambda g: -g["gross_dollar_delta"]),
            "notices": notices, "market_day": history.get("day"),
            "method": "同日期对日收益 OLS（含截距）· 最近最多 252 个共同样本 / 最少 60 个 · 年化波动率 = 样本标准差 × √252"}


LOW_R_SQUARED = .3
SCENARIO_MOVES = (("up1", .01), ("down5", -.05), ("down10", -.10))
R_UNAVAILABLE = "未确认净值，无法折算 R"


def standard_r(policy):
    """Standard 1R = confirmed sizing NLV x global risk pct, floored like Store._size.

    Returns None when the sizing NLV has not been confirmed; nothing is assumed.
    """
    policy = policy or {}
    nlv, pct = policy.get("nlv"), policy.get("risk_pct")
    if not finite(nlv) or nlv <= 0 or not finite(pct) or pct <= 0:
        return None
    return math.floor(nlv * pct + 1e-9) / 100


def market_scenario(exposure, snapshot_nlv, policy):
    """Read the Beta-weighted net exposure as linear index scenarios in dollars and R.

    Uses only rows that already carry both Dollar Delta and a current Beta
    (`beta_dollar_delta`). Rows without either are listed, never counted as zero.
    Exposure percentages use the account snapshot NLV; R uses the global sizing 1R.
    No size thresholds or good/bad judgements are applied.
    """
    rows = exposure.get("rows") or []
    benchmark = exposure.get("benchmark") or {}
    label = benchmark.get("label") or "基准未知"
    index = "SPY" if benchmark.get("proxy") else "SPX"
    used = [r for r in rows if r.get("beta_dollar_delta") is not None]
    excluded = []
    for r in rows:
        if r.get("beta_dollar_delta") is not None:
            continue
        missing = "缺 Delta 美元" if r.get("dollar_delta") is None else "缺当前 Beta 加权"
        excluded.append({"id": r.get("id"), "symbol": r.get("symbol"), "broker": r.get("broker", ""),
                         "missing": missing, "reason": "；".join(r.get("issues") or []) or missing})
    beta_net = sum(r["beta_dollar_delta"] for r in used) if used else None
    gross = sum(abs(r["dollar_delta"]) for r in used) if used else None
    has_nlv = finite(snapshot_nlv) and snapshot_nlv > 0
    one_r = standard_r(policy)
    scenarios = []
    for key, move in SCENARIO_MOVES:
        pnl = beta_net * move if beta_net is not None else None
        scenarios.append({"key": key, "move_pct": _round(move * 100), "label": f"{label} {move * 100:+.0f}%",
                          "pnl": _round(pnl, 2),
                          "r": _round(pnl / one_r, 4) if pnl is not None and one_r else None,
                          "r_note": None if one_r else R_UNAVAILABLE})
    low_r2 = [{"id": r.get("id"), "symbol": r.get("symbol"), "broker": r.get("broker", ""),
               "r_squared": r.get("r_squared"), "beta": r.get("beta"),
               "annualized_vol_pct": r.get("annualized_vol_pct"), "dollar_delta": r.get("dollar_delta")}
              for r in used if r.get("r_squared") is not None and r["r_squared"] < LOW_R_SQUARED]
    scope = "empty" if not rows else "unavailable" if not used else "portfolio" if len(used) == len(rows) else "covered_subset"
    notices = [f"线性近似：{label} 变动 × Σ(Delta美元 × Beta)。不含 Gamma/Vega/跳空/指派，不是最大损失或 VaR。",
               f"约 100% ≈ 满仓持有 {index}（Beta 加权净敞口 ÷ 账户快照净值）；这是中性参照，不是上限或建议。",
               "占比分母是账户快照净值；R 的分母是全局定仓 1R（确认的定仓净值 × 标准风险比例），两者不同。",
               "−5%/−10% 等较大波动下期权 Delta 会明显变化，线性估算误差更大。"]
    if low_r2:
        notices.append(f"R² < {LOW_R_SQUARED:g} 的标的大盘解释力有限，Beta 加权会低估这些标的的自身波动。")
    if scope == "covered_subset":
        notices.append("仅覆盖子集：缺 Delta 或缺 Beta 的仓位不参与计算，也不当作零风险。")
    if scope == "empty":
        notices.append("当前没有持仓行，无可估算的敞口。")
    if one_r is None:
        notices.append(R_UNAVAILABLE + "：请先在全局风险设置中确认定仓净值。")
    if exposure.get("status") == "stale" or exposure.get("snapshot_status") not in (None, "available"):
        notices.append("敞口基于过期或不可用的快照/行情，仅供历史参考。")
    return {"benchmark_label": label, "proxy": bool(benchmark.get("proxy")), "scope": scope,
            "coverage": {"used": len(used), "total": len(rows), "subset": scope == "covered_subset",
                         "excluded": excluded},
            "beta_net_dollar_delta": _round(beta_net, 2),
            "beta_net_nlv_pct": _round(beta_net / snapshot_nlv * 100, 4) if beta_net is not None and has_nlv else None,
            "gross_dollar_delta": _round(gross, 2),
            "gross_nlv_pct": _round(gross / snapshot_nlv * 100, 4) if gross is not None and has_nlv else None,
            "snapshot_nlv": snapshot_nlv if has_nlv else None,
            "one_r": one_r, "sizing_nlv": (policy or {}).get("nlv"), "risk_pct": (policy or {}).get("risk_pct"),
            "r_note": None if one_r else R_UNAVAILABLE,
            "scenarios": scenarios, "low_r_squared": low_r2, "low_r_squared_threshold": LOW_R_SQUARED,
            "notices": notices}


def _sensitivity(row, bars, proxy, proxy_bars, config):
    """Sensitivity of one row to its cluster proxy: OLS beta when both series exist,
    else the volatility ratio (an upper bound on beta, since beta = rho * vol ratio)."""
    if row["symbol"] == proxy:
        return 1.0, "代理本身"
    if bars and proxy_bars:
        fit = estimate_beta(bars, proxy_bars)
        if fit["beta"] is not None:
            return max(0.0, min(3.0, fit["beta"])), f"对 {proxy} 回归 β（{fit['samples']} 个样本）"
    vol, proxy_vol = row.get("annualized_vol_pct"), config.get("proxy_vol_pct")
    if finite(vol) and vol > 0 and finite(proxy_vol) and proxy_vol > 0:
        return max(.5, min(3.0, vol / proxy_vol)), f"波动率比 {vol:.1f}%/{proxy_vol:.1f}%（β 上界，偏保守）"
    return 1.0, "缺行情，按 1 倍计"


def factor_clusters(exposure, history, snapshot_nlv, policy, clusters=None, buckets=None):
    """Group rows into sleeves, sleeves into buckets, and read each bucket's gap stress in R.

    Stress = sum(Dollar Delta x sleeve shock x sensitivity), summed linearly across a
    bucket's sleeves (they fall together in a real crash). Option gamma and stop slippage
    beyond the gap are not modelled. Caps sit on buckets only and never block a plan.
    Rows without Dollar Delta are listed as uncovered, not zero.
    """
    clusters = clusters or FACTOR_CLUSTERS
    buckets = buckets or FACTOR_BUCKETS
    history = history or {}
    cutoff = _asof_day(history.get("day"))
    series = {key.upper(): [p for p in _bars(points) if cutoff is None or p["day"] <= cutoff.isoformat()]
              for key, points in (history.get("series") or {}).items()}
    one_r = standard_r(policy)
    has_nlv = finite(snapshot_nlv) and snapshot_nlv > 0
    groups, sleeves = {}, {}
    for row in exposure.get("rows") or []:
        label = row.get("factor") or UNCLUSTERED
        config = clusters.get(label, {})
        name = config.get("bucket") or label
        bucket = buckets.get(name, {}) if config else {}
        group = groups.setdefault(name, {"factor": name, "note": bucket.get("note"), "cap_r": bucket.get("cap_r"),
                                         "cap_base_r": bucket.get("cap_base_r", bucket.get("cap_r")),
                                         "members": [], "uncovered": [], "net_dollar_delta": 0.0,
                                         "gross_dollar_delta": 0.0, "stress_pnl": 0.0 if config else None,
                                         "sleeves": []})
        sleeve = None
        if config:
            sleeve = sleeves.get(label)
            if sleeve is None:
                sleeve = sleeves[label] = {"name": label, "proxy": config["proxy"],
                                           "shock_pct": _round(config["shock"] * 100, 2),
                                           "shock_source": config.get("shock_source"), "stress_pnl": 0.0,
                                           "net_dollar_delta": 0.0, "members": 0, "uncovered": 0}
                group["sleeves"].append(sleeve)
        if row.get("dollar_delta") is None:
            group["uncovered"].append({"symbol": row["symbol"], "broker": row.get("broker", ""), "sleeve": label,
                                       "reason": "；".join(row.get("issues") or []) or "缺 Delta 美元"})
            if sleeve:
                sleeve["uncovered"] += 1
            continue
        member = {"id": row["id"], "symbol": row["symbol"], "broker": row.get("broker", ""), "sleeve": label,
                  "dollar_delta": row["dollar_delta"], "sensitivity": None, "sensitivity_method": None,
                  "stress_pnl": None}
        if config:
            k, method = _sensitivity(row, series.get(row["symbol"]), config["proxy"],
                                     series.get(config["proxy"]), config)
            pnl = row["dollar_delta"] * config["shock"] * k
            member.update(sensitivity=_round(k, 3), sensitivity_method=method, stress_pnl=_round(pnl, 2))
            group["stress_pnl"] += pnl
            sleeve["stress_pnl"] += pnl
            sleeve["net_dollar_delta"] += row["dollar_delta"]
            sleeve["members"] += 1
        group["members"].append(member)
        group["net_dollar_delta"] += row["dollar_delta"]
        group["gross_dollar_delta"] += abs(row["dollar_delta"])
    result = []
    for group in groups.values():
        stress, cap = group["stress_pnl"], group["cap_r"]
        loss_r = -stress / one_r if stress is not None and one_r else None
        headroom_r = max(0.0, cap - loss_r) if loss_r is not None and cap else None
        excess_r = max(0.0, loss_r - cap) if loss_r is not None and cap else None
        for sleeve in group["sleeves"]:
            # Over the (light-scaled) cap: proxy short Delta that would bring the bucket back under,
            # if all of the fix came from this sleeve. Cut size or buy protection; PA picks the moment.
            sleeve["hedge_proxy_dollars"] = (_round(excess_r * one_r / abs(sleeve["shock_pct"] / 100), 0)
                                             if excess_r and sleeve["shock_pct"] and sleeve["stress_pnl"] < 0 else None)
            # Extra proxy notional (sensitivity 1) of this sleeve that fits under the bucket cap.
            sleeve.update(stress_pnl=_round(sleeve["stress_pnl"], 2),
                          stress_loss_r=_round(-sleeve["stress_pnl"] / one_r, 3) if one_r else None,
                          net_dollar_delta=_round(sleeve["net_dollar_delta"], 2),
                          headroom_proxy_dollars=_round(headroom_r * one_r / abs(sleeve["shock_pct"] / 100), 0)
                          if headroom_r is not None and sleeve["shock_pct"] else None)
        group["sleeves"].sort(key=lambda x: x["stress_pnl"])
        status = ("unclustered" if stress is None else "r_unavailable" if loss_r is None
                  else "over" if cap and loss_r >= cap else "near" if cap and loss_r >= .75 * cap else "ok")
        group.update(net_dollar_delta=_round(group["net_dollar_delta"], 2),
                     gross_dollar_delta=_round(group["gross_dollar_delta"], 2),
                     net_nlv_pct=_round(group["net_dollar_delta"] / snapshot_nlv * 100, 2) if has_nlv else None,
                     stress_pnl=_round(stress, 2) if stress is not None else None,
                     stress_loss_r=_round(loss_r, 3), headroom_r=_round(headroom_r, 3),
                     excess_r=_round(excess_r, 3) if excess_r else None, status=status)
        group["members"].sort(key=lambda m: -abs(m["dollar_delta"]))
        result.append(group)
    order = {"over": 0, "near": 1, "ok": 2, "r_unavailable": 3, "unclustered": 4}
    result.sort(key=lambda g: (order[g["status"]], g["stress_pnl"] if g["stress_pnl"] is not None else 0))

    # Pairwise daily-return correlation checks whether the labels match co-movement:
    # high correlation across buckets, or low correlation inside one sleeve.
    covered = {}
    for group in result:
        for m in group["members"]:
            covered.setdefault(m["symbol"], (group["factor"], m["sleeve"]))
    returns = {s: _returns(series[s]) for s in covered if series.get(s)}
    linked, weak = [], []
    symbols = sorted(returns)
    for i, a in enumerate(symbols):
        for b in symbols[i + 1:]:
            rho = _correlation(returns[a], returns[b])
            if rho is None:
                continue
            (bucket_a, sleeve_a), (bucket_b, sleeve_b) = covered[a], covered[b]
            pair = {"a": a, "b": b, "factor_a": bucket_a, "factor_b": bucket_b,
                    "sleeve_a": sleeve_a, "sleeve_b": sleeve_b, "correlation": _round(rho, 3)}
            if bucket_a != bucket_b and rho >= PAIR_LINKED:
                linked.append(pair)
            elif sleeve_a == sleeve_b and sleeve_a in clusters and rho < PAIR_WEAK:
                weak.append(pair)
    linked.sort(key=lambda p: -p["correlation"])
    weak.sort(key=lambda p: p["correlation"])
    notices = ["两层结构：大类（科技 / 非科技 / 黄金 / 利率 / 宽基）设上限；子项（软件、硬件、M7、金融、消费……）"
               "各用自己的代理与冲击，只展示不单独设上限。标签可在“补充风险数据”里逐个改（填子项名）。",
               "压力情景 = 子项代理 ETF 1999 年以来最差单日跌幅，模拟隔夜跳空、止损来不及成交；不是预测，也不是最大亏损。"
               "同一大类内各子项线性相加（真崩盘时一起跌）。",
               "敏感度优先用对代理 ETF 的日收益回归 β；缺代理行情时用波动率比（β 的上界，偏保守）。",
               "上限只做提示，不阻止开仓；R 的分母是全局定仓 1R。单笔 2R 是单笔止损风险上限，与这里的大类跳空上限是两道闸。",
               "各大类在系统性暴跌时会一起跌，大类压力不能相加当作全组合最坏情况；全组合请看大盘情景。"]
    if one_r is None:
        notices.append(R_UNAVAILABLE + "：请先在全局风险设置中确认定仓净值。")
    if any(g["uncovered"] for g in result):
        notices.append("有仓位缺 Delta 美元，未计入所在大类的压力，不当作零风险。")
    return {"clusters": result, "one_r": one_r, "linked_pairs": linked[:12], "weak_pairs": weak[:12],
            "pair_thresholds": {"linked": PAIR_LINKED, "weak": PAIR_WEAK}, "notices": notices}


def _first_stop(position):
    """The stop that triggers first: highest level for long `<=` stops, lowest for `>=`."""
    stops = [t for t in position.get("trigs") or []
             if isinstance(t, dict) and t.get("kind") == "止损" and finite(t.get("level"))
             and t.get("basis") in ("und", "mark") and t.get("dir") in ("<=", ">=")]
    if not stops:
        return None
    return max(stops, key=lambda t: t["level"] if t["dir"] == "<=" else -t["level"])


def stop_vs_cost(position, stop, price):
    """Where a stock stop sits against the cost basis, apart from the heat (which is measured from the current price).

    cost_result_usd: result against cost if the stop fires (negative = loss, positive = profit already locked).
    initial_risk_usd: (cost - first stop) x qty. The first stop is the denominator for every multiple and never moves when
    the stop is trailed. It exists only when that first stop sat on the losing side of the cost.
    """
    opened, qty = position.get("open"), position.get("qty")
    if not (finite(opened) and opened > 0 and finite(qty) and qty > 0):
        return {}
    sign = 1 if position.get("side") == "long" else -1
    out = {"cost_result_usd": round(sign * (stop["level"] - opened) * qty, 2),
           "moves": len(stop.get("moves") or [])}
    level0 = stop.get("level0")
    if finite(level0):
        out["level0"] = level0
        risk0 = sign * (opened - level0) * qty
        if risk0 > 0:
            out["initial_risk_usd"] = round(risk0, 2)
            out["stop_multiple"] = _round(out["cost_result_usd"] / risk0, 2)
            now = price if finite(price) else position.get("mark")
            if finite(now):
                out["open_multiple"] = _round(sign * (now - opened) * qty / risk0, 2)
    return out


def _mark_value(position):
    mark, qty, mult = position.get("mark"), position.get("qty"), position.get("mult", 100)
    if not (finite(mark) and finite(qty) and finite(mult)):
        return None
    return mark * qty * mult * (1 if position.get("side") == "long" else -1)


def option_expiry_bound(legs):
    """Exact terminal payoff bound for standard, same-expiry vanilla option legs.

    Evaluate all breakpoints on S >= 0 and the terminal call slope. This is not
    a bound on interim margin, early assignment, or liquidation costs.
    """
    contracts = []
    for p in legs:
        right = str(p.get("right") or p.get("call_put") or "").upper()
        right = {"CALL": "C", "PUT": "P"}.get(right, right)
        strike, qty, mult, mark = (p.get(k) for k in ("strike", "qty", "mult", "mark"))
        try:
            date.fromisoformat(p.get("expiry"))
        except (ValueError, TypeError):
            return None, "unknown", "缺少有效到期日，无法校验组合收益边界"
        if (right not in ("C", "P") or not finite(strike) or strike <= 0
                or not finite(qty) or qty <= 0 or not finite(mult) or mult <= 0
                or not finite(mark) or mark < 0 or p.get("side") not in ("long", "short")
                or p.get("currency", "USD") != "USD" or p.get("confirm")):
            return None, "unknown", "合约、现价、数量、乘数或币种不完整，无法校验组合收益边界"
        contracts.append((right, strike, qty * mult * (1 if p["side"] == "long" else -1), mark))
    identities = {(p.get("broker"), p.get("account_id") or p.get("account") or p.get("broker_account"),
                   p.get("sym") or p.get("symbol"), p.get("expiry"), p.get("mult")) for p in legs}
    if not contracts or len(identities) != 1:
        return None, "unknown", "跨到期日、标的、账户或乘数组合，不能用单一期末收益确定最大亏损"
    if sum(q for right, _, q, _ in contracts if right == "C") < -1e-8:
        return None, "unbounded", "到期上行尾部净卖出 Call，理论亏损无上限"
    knots = [0.0] + [strike for _, strike, _, _ in contracts]
    minimum = min(sum(q * max(s - k if right == "C" else k - s, 0)
                      for right, k, q, _ in contracts) for s in knots)
    current = sum(q * mark for _, _, q, mark in contracts)
    return round(max(0.0, current - minimum), 2), "bounded", "按同到期合约收益折点计算 · 从当前净值起算；不含提前指派及费用"


def portfolio_heat(positions, exposure, policy, cap_r=HEAT_CAP_R):
    """Total loss if every stop hits at once, measured from current prices, in R.

    Stocks: shares x distance to the stop. Options with an underlying stop: |position
    Delta| x distance, capped at the long leg's current value (linear, so an upper bound
    for long options). Options without a stop: remaining premium of long legs, or the net
    payoff bound of a validated same-expiry group. Stocks without a stop are listed apart:
    they carry gap stress, not stop risk. Short calls with an unbounded upper tail are
    flagged; short puts are evaluated on the nonnegative underlying-price domain.
    """
    positions, summaries = partition_positions(positions)
    one_r = standard_r(policy)
    rows = {r["id"]: r for r in (exposure or {}).get("rows") or []}
    items, no_stop, exempt, unbounded, unknown = [], [], [], [], []
    grouped = {}

    def base(p):
        return {"id": str(p.get("id")), "symbol": str(p.get("sym") or p.get("symbol") or "?").upper(),
                "broker": p.get("broker", ""), "label": p.get("label", "")}

    for p in positions or []:
        entry, row = base(p), rows.get(str(p.get("id")), {})
        kind, side, qty = p.get("kind"), p.get("side"), p.get("qty")
        sign = 1 if side == "long" else -1
        if p.get("confirm") or not finite(qty) or qty <= 0 or side not in ("long", "short"):
            unknown.append(dict(entry, reason="持仓数量或状态待核对"))
            continue
        stop = _first_stop(p)
        if stop:
            if stop["basis"] == "mark":
                mark, mult = p.get("mark"), (p.get("mult", 100) if kind == "option" else 1)
                if not finite(mark):
                    unknown.append(dict(entry, reason="按自身价止损但缺现价"))
                    continue
                loss = max(0.0, (mark - stop["level"]) * sign) * qty * mult
                method = f"自身价 {mark:g} → 止损 {stop['level']:g}"
            else:
                price = row.get("underlying_price")
                if not finite(price) and kind == "stock":
                    price = p.get("mark")
                if not finite(price):
                    unknown.append(dict(entry, reason="按标的止损但缺标的价"))
                    continue
                distance = max(0.0, price - stop["level"]) if stop["dir"] == "<=" else max(0.0, stop["level"] - price)
                if kind == "stock":
                    loss, method = qty * distance, f"{qty:g} 股 × 距止损 {distance:.2f}（{price:g} → {stop['level']:g}）"
                elif kind == "option" and finite(row.get("position_delta")):
                    loss = abs(row["position_delta"]) * distance
                    value = _mark_value(p)
                    method = f"Delta {abs(row['position_delta']):.1f} × 距止损 {distance:.2f}（线性，偏保守）"
                    if side == "long" and value is not None and value < loss:
                        loss, method = value, f"权利金现值封顶（线性估算 {abs(row['position_delta']) * distance:,.0f}）"
                elif kind == "option" and side == "long" and _mark_value(p) is not None:
                    loss, method = _mark_value(p), "缺 Delta，按权利金现值（上限）"
                else:
                    unknown.append(dict(entry, reason="期权缺 Delta，无法估算到止损的亏损"))
                    continue
            if loss == 0:
                entry["flag"] = "现价已在止损之外，需要立即处理"
            if kind == "stock" and stop["basis"] == "und":
                entry.update(stop_vs_cost(p, stop, row.get("underlying_price")))
            items.append(dict(entry, heat_usd=round(loss, 2), method=method, stop=stop.get("text") or stop["level"]))
            continue
        if kind == "stock":
            value = qty * (row.get("underlying_price") if finite(row.get("underlying_price")) else p.get("mark") or 0)
            group = p.get("group") or ""
            # 「免判据」 holdings (cash parking, long-hold baskets) have no stop by design: listed apart, never chased
            (exempt if EXEMPT_MARK in group else no_stop).append(
                dict(entry, value_usd=round(value, 2) if value else None, group=group))
        elif kind == "option" and p.get("group"):
            key = (p.get("broker", ""), p.get("account_id") or p.get("account") or p.get("broker_account") or "",
                   p.get("sym") or p.get("symbol"), p["group"])
            grouped.setdefault(key, []).append(p)
        elif kind == "option" and side == "long" and _mark_value(p) is not None:
            items.append(dict(entry, heat_usd=round(_mark_value(p), 2), method="无止损 · 权利金现值（最多亏完）"))
        elif kind == "option" and side == "short":
            loss, bound, reason = option_expiry_bound([p])
            if bound == "bounded":
                items.append(dict(entry, heat_usd=loss, method=reason))
            else:
                (unbounded if bound == "unbounded" else unknown).append(dict(entry, reason=reason))
        else:
            unknown.append(dict(entry, reason="资产类型或现价不完整"))

    for (broker, account, symbol, group), legs in grouped.items():
        entry = {"id": ",".join(str(p.get("id")) for p in legs), "symbol": str(legs[0].get("sym") or "?").upper(),
                 "broker": broker, "label": group}
        loss, bound, reason = option_expiry_bound(legs)
        if bound != "bounded":
            (unbounded if bound == "unbounded" else unknown).append(dict(entry, reason=reason))
        else:
            items.append(dict(entry, heat_usd=loss, method=reason))

    total = sum(i["heat_usd"] for i in items)
    for i in items:
        i["heat_r"] = _round(i["heat_usd"] / one_r, 3) if one_r else None
    items.sort(key=lambda i: -i["heat_usd"])
    locked = sum(i["cost_result_usd"] for i in items if (i.get("cost_result_usd") or 0) > 0)
    loss_risk = -sum(i["cost_result_usd"] for i in items if (i.get("cost_result_usd") or 0) < 0)
    heat_r = total / one_r if one_r else None
    status = ("r_unavailable" if heat_r is None else "over" if heat_r >= cap_r
              else "partial" if unbounded or unknown else "near" if heat_r >= .75 * cap_r else "ok")
    notices = ["总热度 = 所有止损同时被打掉时的亏损，从当前价起算；这是「满仓」的口径。只提示不拦截。",
               "没有止损的正股不计入热度（它们的风险在跳空压力里），单独列出；标了「免判据」的持仓按你的裁定不设止损，另列一栏。",
               "期权按标的止损用 Delta 线性估算，买方以权利金现值封顶；无止损的买方期权按权利金全亏计。"]
    if one_r is None:
        notices.append(R_UNAVAILABLE + "：请先在全局风险设置中确认定仓净值。")
    if unbounded or unknown:
        notices.append("有仓位无法估算或亏损无上限，未计入热度，不当作零风险。")
    return {"heat_usd": round(total, 2), "heat_r": _round(heat_r, 3), "cap_r": cap_r, "one_r": one_r,
            "headroom_r": _round(max(0.0, cap_r - heat_r), 3) if heat_r is not None and not (unknown or unbounded) else None,
            "status": status, "items": items, "no_stop": sorted(no_stop, key=lambda x: -(x["value_usd"] or 0)),
            "no_stop_usd": round(sum(x["value_usd"] or 0 for x in no_stop), 2),
            "locked_usd": round(locked, 2), "loss_risk_usd": round(loss_risk, 2),
            "exempt": sorted(exempt, key=lambda x: -(x["value_usd"] or 0)),
            "exempt_usd": round(sum(x["value_usd"] or 0 for x in exempt), 2),
            "unbounded": unbounded, "unknown": unknown, "display_summaries": summaries, "notices": notices}


def r_budget(heat, positions, today=None):
    """Split the heat into the core book and the short-dated option allowance, and check both against the
    owner's target band and caps. Prompt only: nothing here blocks an order.

    A stock whose stop already sits at or above its cost is locked and takes 0R. An item is short-dated when
    any option leg expires within SHORT_DTE_DAYS; it is a lottery when its strongest long leg has |Delta| below
    LOTTERY_DELTA now (current Delta, not the Delta at entry).
    """
    today = today or datetime.now(ET).date()
    one_r = (heat or {}).get("one_r")
    by_id = {str(p.get("id")): p for p in positions or []}
    core, short, locked = [], [], []
    for item in (heat or {}).get("items") or []:
        legs = [by_id[i] for i in str(item.get("id", "")).split(",") if i in by_id]
        r = item.get("heat_r") or 0
        sym, label = item.get("symbol") or "", item.get("label") or ""
        row = {"id": item.get("id"), "symbol": sym, "label": label,
               "name": label if sym and sym in label else f"{sym} {label}".strip(),
               "heat_usd": item.get("heat_usd"), "r": _round(r, 3)}
        if (item.get("cost_result_usd") is not None and item["cost_result_usd"] >= 0):
            locked.append(dict(row, r=0, reason="止损已在成本之上，按 0R 计"))
            continue
        days = []
        for p in legs:
            if p.get("kind") != "option":
                continue
            try:
                days.append((date.fromisoformat(p.get("expiry")) - today).days)
            except (TypeError, ValueError):
                pass
        if days and min(days) <= SHORT_DTE_DAYS:
            deltas = [abs(p["delta"]) for p in legs if p.get("side") == "long" and finite(p.get("delta"))]
            short.append(dict(row, dte=min(days), delta=_round(max(deltas), 3) if deltas else None,
                              lottery=bool(deltas) and max(deltas) < LOTTERY_DELTA))
        else:
            core.append(row)
    total = lambda rows: _round(sum(r["r"] or 0 for r in rows), 3)
    core_r, short_r = total(core), total(short)
    lottery = [r for r in short if r["lottery"]]
    lottery_r = total(lottery)
    expiring = [r for r in short if r["dte"] <= EXPIRY_WARN_DAYS]
    low, high = R_TARGET_BAND
    warnings = []
    if one_r:
        if core_r < low:
            warnings.append(f"核心占用 {core_r:.2f}R，低于目标下限 {low:g}R（短期期权不算在内）")
        elif core_r > high:
            warnings.append(f"核心占用 {core_r:.2f}R，高于目标上限 {high:g}R")
        if short_r > SHORT_BUCKET_CAP_R:
            warnings.append(f"两周内到期的期权合计 {short_r:.2f}R，超过额度 {SHORT_BUCKET_CAP_R:g}R")
        warnings += [f"{r['name']} 占 {r['r']:.2f}R，超过短期期权单笔上限 {SHORT_SINGLE_CAP_R:g}R"
                     for r in short if r["r"] > SHORT_SINGLE_CAP_R + 1e-9]
        if lottery_r > LOTTERY_BUCKET_CAP_R:
            warnings.append(f"价外短期期权合计 {lottery_r:.2f}R，超过彩票额度 {LOTTERY_BUCKET_CAP_R:g}R")
        warnings += [f"{r['name']} 是价外短期期权（Delta {r['delta']:.2f}），占 {r['r']:.2f}R，"
                     f"超过彩票单笔上限 {LOTTERY_SINGLE_CAP_R:g}R" for r in lottery if r["r"] > LOTTERY_SINGLE_CAP_R + 1e-9]
    status = ("r_unavailable" if not one_r else "below" if core_r < low else "above" if core_r > high else "ok")
    return {"one_r": one_r, "band_r": [low, high], "core_r": core_r, "short_r": short_r, "lottery_r": lottery_r,
            "expiring_r": total(expiring), "expiring_days": EXPIRY_WARN_DAYS, "status": status,
            "core_gap_r": _round(max(0.0, low - core_r), 3), "core_room_r": _round(max(0.0, high - core_r), 3),
            "short_room_r": _round(max(0.0, SHORT_BUCKET_CAP_R - short_r), 3),
            "lottery_room_r": _round(max(0.0, min(LOTTERY_BUCKET_CAP_R - lottery_r, SHORT_BUCKET_CAP_R - short_r)), 3),
            "short_cap_r": SHORT_BUCKET_CAP_R, "short_single_cap_r": SHORT_SINGLE_CAP_R,
            "lottery_cap_r": LOTTERY_BUCKET_CAP_R, "lottery_single_cap_r": LOTTERY_SINGLE_CAP_R,
            "short_dte_days": SHORT_DTE_DAYS, "lottery_delta": LOTTERY_DELTA,
            "core": sorted(core, key=lambda r: -(r["r"] or 0)), "short": sorted(short, key=lambda r: r["dte"]),
            "locked": locked, "warnings": warnings,
            "notices": [f"核心占用 = 热度里去掉两周内到期的期权、去掉止损已在成本之上的正股；目标 {low:g}–{high:g}R。",
                        f"两周内到期的期权单独一个额度：合计 ≤{SHORT_BUCKET_CAP_R:g}R、单笔 ≤{SHORT_SINGLE_CAP_R:g}R；"
                        f"其中多头腿 Delta 低于 {LOTTERY_DELTA:.2f} 的算彩票：合计 ≤{LOTTERY_BUCKET_CAP_R:g}R、单笔 ≤{LOTTERY_SINGLE_CAP_R:g}R。",
                        "没设止损的正股不在热度里，也不算核心占用。只提示，不拦截。"]}
