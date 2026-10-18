#!/usr/bin/env python3
"""Check the data files Shepherd reads, using the same code the app uses to read them.

    python3 tools/check_sources.py                          # files under your home folder (the app's default)
    python3 tools/check_sources.py --source-home DIR        # files under another folder, e.g. a test folder
    python3 tools/check_sources.py --holdings FILE          # also check a holdings JSON before importing it

Run it from the repository root. It only reads files. Each source gets one line: OK, STALE (readable but old),
MISSING (no file; fine for a module you don't use) or BAD (a file exists but the app cannot use it).
The exit status is 1 when any source is BAD, so a script or a coding agent can check its own output.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from workbench import macro, optionviz, paper, weekly_watch  # noqa: E402
from workbench.sources import Sources  # noqa: E402


def holdings(path: Path):
    if not path.exists():
        return "MISSING", str(path), "import a holdings JSON in the app, or write one here"
    snap = _snapshot_at(path)
    if snap["status"] == "unavailable":
        return "BAD", str(path), "; ".join(snap["errors"])
    note = f"{len(snap['positions'])} positions, as of {snap['asof']}"
    if snap["errors"]:
        note += " · " + "; ".join(snap["errors"])
    return ("STALE" if snap["status"] == "stale" else "OK"), str(path), note


def _snapshot_at(path: Path):
    source = Sources(path.parent)
    source.snapshot_path = path
    return source.snapshot()


def module(loader, path: Path):
    result = loader(path)
    status = result.get("status")
    label = {"ok": "OK", "stale": "STALE", "missing": "MISSING"}.get(status, "BAD")
    return label, str(path), result.get("notice") or result.get("data_day") or ""


def plan_cards(folder: Path):
    if not folder.is_dir():
        return [("MISSING", str(folder), "no plan-card folder")]
    built = optionviz.build([], directory=folder)
    rows = []
    for card in built["cards"]:
        if card.get("status") == "ok":
            rows.append(("OK", f"{folder}/{card['id']}.json", f"{card.get('name', '')} · max loss {card.get('max_loss')}"))
        else:
            rows.append(("BAD", f"{folder}/{card['id']}.json", card.get("error") or card.get("status")))
    return rows or [("MISSING", str(folder), "folder is empty")]


def json_file(path: Path, what: str):
    if not path.exists():
        return "MISSING", str(path), what
    try:
        json.loads(path.read_text())
    except (OSError, ValueError) as error:
        return "BAD", str(path), f"not valid JSON: {error}"
    return "OK", str(path), what


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--source-home", help="folder the app reads data files from (default: your home folder)")
    parser.add_argument("--holdings", help="a holdings JSON file to check, e.g. before importing it")
    args = parser.parse_args()
    src = Sources(args.source_home) if args.source_home else Sources()

    rows = [("Holdings snapshot", *holdings(Path(args.holdings).expanduser() if args.holdings else src.snapshot_path))]
    rows.append(("Macro light", *module(macro.load, src.macro_state)))
    rows.append(("Weekly watch", *module(weekly_watch.load, src.weekly_state)))
    rows.append(("Paper book", *module(paper.load, src.paper_state)))
    rows += [("Option plan card", *row) for row in plan_cards(src.option_plans)]
    rows.append(("Macro calendar", *json_file(src.digest / "calendar_data.json", "macro events for the calendar")))
    rows.append(("Earnings dates", *json_file(src.digest / "cache/earnings.json", "earnings dates for the calendar")))
    inbox = src.econf_inbox
    rows.append(("Confirmation inbox", "OK" if inbox.is_dir() else "MISSING", str(inbox), "folder for Schwab confirmations"))

    for name, status, where, note in rows:
        print(f"{status:<8} {name:<19} {where}" + (f"\n         {'':<19} {note}" if note else ""))
    return 1 if any(row[1] == "BAD" for row in rows) else 0


if __name__ == "__main__":
    sys.exit(main())
