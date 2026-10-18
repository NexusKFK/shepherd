#!/usr/bin/env python3
"""Check a translation file against workbench/i18n/source.json.

    python3 tools/i18n_check.py en          # checks workbench/i18n/en.json
    python3 tools/i18n_check.py zh-Hant     # checks workbench/i18n/zh-Hant.json

Exit code 0 only when every source string has a translation and every translation keeps the source's
placeholders ({0}, {1}, ... and any other {...} token) and line breaks. English must contain no Chinese characters.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if not (ROOT / "workbench").is_dir():          # running from the private repo's oss/public/tools
    ROOT = ROOT.parent.parent
CJK = re.compile(r"[一-鿿]")
TOKEN = re.compile(r"\{[^{}]*\}")


def tokens(s):
    return sorted(TOKEN.findall(s.replace("{{", "").replace("}}", "")))


def main():
    lang = sys.argv[1] if len(sys.argv) > 1 else "en"
    folder = ROOT / "workbench/i18n"
    source = json.loads((folder / "source.json").read_text(encoding="utf-8"))
    path = folder / f"{lang}.json"
    try:
        table = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        print(f"缺少文件 {path}"); return 1
    except ValueError as e:
        print(f"{path} 不是合法 JSON：{e}"); return 1
    if not isinstance(table, dict):
        print("顶层必须是一个对象：{\"原文\": \"译文\", ...}"); return 1
    keys = {e["key"] for e in source}
    problems = []
    missing = [k for k in keys if not isinstance(table.get(k), str) or not table[k].strip()]
    extra = [k for k in table if k not in keys]
    for e in source:
        k = e["key"]
        v = table.get(k)
        if not isinstance(v, str) or not v.strip():
            continue
        if tokens(k) != tokens(v):
            problems.append(f"占位符不一致：{k!r} → {v!r}")
        if k.count("\n") != v.count("\n"):
            problems.append(f"换行数不一致：{k!r} → {v!r}")
        if lang == "en" and CJK.search(v):
            problems.append(f"英文里有中文：{k!r} → {v!r}")
    for k in missing[:30]:
        problems.append(f"没有译文：{k!r}")
    for k in extra[:30]:
        problems.append(f"多出来的键（原文里没有）：{k!r}")
    done = len(keys) - len(missing)
    print(f"{lang}: {done}/{len(keys)} 条已翻译，缺 {len(missing)} 条，多出 {len(extra)} 条，其他问题 {len(problems) - min(len(missing), 30) - min(len(extra), 30)} 条")
    for p in problems[:80]:
        print("  " + p)
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main())
