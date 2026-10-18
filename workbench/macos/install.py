"""Install one Shepherd app, retaining restorable archives and the existing ledger."""
from __future__ import annotations

import argparse
from datetime import datetime
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import uuid
import zipfile
from xml.parsers.expat import ExpatError


BUNDLE_ID = "local.shepherd.tradingdesk"
LSREGISTER = "/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister"


def info(path: Path) -> dict:
    try:
        value = plistlib.loads((path / "Contents/Info.plist").read_bytes())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, plistlib.InvalidFileException, ExpatError):
        return {}


def bundles(root: Path):
    for parent, directories, _ in os.walk(root):
        for name in directories[:]:
            if name.endswith(".app"):
                directories.remove(name)
                path = Path(parent) / name
                if info(path).get("CFBundleIdentifier") == BUNDLE_ID:
                    yield path


def run(*args: str | Path):
    subprocess.run([str(arg) for arg in args], check=True)


def unregister(path: Path):
    result = subprocess.run([LSREGISTER, "-u", str(path)], capture_output=True, text=True)
    # A never-opened build may not have a Launch Services record yet.
    if result.returncode and "-10814" not in result.stdout + result.stderr:
        raise RuntimeError(f"Cannot unregister {path}: {result.stdout}{result.stderr}")


def main():
    parser = argparse.ArgumentParser(description="Install the single Shepherd app without changing its ledger")
    parser.add_argument("--check", action="store_true", help="Show the installation and archives without changing files")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    built = root / ".app-build/Shepherd.app"
    apps = Path.home() / "Applications"
    destination = apps / "Shepherd.app"
    backup_root = Path.home() / "Library/Application Support/Shepherd/AppBackups"
    expected = {"CFBundleIdentifier": BUNDLE_ID, "CFBundleName": "Shepherd", "CFBundleDisplayName": "Shepherd"}
    if any(info(built).get(key) != value for key, value in expected.items()):
        raise SystemExit("Run ./build-workbench.sh first; the built Shepherd bundle is missing or invalid.")
    run("codesign", "--verify", "--deep", "--strict", built)
    if destination.exists() and info(destination).get("CFBundleIdentifier") != BUNDLE_ID:
        raise SystemExit("A different app already occupies ~/Applications/Shepherd.app; nothing changed.")
    candidates = sorted(set(bundles(apps)) | set(bundles(root / ".app-build")) | set(bundles(backup_root)))
    print(f"Install: {destination}", flush=True)
    for path in candidates:
        print(f"Archive duplicate: {path}", flush=True)
    print("Ledger remains: ~/Library/Application Support/Shepherd/TradingDesk/trading.sqlite3", flush=True)
    if args.check:
        return
    # Quit through the app first so that unsaved forms can be handled normally.
    processes = subprocess.check_output(["ps", "-axo", "command="], text=True)
    if any(str(path / "Contents/MacOS" / str(info(path).get("CFBundleExecutable", ""))) in line
           for path in candidates for line in processes.splitlines()):
        raise SystemExit("Quit the running Shepherd app before installing; nothing changed.")
    backup = backup_root / (datetime.now().strftime("%Y%m%d-%H%M%S") + "-rename")
    backup.mkdir(parents=True, mode=0o700)
    apps.mkdir(exist_ok=True)
    staging = apps / (".Shepherd-install-" + uuid.uuid4().hex + ".app")
    run("ditto", built, staging)
    run("codesign", "--verify", "--deep", "--strict", staging)
    # Every bundle is archived and CRC-checked before any old path is moved.
    manifest = []
    for index, path in enumerate(candidates):
        archive = backup / f"{index:02d}-{path.stem}.zip"
        run("ditto", "-c", "-k", "--sequesterRsrc", "--keepParent", path, archive)
        with zipfile.ZipFile(archive) as zipped:
            if zipped.testzip() is not None:
                raise RuntimeError(f"Archive verification failed: {archive}")
            # ditto stores non-ASCII names in a macOS ZIP extra field; Python may
            # decode the root name differently. The ASCII suffix stays stable.
            plists = [name for name in zipped.namelist()
                      if name.count("/") == 2 and name.endswith("/Contents/Info.plist")]
            if len(plists) != 1:
                raise RuntimeError(f"Archive has no unique app manifest: {archive}")
            archived_info = plistlib.loads(zipped.read(plists[0]))
            if archived_info.get("CFBundleIdentifier") != BUNDLE_ID:
                raise RuntimeError(f"Wrong bundle archived: {archive}")
        manifest.append({"original": str(path), "archive": archive.name})
    import json
    (backup / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    moved = []
    installed = False
    try:
        for index, path in enumerate(candidates):
            # Preserve the canonical registration and Dock pin during upgrades.
            if path != destination:
                unregister(path)
            retired = backup / f"{index:02d}.retired-bundle"
            path.rename(retired)
            moved.append((path, retired))
        staging.rename(destination)
        installed = True
        run("codesign", "--verify", "--deep", "--strict", destination)
        run(LSREGISTER, "-f", destination)
    except BaseException:
        if installed:
            destination.rename(staging)
        for original, retired in reversed(moved):
            retired.rename(original)
            run(LSREGISTER, "-f", original)
        raise
    for _, retired in moved:
        shutil.rmtree(retired)  # Only this run's moved copies; verified ZIPs remain.
    print(f"Installed: {destination}\nVerified archives: {backup}", flush=True)


if __name__ == "__main__":
    main()
