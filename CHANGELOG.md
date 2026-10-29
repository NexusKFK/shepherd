# Changelog

## 1.4.2 (2026-10-29)

- Trade plans are now the home of every plan, option structures included. Plans drafted by a coding agent come in as "Pending verification"; you approve them into a draft or reject them. A trade that is already open can record its real fills straight from a draft, without the 1R check, and a plan keeps its source when edited.
- The option plans page is now **Option lab**: a pure OptionStrat-style visualizer that opens on your held structures. Scratch cards only show when there are any. Spot prices fall back to the snapshot's quotes, and a plan's placeholder row no longer breaks a structure chart.
- Holdings: legs you put in one group (same broker, underlying and expiry, at least one long and one short) get a single combo summary row. A holding option plan without a snapshot link claims its same-expiry legs automatically, so the structure is not shown twice; ambiguous cases still ask you to confirm.
- Holdings: options past expiry with no closing fill are settled at the expiry close's intrinsic value (reversible). A "This year" tab shows realized and open P&L per symbol, and stock rows show what was banked this round, from an optional `realized` block in the snapshot.
- Risk: an R budget panel. Core R in use (heat minus options expiring within two weeks and minus stocks whose stop is above cost) is compared with a 3–5R target band; short-dated options get their own allowance (0.5R total, 0.2R each) and long legs with |Delta| below 0.30 count as lotteries (0.2R total, 0.1R each). Prompts only; the defaults live in `workbench/exposure.py`.
- Calendar: a month view (now the default) beside the list. Earnings of held companies are starred, say what you hold, and warn when an option expires on or after the report. An optional big-tech list keeps those companies on the calendar whether held or not.
- Fills & review: notification mails no longer warn about a missing confirmation number, open/close word or fees; missing fees are estimated at default Schwab rates and stay editable. "Sold Short" reads as an opening sale, same-day same-expiry buy and sell legs group into one strategy, and settled files collapse into one group.
- A "Fetch mail" button on the holdings and fills pages runs a locally installed mail-export launchd job. It only shows when that job is installed.
- Local-only modules: pages placed in `workbench/local/` and `native/Sources/Shepherd/Local/` load automatically and are never part of the public export.
- The calendar's month grid, the R budget and the new pages are translated into English and Traditional Chinese.

中文：交易计划成为所有计划（含期权结构）的归处；助手起草的计划先进「待核对」，由你通过或退回；已开仓的交易可以直接从草稿登记真实成交。期权计划页改为「期权演算」，只做可视化，默认显示持仓结构。持仓页：同组多腿合成一行，持仓期权计划自动认领同到期的腿；到期未平仓的期权按到期收盘内在价值结算；新增「今年盈亏」页和「本轮落袋」。风险页新增 R 预算：核心占用对照 3–5R 目标区间，两周内到期的期权和价外彩票各有单独额度，只提示不拦截。日历新增月历视图，持仓公司财报加星并提示期权到期，可选的大科技财报名单。成交与复盘：通知邮件不再误报，缺失费用按默认费率估算，已处理的文件收起。持仓页和成交页新增「收取邮件」按钮（只在本机装了收取任务时显示）。支持只在本机加载、不进公开版的本地模块。新页面补齐英文和繁体中文。

## 1.4.1 (2026-10-20)

- Holdings: the detail panel has a close button and closes with Esc. In a narrow window it floats over the right side of the table instead of pushing the table and footer down.
- Docs: a "What it does" overview in the READMEs, a guide to working with a coding agent (`docs/agent-guide.md`, `docs/agent-guide.en.md`) and `AGENTS.md` with development rules for agents.
- `tools/check_sources.py` checks every data file the app reads, with the app's own loaders, and exits with status 1 when a file is unusable.

## 1.4.0 (2026-10-18)

- The interface is now in English, Simplified Chinese and Traditional Chinese. It follows the macOS language by default; switch it in Settings or on the first-run walkthrough. Engine messages and alerts are translated too.
- A Settings window (⌘, or the gear in the toolbar) with four tabs: General (language, appearance, demo, walkthrough), Risk & 1R, Modules, and Data (ledger location, backup, holdings source, confirmation inbox).
- Appearance can be set to match the system, dark or light.
- The demo keeps its own language and appearance, so trying them there never changes the real app.
- The fill form only asks to discard changes after something was actually edited.

中文：界面支持简体中文、繁体中文和英文，默认跟随系统语言，引擎提示和告警一起翻译。新增设置窗口（⌘, 或工具栏齿轮）：通用、风险与 1R、模块、数据四页；外观可选跟随系统、深色或浅色；演示窗口的语言和外观单独保存；登记成交表单只有真的改过才会问是否放弃。

## 1.3.0 (2026-10-18)

First public release.

- First-run walkthrough: an interactive R example, then setting the sizing net value and risk per trade, then picking modules. No plan can be sized before 1R is confirmed.
- Optional modules (calendar, option plans, pre-market, macro light, weekly watch, paper book, Schwab confirmation import). A module that is off hides its page, its overview lines and its freshness dot.
- Demo mode (`File → 打开演示（虚构数据）` or `--demo`): a second window on invented accounts, prices and plans in a temporary folder.
- An empty ledger shows a "从这里开始" card with the three ways to start.
- A plan held only in the app's ledger now carries its stop into heat and takes its price from snapshot quotes.
- A snapshot or daily-bar source that was never connected is shown as optional, not as broken.
- Next-refresh times are shown in the Mac's own time zone.
- `tools/fetch_market_data.py` writes the optional daily-bars file from Yahoo's chart endpoint.

中文：首个公开版本。新增首次引导（认识 R、定 R、选模块）、可选模块、演示模式、空账本引导卡片；只在 App 账本里的持仓也会把计划止损计入热度；从未接入的数据源显示为"未接入（可选）"而不是故障；更新时间按本机时区显示；新增补日线脚本。
