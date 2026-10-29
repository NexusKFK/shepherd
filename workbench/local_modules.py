"""Loader for optional local modules that live in workbench/local/ (a folder that may not exist).

A module is a Python file in that folder. It provides:
  KEY                   short name; its data appears under state["local"][KEY]
  load(sources) -> dict its data for the state
  attention(data) -> list of (severity, title, detail, page) for the overview

If the folder is missing nothing happens. A module that fails to import, load or build its lines
only affects itself: its data becomes {"status": "error", "error": <exception type>}.
"""
from __future__ import annotations

import importlib
import pkgutil
from pathlib import Path

FOLDER = Path(__file__).resolve().parent / "local"


def discover():
    """(key, module) for every importable module in the folder, sorted by file name. Broken files are skipped."""
    if not FOLDER.is_dir():
        return []
    found = []
    for info in sorted(pkgutil.iter_modules([str(FOLDER)]), key=lambda i: i.name):
        if info.name.startswith("_"):
            continue
        try:
            module = importlib.import_module(f"{__package__ or 'workbench'}.local.{info.name}")
            found.append((str(getattr(module, "KEY", info.name)), module))
        except Exception:
            continue
    return found


def broken():
    """File names in the folder that cannot be imported, so the state can say so."""
    if not FOLDER.is_dir():
        return {}
    bad = {}
    for info in pkgutil.iter_modules([str(FOLDER)]):
        if info.name.startswith("_"):
            continue
        try:
            importlib.import_module(f"{__package__ or 'workbench'}.local.{info.name}")
        except Exception as error:
            bad[info.name] = type(error).__name__
    return bad


def load_all(sources):
    data = {name: {"status": "error", "error": why} for name, why in broken().items()}
    for key, module in discover():
        try:
            data[key] = module.load(sources)
        except Exception as error:
            data[key] = {"status": "error", "error": type(error).__name__}
    return data


def attention(data):
    """Overview lines from every module. A module that cannot build its lines contributes none."""
    lines = []
    data = data if isinstance(data, dict) else {}
    for key, module in discover():
        if key not in data:  # state built without local data (not loaded): say nothing
            continue
        try:
            for severity, title, detail, page in module.attention(data[key]):
                lines.append((severity, title, detail, page))
        except Exception:
            continue
    return lines
