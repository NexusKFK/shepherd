#!/usr/bin/env python3
"""Collect every Chinese string the app can show, for translation.

    python3 tools/i18n_extract.py            # writes workbench/i18n/source.json

Sources:
  * Swift string literals in native/Sources/Shepherd (interpolations and printf specifiers become {0}, {1}, ...)
  * Python string constants and f-strings in workbench/ (f-string fields become {0}, {1}, ...)

An entry with placeholders is a template: at display time the app matches the whole shown string against it and
translates the filled-in values separately. Entries without placeholders are matched exactly, or as fragments of a
longer string. The engine keeps working in Chinese; translation happens only when text is shown.
"""
from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path

ROOT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent
CJK = re.compile(r"[一-鿿]")
CJK_PUNCT = re.compile("[\u3000-\u303f\uff00-\uffef]")
PRINTF = re.compile(r"%(?:\d+\$)?[-+ 0#]*\d*(?:\.\d+)?(?:hh|h|ll|l|q|z|t|j|L)?[@dDiuUxXoOfFeEgGcCsSpaA]")
SKIP_PY = {"qa_browser.py", "server.py"}


def swift_literals(text):
    """String literals per line, with Swift escapes resolved and \\( ) interpolations kept as raw markers."""
    for line in text.splitlines():
        code = line
        if line.lstrip().startswith("//"):
            continue
        i = 0
        while True:
            start = code.find('"', i)
            if start < 0:
                break
            j, depth, buf = start + 1, 0, []
            while j < len(code):
                ch = code[j]
                if ch == "\\" and j + 1 < len(code):
                    nxt = code[j + 1]
                    if nxt == "(":
                        depth_j, k = 1, j + 2
                        while k < len(code) and depth_j:
                            depth_j += {"(": 1, ")": -1}.get(code[k], 0)
                            k += 1
                        buf.append("\x00")       # interpolation marker
                        j = k
                        continue
                    buf.append({"n": "\n", "t": "\t", '"': '"', "\\": "\\"}.get(nxt, nxt))
                    j += 2
                    continue
                if ch == '"':
                    break
                buf.append(ch)
                j += 1
            yield "".join(buf)
            i = j + 1


def to_template(raw):
    """Interpolation markers and printf specifiers become numbered placeholders."""
    n = 0
    out = []
    pos = 0
    pattern = re.compile("\x00|" + PRINTF.pattern)
    for m in pattern.finditer(raw):
        out.append(raw[pos:m.start()].replace("{", "{{").replace("}", "}}"))
        out.append("{%d}" % n)
        n += 1
        pos = m.end()
    out.append(raw[pos:].replace("{", "{{").replace("}", "}}"))
    return "".join(out), n


def python_strings(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef, ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
                docstrings.add(id(first.value))
    inner = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            for v in node.values:
                inner.add(id(v))
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings and id(node) not in inner:
            yield node.value, 0
        elif isinstance(node, ast.JoinedStr):
            parts, n = [], 0
            for v in node.values:
                if isinstance(v, ast.Constant):
                    parts.append(str(v.value).replace("{", "{{").replace("}", "}}"))
                else:
                    parts.append("{%d}" % n)
                    n += 1
            yield "".join(parts), n


def main():
    entries = {}

    def add(text, n, where):
        # Chinese text, or a template whose fixed part is only Chinese punctuation ("{0}：{1}"): it joins translated pieces
        if not CJK.search(text) and not (n and CJK_PUNCT.search(text)):
            return
        e = entries.setdefault(text, {"key": text, "placeholders": n, "where": []})
        if where not in e["where"]:
            e["where"].append(where)

    for f in sorted((ROOT / "native/Sources/Shepherd").glob("*.swift")):
        if f.name == "Localization.swift":      # language names are shown as written, never translated
            continue
        for raw in swift_literals(f.read_text(encoding="utf-8")):
            template, n = to_template(raw)
            if n == 0:
                template = raw          # plain text: braces are literal, no escaping needed
            add(template, n, f.name)
    for f in sorted((ROOT / "workbench").glob("*.py")):
        if f.name in SKIP_PY:
            continue
        for text, n in python_strings(f):
            add(text, n, f.name)
    out = ROOT / "workbench/i18n" / (sys.argv[2] if len(sys.argv) > 2 else "source.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    rows = sorted(entries.values(), key=lambda e: (e["where"][0], e["key"]))
    out.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    templates = sum(1 for r in rows if r["placeholders"])
    print(f"{out}: {len(rows)} strings ({templates} templates)")


if __name__ == "__main__":
    main()
