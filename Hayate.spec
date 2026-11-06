# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_submodules

hiddenimports = ['PyQt6.QtSvg']
hiddenimports += collect_submodules('hayate')


a = Analysis(
    ['E:/home/panther/Projects/Hayate-Multi-Platform/packaging/windows/launcher.py'],
    pathex=['E:/home/panther/Projects/Hayate-Multi-Platform/src'],
    binaries=[],
    datas=[('E:/home/panther/Projects/Hayate-Multi-Platform/src/hayate/assets', 'hayate/assets'), ('E:/home/panther/Projects/Hayate-Multi-Platform/packaging/windows/hayate.ico', '.'), ('E:/home/panther/Projects/Hayate-Multi-Platform/app_icon.png', '.')],
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
    icon=['E:/home/panther/Projects/Hayate-Multi-Platform/packaging/windows/hayate.ico'],
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
