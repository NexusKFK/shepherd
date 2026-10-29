# 数据文件格式

Shepherd 只读这些文件，从不改写它们。每个文件都是可选的：缺了，对应的页面会写明"没有找到"，不会假装正常。

最完整的样例是演示数据。运行下面的命令，再打开 `/tmp/shepherd-demo/sources/` 逐个看：

```sh
python3 -m workbench.demo --out /tmp/shepherd-demo
```

## 文件放在哪

所有路径都相对于一个"数据源目录"。默认是你的主目录 `~`；用环境变量 `SHEPHERD_SOURCE_HOME` 可以换成别的目录。每个模块的文件也可以单独用环境变量指到任意位置。

| 内容 | 默认位置（相对数据源目录） | 单独指定的环境变量 |
|---|---|---|
| 持仓快照 | `tools/holdings_local/state.json` | 用 App 的「导入持仓 JSON」 |
| 日线（主） | `tools/daily_digest/cache/addscore/<日期>.bundle.json` | — |
| 日线（补充，推荐） | App 数据目录里的 `market-data.json` | — |
| 盘前观察 | `tools/daily_digest/cache/addscore/<日期>.editorial.json` | — |
| 宏观日历 | `tools/daily_digest/calendar_data.json` | — |
| 财报日期 | `tools/daily_digest/cache/earnings.json` | — |
| 宏观灯 | `tools/macro_panel/out/state.json` | `SHEPHERD_MACRO_STATE` |
| 周线观察 | `tools/weekly_watch/out/state.json` | `SHEPHERD_WEEKLY_STATE` |
| 纸盘 | `tools/paper_book/out/state.json` | `SHEPHERD_PAPER_STATE` |
| 期权计划卡 | `tools/option_plans/*.json` | `SHEPHERD_OPTION_PLANS`（文件夹） |
| 券商确认单 | `tools/schwab_confirms/inbox/` | `SHEPHERD_ECONF_INBOX`（文件夹） |

目录名沿用了作者自己那套采集脚本的叫法。App 数据目录是 `~/Library/Application Support/Shepherd/TradingDesk/`（账本 `trading.sqlite3` 也在这里）。

通过 Finder 或 Dock 打开的 App 读不到你在终端里设的环境变量。要用环境变量，就从终端启动：

```sh
open -n --env SHEPHERD_SOURCE_HOME=/path/to/sources /Applications/Shepherd.app
```

## 持仓快照

样例：[`examples/holdings-template.json`](../examples/holdings-template.json)。

```json
{
  "asof": "任何说明 ｜ 收盘价 2026-10-16",
  "accounts": [{"id": "券商A", "name": "券商 A 主账户", "nlv": 60000, "cash": 21000}],
  "positions": [ ... ],
  "quotes": {"KO": 68.0},
  "benchmarkQuote": {"symbol": "SPX", "price": 6475.0, "day": "2026-10-16"}
}
```

- `asof` 里要有"收盘价 YYYY-MM-DD"，App 用这个日期判断快照新不新；超过 4 天标成历史资料。
- `accounts[].nlv` 每个账户都要有，App 把它们加起来当快照净值。
- `quotes` 是现价表。账本里建的计划没有快照价时，从这里取价。

每条持仓：

| 字段 | 必填 | 说明 |
|---|---|---|
| `id` | 是 | 在文件内唯一 |
| `sym` | 是 | 代码，例如 `KO` |
| `kind` | 是 | `stock` 或 `option` |
| `side` | 是 | `long` 或 `short`；也可以不写，用带符号的 `qty` 表示方向 |
| `qty` | 是 | 股数或张数 |
| `mult` | 否 | 乘数；期权默认 100，股票默认 1 |
| `open` / `mark` | 是 | 成本价、现价（期权是每股权利金） |
| `broker` | 否 | 账户名，要和 `accounts[].id` 对得上 |
| `label` | 否 | 显示用，例如 `正股 120 股`、`Jun18'27 180C` |
| `group` | 否 | 同一组的多条腿写同一个组名；组名里带"免判据"表示这批持仓按设计不设止损（例如现金停放），不算"无止损"异常 |
| `expiry` / `right` / `strike` | 期权必填 | 到期日 `YYYY-MM-DD`、`C` 或 `P`、行权价 |
| `delta` | 期权建议填 | 每份买方合约的 Delta（Call 正、Put 负），配 `delta_status: "ok"` 和 `delta_asof` |
| `trigs` | 否 | 止损。见下 |

止损写法：

```json
{"kind": "止损", "basis": "und", "level": 64.5, "dir": "<=", "level0": 58.9, "moves": [{}], "text": "KO 收盘 ≤ 64.5 → 卖出"}
```

`basis: "und"` 表示按标的价格判断；`dir` 多头用 `<=`，空头用 `>=`；`level0` 是第一次设的止损，`moves` 每上移一次加一条。风险页用 `level0` 算"初始风险"，用 `level` 算热度。


已平仓盈亏（可选）：快照顶层可以带一个 `realized` 块，持仓页的「今年盈亏」和正股行的「本轮落袋」从这里读。不写就不显示。

```json
"realized": {"asof": "2026-10-27", "note": "IBKR 自 2026-01-02", "by_sym": {
  "KO": {"realized": 312.5, "count": 2, "held": true,
         "events": [{"date": "2026-09-12", "text": "卖出 50 股", "realized": 180.0}],
         "round": {"since": "2026-08-01", "count": 1, "realized": 180.0, "events": [ ... ]}}}}
```

`realized` 是含佣金的已实现盈亏（美元），`round` 是这一轮建仓以来的部分，可以不写。
## 补充日线 market-data.json（推荐）

用 [`tools/fetch_market_data.py`](../tools/fetch_market_data.py) 生成，或者自己写：

```json
{"schema": "shepherd-market-data/1", "price_basis": "regular_split_adjusted", "source": "你的数据来源",
 "collected_at": "2026-10-17T00:00:00Z",
 "series": {"SPX": [{"day": "2026-10-15", "close": 6450.1}, {"day": "2026-10-16", "close": 6475.0}], "KO": [ ... ]}}
```

- 收盘价要是常规时段、只做拆股调整、不含分红的口径。
- 必须有 `SPX` 或 `SPY`，当作大盘基准。至少 60 个交易日才算 Beta，最多用最近 252 个。
- 日期要严格递增；文件里任何一处不合格，整份都不采用，页面会写明原因。

## 主日线包 `<日期>.bundle.json`

作者的采集脚本写的格式。新用户用上面的 market-data.json 就够了。

```json
{"price_basis": "regular_split_adjusted", "signal_day": "2026-10-16",
 "series": {"NYSE:KO": {"daily": {"bars": [{"t": 1792180800, "c": 68.0}]}}}}
```

`t` 是 Unix 秒，`c` 是收盘价。App 读日期最新的那一个文件。

## 盘前观察 `<日期>.editorial.json`

```json
{"sheet": {"signal_day": "2026-10-15",
           "rows": [{"symbol": "NASDAQ:AMD", "quiet": false, "close": 165.0,
                     "facts": {"rsi6": "连续 2 天低位", "market_day": "同日大盘 +0.3%"}}]}}
```

`facts` 是任意的"标签 → 一句话"。`quiet: true` 的行不显示。超过 4 天没更新会标成过期。

## 宏观日历与财报

```json
{"events": [{"date": "2026-11-06", "name": "非农就业", "time": "08:30", "kind": "NFP"}]}
```

```json
{"data": {"KO": "2026-10-29", "JPM": "2026-10-22"}}
```

宏观日历应覆盖未来 90 天，否则日历页会提示"空白日期不代表没有事件"。财报日期标成"待确认"。

大科技财报名单（可选，`tools/daily_digest/core_earnings.json`，或用环境变量 `SHEPHERD_CORE_EARNINGS` 指定）：名单里的公司不管持不持有都列进日历主列表，最远看到 200 天。

```json
{"symbols": {"AAPL": {"name": "苹果", "date": "2026-11-02", "confirmed": true}}}
```

`confirmed` 为 `true` 表示公司已公告，`false` 是预估。名单里的日期过了，就改用财报缓存里的下一次日期。

## 宏观灯

```json
{"data_day": "2026-10-16", "light": "黄", "stage": "normal", "fuel_high": true, "vix_cheap": true,
 "knobs": {"heat_cap_r": 6, "bucket_scale": 0.75, "unconfirmed": "科技桶 0，其他桶 ≤ 1/3", "insurance": "各桶上限打 75 折"},
 "trends": [{"name": "宽基", "proxy": "SPY", "state": "顺风", "close": 646.0, "vs_ma200_pct": 3.3, "ma200_rising": true, "rule": "逢低买"}],
 "families": [{"key": "F1", "name": "估值", "role": "出事时能跌多深", "grade": "红", "reading": "…"}],
 "history": [{"week": "2026-10-09", "raw": "黄", "effective": "黄"}],
 "disarm": {"rel1": "…", "rel2": "…", "ma50": 6420.0, "ma200": 6100.0, "spx": 6475.0},
 "notes": ["…"]}
```

必填：`data_day`、`knobs.heat_cap_r`（热度上限，单位 R）、`knobs.bucket_scale`（板块上限的倍数）。`light` 用"红/黄/绿"，`trends[].state` 用"顺风/过渡/逆风"。超过 10 天没更新就不采用，上限回到固定值。

## 周线观察

```json
{"schema": "weekly-watch/2", "data_day": "2026-10-16", "order": ["VOO", "IWM"],
 "symbols": {"VOO": {"symbol": "VOO", "close": 594.0, "week_change_pct": -0.8, "atr": 18.3, "from_high_pct": -4.3,
                     "last_day": "2026-10-16", "partial_week": false,
                     "weeks": [{"t": "2026-10-16", "o": 600, "h": 603, "l": 590, "c": 594, "e20": 578, "e50": 562, "e100": 540, "e200": 506}],
                     "tiers": [{"span": 50, "ema": 562.7, "dist_pct": -5.3, "dist_atr": -1.7, "close_below": false,
                                "touched_this_week": false, "first_touch": false, "hist_reach": 56, "hist_deepest": 12}],
                     "old_highs": [{"price": 589.9}], "swing_lows": [{"price": 521.9, "week": "2026-08-07"}],
                     "zone": {"low": 560, "high": 565, "label": "50 周 EMA 一带"}}},
 "triggers": [{"key": "warning", "severity": "amber", "status": "off", "label": "预警：…", "detail": "…",
               "armed": {"title": "重点观察：…", "detail": "…"}}],
 "slow_bear": [], "light_note": "…", "source": "…"}
```

必填：`data_day`、`symbols`、`triggers`。`triggers[].status` 是 `on`、`off` 或 `unknown`；`severity` 是 `amber` 或 `red`。`order` 决定图表顺序，不写就按代码字母排。超过 5 天没更新会标成旧数。

## 纸盘

```json
{"schema": "paper-book-state/1", "data_day": "2026-10-16", "one_r": 2000.0,
 "totals": {"open": 1, "closed": 0, "pnl": -238.0, "pnl_r": -0.12, "budget_r_open": 0.5},
 "trades": [{"id": "P001", "name": "多 XLV 空 XLY", "status": "open", "entry_day": "2026-10-07", "budget_r": 0.5,
             "legs": [{"sym": "XLV", "side": "long", "qty": 100, "entry": 152.8, "last": 146.0, "pnl": -680, "ret_pct": -4.45}],
             "pnl": -238.0, "pnl_r": -0.12, "ratio_name": "XLV÷XLY", "ratio": 0.682, "stop_line": 0.664,
             "series": [{"day": "2026-10-16", "pnl": -238.0, "pnl_r": -0.12, "ratio": 0.682, "stop_line": 0.664}],
             "review_on": "2026-12-15"}],
 "closed_this_run": []}
```

必填：`data_day`、`trades`。纸盘不进热度、敞口、VaR 和真实账本。超过 5 天没更新会标成旧数。

## 期权计划卡

每张卡一个 JSON 文件，放在期权计划文件夹的顶层（子文件夹里的不显示，适合放已经成交的旧卡）。

```json
{"id": "de-call-spread", "name": "DE 11/20 450/480 看涨价差", "underlying": "DE", "spot": 440.0, "asof": "2026-10-16",
 "thesis": "回踩支撑后反弹到前高", "entry_price": 9.80, "contracts": 1, "horizons": [3, 5],
 "legs": [{"side": "buy",  "right": "C", "strike": 450, "expiry": "2026-11-20", "qty": 1, "bid": 16.2, "ask": 16.8},
          {"side": "sell", "right": "C", "strike": 480, "expiry": "2026-11-20", "qty": 1, "bid": 6.6, "ask": 7.0}],
 "levels": [{"price": 424.6, "label": "作废线", "kind": "invalid"}, {"price": 466.4, "label": "目标", "kind": "target"}],
 "notes": ["报价是写卡时的快照"]}
```

- `entry_price` 是净成本，借方为正。每条腿给 `bid`/`ask` 或 `price`，隐含波动率由报价反推（也可以直接给 `iv`）。
- `levels[].kind` 可以是 `invalid`、`entry`、`target`、`note`。
- 曲线是 Black-Scholes 估算，没有波动率偏斜、提前行权和分红。计划卡不进热度、敞口和账本。

## 券商确认单

把 Schwab 的确认单原件放进投喂文件夹：邮件 `.eml`、网页 `.html`、`.pdf` 或纯文本都可以。App 每分钟扫一次，把每条成交解析成提案；你在「成交与复盘」逐条核对后才入账。原文件只读，不移动、不删除。

`workbench/macos/SchwabEConfToInbox.applescript` 可以挂在 Mail 的收件规则上，自动把确认邮件存进投喂文件夹。
