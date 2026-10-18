#!/usr/bin/env python3
"""Write Shepherd's optional daily-bars file (market-data.json) from Yahoo Finance's public chart endpoint.

    python3 tools/fetch_market_data.py AAPL MSFT NVDA          # SPY and ^GSPC are always added
    python3 tools/fetch_market_data.py --out /path/to/market-data.json AAPL

The risk page uses these closes for Beta, the -5% market scenario and historical VaR. Closes are regular-session,
split-adjusted, without dividends (what the endpoint calls "close"). Yahoo is not an official data service: it can
rate-limit or change without notice, and its terms apply to what you download. Standard library only.
"""
from __future__ import annotations

import argparse
import json
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

DEFAULT_OUT = Path.home() / "Library/Application Support/Shepherd/TradingDesk/market-data.json"
NAMES = {"^GSPC": "SPX"}  # the app looks for SPX (or SPY) as the market benchmark
ET = ZoneInfo("America/New_York")


def closes(symbol, years):
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(symbol)}"
           f"?range={years}y&interval=1d&includePrePost=false&events=split")
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 shepherd-market-data"})
    with urllib.request.urlopen(request, timeout=20) as response:
        body = json.load(response)
    result = (body.get("chart") or {}).get("result") or []
    if not result:
        raise ValueError((body.get("chart") or {}).get("error") or "no data")
    stamps, quote = result[0].get("timestamp") or [], result[0]["indicators"]["quote"][0]
    bars, last = [], ""
    for t, c in zip(stamps, quote.get("close") or []):
        if c is None or c <= 0:
            continue
        day = datetime.fromtimestamp(t, timezone.utc).astimezone(ET).date().isoformat()
        if day > last:
            bars.append({"day": day, "close": round(float(c), 4)})
            last = day
    return bars


def main():
    parser = argparse.ArgumentParser(description="写 Shepherd 的补充日线文件 market-data.json")
    parser.add_argument("symbols", nargs="*", help="美股代码，例如 AAPL MSFT（SPY 和 ^GSPC 会自动加上）")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--years", type=int, default=2)
    args = parser.parse_args()
    wanted = list(dict.fromkeys([s.upper() for s in args.symbols] + ["SPY", "^GSPC"]))
    series, failed = {}, []
    for symbol in wanted:
        try:
            series[NAMES.get(symbol, symbol)] = closes(symbol, args.years)
            print(f"{symbol:8s} {len(series[NAMES.get(symbol, symbol)])} 根日线")
        except Exception as error:  # report and keep going; a missing symbol is listed, not guessed
            failed.append(symbol)
            print(f"{symbol:8s} 失败：{error}")
        time.sleep(0.4)
    if not series.get("SPX") and not series.get("SPY"):
        raise SystemExit("基准 SPX/SPY 都没取到，没有写文件。")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    body = {"schema": "shepherd-market-data/1", "price_basis": "regular_split_adjusted",
            "source": "Yahoo Finance chart（tools/fetch_market_data.py）",
            "collected_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "series": series}
    temp = args.out.with_suffix(".tmp")
    temp.write_text(json.dumps(body, ensure_ascii=False))
    temp.replace(args.out)
    print(f"已写入 {args.out}" + (f"；没取到：{', '.join(failed)}" if failed else ""))


if __name__ == "__main__":
    main()
