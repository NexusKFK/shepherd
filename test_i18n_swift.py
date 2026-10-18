"""Display-time translation: compiles native/Sources/Shepherd/Localization.swift with a tiny driver and checks the
lookup rules against a small invented table, plus the shipped tables' completeness."""
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).parent
SRC = ROOT / "native" / "Sources" / "Shepherd" / "Localization.swift"
DRIVER = '''
import Foundation
Translator.shared.load(CommandLine.arguments[1])
while let line = readLine() {
    print(Translator.shared.translate(line.replacingOccurrences(of: "\\\\n", with: "\\n")).replacingOccurrences(of: "\\n", with: "\\\\n"))
}
'''
TABLE = {
    "总览": "Overview",
    "热度上限": "Heat cap",
    "{0} 天后": "in {0} days",
    "第 {0} 天（{1}）": "day {0} ({1})",
    "漏了 {0} 期（应有基于 {1} 收盘的一期）": "missed {0} (expected the one from the {1} close)",
    "{0} {1}：{2}": "{0} {1}: {2}",
    "正股 {0} 股": "{0} shares",
    "现价已在止损之外，需要立即处理": "price is past the stop; act now",
    "日期": "Date",
    "示例券商": "Sample broker",
    "价格 {{用大括号}} {0}": "price {{in braces}} {0}",
    " · 正股 {0}": " · underlying {0}",
    "{0} 条": "{0} entries",
    "{0} 份": "{0} versions",
}
CASES = [
    ("总览", "Overview"),                                         # exact
    ("5 天后", "in 5 days"),                                      # template
    ("漏了 3 期（应有基于 2026-10-15 收盘的一期）", "missed 3 (expected the one from the 2026-10-15 close)"),
    ("XOM 正股 40 股：现价已在止损之外，需要立即处理", "XOM 40 shares: price is past the stop; act now"),  # nested templates
    ("日期：第 7 天（10-25）", "Date: day 7 (10-25)"),            # separator split, then a template
    ("示例券商 A · 热度上限", "Sample broker A · Heat cap"),       # fragments across a separator
    ("回踩 50 日线", "回踩 50 日线"),                              # user text with unknown words stays as written
    ("总览 回踩", "总览 回踩"),                                     # partly known: shown unchanged, never half translated
    ("AAPL 100", "AAPL 100"),                                     # nothing Chinese: untouched
    ("价格 {用大括号} 9", "price {in braces} 9"),                 # escaped braces in a template
    ("示例券商 A · 正股 83", "Sample broker A · underlying 83"),   # a piece that is an entry together with its separator
    ("1 条", "1 entry"),                                          # English singular after 1
    ("11 条", "11 entries"),
    ("1 份", "1 version"),
]


@unittest.skipUnless(shutil.which("swiftc"), "swiftc not available")
class SwiftTranslatorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        d = Path(cls.tmp.name)
        (d / "main.swift").write_text(DRIVER)
        (d / "en.json").write_text(json.dumps(TABLE, ensure_ascii=False))
        cls.exe = d / "probe"
        subprocess.run(["swiftc", "-O", "-o", str(cls.exe), str(SRC), str(d / "main.swift")], check=True, capture_output=True)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def run_lines(self, lang, lines):
        env = dict(os.environ, SHEPHERD_I18N_DIR=self.tmp.name)
        env.pop("SHEPHERD_LANG", None)
        out = subprocess.run([str(self.exe), lang], input="\n".join(lines) + "\n", capture_output=True, text=True, env=env, check=True)
        return out.stdout.splitlines()

    def test_lookup_rules(self):
        got = self.run_lines("en", [c[0] for c in CASES])
        for (source, want), have in zip(CASES, got):
            self.assertEqual(have, want, source)

    def test_simplified_chinese_is_shown_as_written(self):
        self.assertEqual(self.run_lines("zh-Hans", ["总览", "5 天后"]), ["总览", "5 天后"])


class ShippedTablesTests(unittest.TestCase):
    """Every string the app can show has an English and a Traditional Chinese entry with the same placeholders."""
    def test_tables_are_complete(self):
        folder = ROOT / "workbench" / "i18n"
        source = json.loads((folder / "source.json").read_text(encoding="utf-8"))
        for lang in ("en", "zh-Hant"):
            checker = ROOT / "tools" / "i18n_check.py"
            if not checker.exists():                      # private checkout keeps the public tools under oss/public
                checker = ROOT / "oss" / "public" / "tools" / "i18n_check.py"
            result = subprocess.run(["python3", str(checker), lang], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout[-2000:])
        self.assertGreater(len(source), 1000)


if __name__ == "__main__":
    unittest.main()
