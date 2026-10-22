# -*- mode: python ; coding: utf-8 -*-
import os
from PyInstaller.utils.hooks import collect_submodules

ROOT = SPECPATH  # this spec lives in the repo root

hiddenimports = []
hiddenimports += collect_submodules('hayate')


a = Analysis(
    [os.path.join(ROOT, 'packaging', 'windows', 'launcher.py')],
    pathex=[os.path.join(ROOT, 'src')],
    binaries=[],
    datas=[],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Hayate',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=[os.path.join(ROOT, 'app_icon.png')],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='Hayate',
)
