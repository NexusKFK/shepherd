#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
build_dir="$PWD/.app-build"
app_dir="$build_dir/Shepherd.app"
mkdir -p "$app_dir/Contents/MacOS" "$app_dir/Contents/Resources"
swift workbench/macos/MakeIcon.swift "$build_dir/AppIcon.iconset"
iconutil -c icns "$build_dir/AppIcon.iconset" -o "$build_dir/AppIcon.icns"
swiftc -swift-version 5 -O -target "$(uname -m)-apple-macos14.0" -parse-as-library $(find native/Sources/Shepherd -name '*.swift' | sort) -o "$app_dir/Contents/MacOS/Shepherd" -framework AppKit -framework SwiftUI -framework Charts
swiftc -O -target "$(uname -m)-apple-macos14.0" workbench/macos/PdfText.swift -o "$app_dir/Contents/MacOS/pdf_text" -framework PDFKit
python3 - "$app_dir" <<'PY'
import json, plistlib, shutil, sys
from pathlib import Path
p=Path(sys.argv[1])
package=p/'Contents/Resources/workbench'
if package.exists(): shutil.rmtree(package)
shutil.copytree('workbench',package,ignore=shutil.ignore_patterns('__pycache__','macos','qa_browser.py','static','server.py','__main__.py','source.json','*.next.json'))
# Native UI communicates over stdio; the engine runtime is embedded below.
legacy=p/'Contents/Resources/runtime.json'
if legacy.exists(): legacy.unlink()
with (p/'Contents/Info.plist').open('wb') as f:
    plistlib.dump({'CFBundleName':'Shepherd','CFBundleDisplayName':'Shepherd',
        'CFBundleIdentifier':'local.shepherd.tradingdesk','CFBundleVersion':'14','CFBundleShortVersionString':'1.4.2',
        'CFBundleExecutable':'Shepherd','CFBundlePackageType':'APPL','LSMinimumSystemVersion':'14.0',
        'NSHighResolutionCapable':True,
        'CFBundleIconFile':'AppIcon'},f)
PY
if [ -f "$build_dir/AppIcon.icns" ]; then cp "$build_dir/AppIcon.icns" "$app_dir/Contents/Resources/"; fi
cp workbench/macos/AppIconSource.png "$app_dir/Contents/Resources/Logo.png"
python3 workbench/macos/bundle_runtime.py "$app_dir"
codesign --force --deep --sign - "$app_dir"
codesign --verify --deep --strict "$app_dir"
echo "已生成：$app_dir"
