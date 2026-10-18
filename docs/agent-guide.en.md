# Using Shepherd with a coding agent

[简体中文](agent-guide.md)

Shepherd fetches no data and calls no AI. Every input is a JSON file in a documented format, and the app only reads it. That split suits a coding agent such as Claude Code, Codex or Cursor: the agent writes files and scripts, Shepherd does the arithmetic and shows it, and you make the decisions.

This is how the author uses it. Agent-written scripts produce the holdings snapshot, daily bars, macro light, weekly watch and paper book files every day, and option plan cards are often drafted by an agent first.

## Who does what

| Who | What |
|---|---|
| You | Set 1R, decide what and how much to trade, place orders at your broker, confirm plans and fills in the app. |
| The agent | Writes data files and collector scripts, drafts option plan cards, reads the ledger for reviews, changes the app's code. |
| Shepherd | Reads those files and computes sizes, heat and risk. A missing or broken file is reported as such, never shown as normal. |

## Three boundaries

1. **Only the app writes the ledger.** Trade plans, fills and 1R are confirmed by you in the app. The agent writes only files in the source folder (locations in [data-formats.md](data-formats.md)). To read the ledger, it opens it read-only:

   ```sh
   sqlite3 -readonly ~/Library/Application\ Support/Shepherd/TradingDesk/trading.sqlite3 '.tables'
   ```

2. **No orders.** Shepherd cannot place orders, and the agent should not place them for you either. It can draft an order; you place it.
3. **Your positions stay on your Mac.** The data files hold your real accounts and positions. Do not let the agent commit them to a public repository or post them anywhere.

## Ways to work together

1. **Holdings snapshot from a broker export.** Give the agent your broker's CSV and [`examples/holdings-template.json`](../examples/holdings-template.json) and ask for the same format, ideally as a script you can rerun daily. Check it with `python3 tools/check_sources.py --holdings your-holdings.json`, then import it under **Data → Import holdings JSON…**.
2. **Daily bars.** Have the agent schedule `python3 tools/fetch_market_data.py AAPL MSFT NVDA` with launchd after each close; the risk page uses the bars for beta, the market scenario and VaR.
3. **Option plan cards.** Describe the idea: underlying, direction, rough expiry and the most you will lose in R. The agent picks strikes from live quotes and writes a card in the [plan card format](data-formats.md) into the plan-card folder. The **Option plans** page charts it at once; check the max loss, breakeven and curve before you trade. Move a card into a subfolder after it fills to hide it.
4. **Data scripts for optional modules.** Macro light, weekly watch, paper book, pre-market watch and calendar each read one JSON file; formats are in [data-formats.md](data-formats.md), and `python3 -m workbench.demo --out /tmp/shepherd-demo` writes a full set of examples under `sources/`. You decide the rules (for example when the macro light turns red); the agent implements, backtests and schedules them.
5. **Reviews.** The agent reads the ledger read-only and summarises fills by plan, results in R and any trade that went past its planned 1R. Conclusions go into your own journal, not back into the ledger.
6. **Changing the app.** [AGENTS.md](../AGENTS.md) at the repository root has the development rules for agents: calculations live in the Python engine with tests, UI text must stay translatable, tests use invented data only.

## Check after every write

```sh
python3 tools/check_sources.py                         # where the app reads by default
python3 tools/check_sources.py --source-home some/dir  # a test folder first
```

One line per source: OK, STALE (readable but old), MISSING (no file; fine for a module you don't use) or BAD (a file the app cannot use). Any BAD makes the command exit with status 1, so the agent can catch and fix its own mistakes.

## A prompt to start with

```
This project is Shepherd (github.com/NexusKFK/shepherd), a risk-first trading desk for macOS.
Read AGENTS.md and docs/data-formats.md in the repository first.

Task: <your task>

Rules:
- Write only files in the source folder. Never modify trading.sqlite3; read it with sqlite3 -readonly.
- Run python3 tools/check_sources.py when done and report back only when nothing is BAD.
- Never commit my positions or account data to any repository.
```
