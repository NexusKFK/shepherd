# Changelog

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
