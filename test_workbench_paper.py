import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from workbench import overview, paper


def write_state(directory, **over):
    body = {"schema": "paper-book-state/1", "data_day": "2026-10-13", "one_r": 2000.0,
            "totals": {"open": 1, "closed": 1, "pnl": -150.0, "pnl_r": -0.075, "budget_r_open": 0.5},
            "trades": [{"id": "P001", "name": "多 A 空 B", "status": "open", "review_on": "2027-01-12", "pnl": 50.0},
                       {"id": "P002", "name": "多 C 空 D", "status": "closed", "closed_day": "2026-10-13", "pnl": -200.0}],
            "closed_this_run": [{"id": "P002", "day": "2026-10-13", "why": "净亏损 -1,153 美元达到 0.5R"}]}
    body.update(over)
    path = Path(directory) / "state.json"
    path.write_text(json.dumps(body, ensure_ascii=False))
    return path


class PaperLoadTests(unittest.TestCase):
    def test_ok(self):
        with tempfile.TemporaryDirectory() as d:
            p = paper.load(write_state(d), today=date(2026, 10, 14))
        self.assertEqual(p["status"], "ok")
        self.assertEqual(len(p["trades"]), 2)
        self.assertEqual(p["age_days"], 1)

    def test_missing_stale_invalid_are_named_never_empty_book(self):
        with tempfile.TemporaryDirectory() as d:
            missing = paper.load(Path(d) / "none.json")
            stale = paper.load(write_state(d), today=date(2026, 10, 25))
            (Path(d) / "bad.json").write_text("{")
            invalid = paper.load(Path(d) / "bad.json")
            nofield = paper.load(write_state(d, data_day=None), today=date(2026, 10, 14))
        for p, status in [(missing, "missing"), (stale, "stale"), (invalid, "invalid"), (nofield, "invalid")]:
            self.assertEqual(p["status"], status)
            self.assertTrue(p["notice"])
        self.assertEqual(len(stale["trades"]), 2)  # stale keeps the old marks, flagged

    def test_attention_reports_exit_and_review(self):
        with tempfile.TemporaryDirectory() as d:
            p = paper.load(write_state(d, data_day="2027-01-12"), today=date(2027, 1, 13))
        lines = paper.attention(p)
        self.assertEqual(lines[0][0], "amber")
        self.assertIn("多 C 空 D 已触发离场", lines[0][1])
        self.assertTrue(any("到复核日" in title for _, title, _ in lines))

    def test_attention_quiet_when_missing_amber_when_stale(self):
        self.assertEqual(paper.attention({"status": "missing", "trades": []}), [])
        self.assertEqual(paper.attention({"status": "stale", "notice": "旧", "trades": []})[0][0], "amber")

    def test_overview_routes_paper_lines_to_paper_page(self):
        with tempfile.TemporaryDirectory() as d:
            p = paper.load(write_state(d), today=date(2026, 10, 14))
        items = overview.attention({"paper": p, "preparation": {"checks": ["snapshot", "events", "plans"]}},
                                   date(2026, 10, 14), [])
        self.assertTrue(any(i["page"] == "paper" and "已触发离场" in i["title"] for i in items))


if __name__ == "__main__":
    unittest.main()
