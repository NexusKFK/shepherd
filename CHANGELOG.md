# Changelog

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
