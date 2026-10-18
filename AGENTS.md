# Notes for coding agents

Shepherd is a macOS trading desk: a SwiftUI app (`native/Sources/Shepherd/`) over a bundled Python engine (`workbench/`, standard library only). The user sets a fixed risk unit (1R); the app sizes plans from stop distance and reports portfolio heat, gap stress, VaR and option payoff charts. It never places orders and never calls an AI model.

How users work with agents: [docs/agent-guide.en.md](docs/agent-guide.en.md) (Chinese: [docs/agent-guide.md](docs/agent-guide.md)).

## When asked to produce data for the app

- Formats and locations: [docs/data-formats.md](docs/data-formats.md). Full examples: `python3 -m workbench.demo --out /tmp/shepherd-demo`, then look under `sources/`.
- Write only into the source folder (default: the user's home folder, layout in data-formats.md). Never write to the ledger `~/Library/Application Support/Shepherd/TradingDesk/trading.sqlite3`; open it with `sqlite3 -readonly` when you need to read it. Plans, fills and 1R are confirmed by the user in the app.
- Validate before reporting: `python3 tools/check_sources.py` (or `--source-home DIR`, `--holdings FILE`). Exit status 1 means a file exists but the app cannot use it.
- The user's data files contain real positions. Never commit them, paste them into issues, or put them in tests.

## When changing the code

```sh
python3 -m unittest discover -p 'test_*.py'      # engine tests
./build-workbench.sh                             # builds .app-build/Shepherd.app (needs python.org framework Python 3.10+)
open -n --env SHEPHERD_LANG=en .app-build/Shepherd.app --args --demo   # demo window on invented data
```

- Put calculations in `workbench/` with tests; the SwiftUI side only displays.
- Missing or broken inputs must show as unavailable, never as "no signal", zero risk or an empty list.
- Tests, demo data, screenshots and docs use invented numbers only.
- UI text is written in Simplified Chinese and translated at display time from `workbench/i18n/en.json` and `zh-Hant.json`. Write a sentence as one interpolated literal (`"第 \(n) 天（\(date)）"`), not pieces joined with `+`. After changing text: `python3 tools/i18n_extract.py .`, add the new entries to both tables, then `python3 tools/i18n_check.py en` and `python3 tools/i18n_check.py zh-Hant`.
- CI compiles with an older Swift than current Xcode: keep long SwiftUI string concatenations in computed properties or small subviews.
