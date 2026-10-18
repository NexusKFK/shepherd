"""Bundle a relocatable python.org macOS framework runtime, excluding user packages.

Uses the build interpreter, never downloads code, and fails if any non-system
Mach-O dependency remains outside the app. The PSF license travels with it.
"""
from pathlib import Path
import os
import shutil
import subprocess
import sys
import sysconfig

root = Path(sys.base_prefix).resolve()
if sysconfig.get_config_var('PYTHONFRAMEWORK') != 'Python' or not (root/'Python').is_file():
    raise SystemExit('Build using a python.org macOS framework Python 3.10+; no private interpreter path is saved.')
target = Path(sys.argv[1]) / 'Contents/Resources/Python'
if target.exists():
    shutil.rmtree(target)
(target/'bin').mkdir(parents=True)
(target/'lib').mkdir()
# python.org's bin/python3 is a launcher that re-execs Python.app. Embed the
# real interpreter to avoid that external app path and an extra Dock entry.
interpreter = root/'Resources/Python.app/Contents/MacOS/Python'
shutil.copy2(interpreter if interpreter.exists() else Path(sys.executable).resolve(), target/'bin/python3')
shutil.copy2(root/'Python', target/'Python')
version = f'{sys.version_info.major}.{sys.version_info.minor}'
lib = 'python' + version
shutil.copytree(root/'lib'/lib, target/'lib'/lib, symlinks=True,
                ignore=shutil.ignore_patterns('__pycache__', '*.pyc', 'site-packages', 'test', 'tests',
                                             '_test*', 'idlelib', 'tkinter', '_tkinter*', 'turtledemo', 'ensurepip'))
for source in (root/'lib').glob('*.dylib'):
    if source.is_symlink():
        destination = Path(os.path.relpath(source.resolve(), root))
        (target/'lib'/source.name).symlink_to(os.path.relpath(target/destination, target/'lib'))
    else:
        shutil.copy2(source, target/'lib'/source.name)
shutil.copy2(root/'lib'/lib/'LICENSE.txt', target/'LICENSE.txt')
magic = (b'\xcf\xfa\xed\xfe', b'\xce\xfa\xed\xfe', b'\xfe\xed\xfa\xcf', b'\xca\xfe\xba\xbe', b'\xca\xfe\xba\xbf')
binaries = []
for path in target.rglob('*'):
    if not path.is_file() or path.is_symlink():
        continue
    with path.open('rb') as stream:
        if stream.read(4) in magic:
            binaries.append(path)
for path in binaries:
    dependencies = subprocess.check_output(['otool', '-L', str(path)], text=True)
    for line in dependencies.splitlines():
        if not line.startswith('\t'):
            continue
        name = line.strip().split(' (')[0]
        if name.startswith(str(root)+'/'):
            mapped = target/name[len(str(root))+1:]
            if not mapped.exists():
                raise SystemExit(f'Missing runtime dependency: {name}')
            replacement = '@loader_path/' + os.path.relpath(mapped, path.parent)
            subprocess.run(['install_name_tool', '-change', name, replacement, str(path)], check=True, capture_output=True)
        elif name.startswith('/') and not name.startswith(('/usr/lib/', '/System/Library/')):
            raise SystemExit(f'Unbundled external dependency: {name}')
    if path.name == 'Python' or path.suffix == '.dylib':
        subprocess.run(['install_name_tool','-id','@rpath/'+path.name,str(path)],check=True,capture_output=True)
    subprocess.run(['codesign','--force','--sign','-',str(path)],check=True,capture_output=True)
# The launch environment is private and explicit; no user site-packages are used.
env = {k:v for k,v in os.environ.items() if not k.startswith('PYTHON')}
env['PYTHONHOME'] = str(target)
subprocess.run([str(target/'bin/python3'), '-B', '-s', '-c',
                'import sqlite3,json,email,hashlib,zoneinfo,ssl;print("Bundled Python engine ready")'], env=env, check=True)
print(f'Embedded Python {version}: {len(binaries)} signed runtime binaries')
