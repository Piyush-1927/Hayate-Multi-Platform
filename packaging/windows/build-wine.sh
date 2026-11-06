#!/usr/bin/env bash
set -e
export WINEPREFIX=~/.wine-hayate WINEDEBUG=-all
cd "$(dirname "$0")/../.."
PY='C:\users\panther\AppData\Local\Programs\Python\Python312\python.exe'
VPY=.build-venv/Scripts/python.exe

[ -d .build-venv ] || wine "$PY" -m venv .build-venv
wine $VPY -m pip install --upgrade pip
wine $VPY -m pip install -r requirements.txt pyinstaller pillow

rm -rf build dist
wine $VPY -m PyInstaller --noconfirm --clean --windowed --name Hayate \
  --icon packaging/windows/hayate.ico \
  --add-data "src/hayate/assets;hayate/assets" \
  --add-data "packaging/windows/hayate.ico;." \
  --add-data "app_icon.png;." \
  --hidden-import PyQt6.QtSvg \
  --paths src --collect-submodules hayate \
  packaging/windows/launcher.py

out=dist/Hayate
cp app_icon.png $out/
curl -L -o $out/yt-dlp.exe https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe
for f in packaging/windows/*mpv*.dll; do
  cp "$f" $out/
  [ -d $out/_internal ] && cp "$f" $out/_internal/
done
(cd dist && zip -qr Hayate-Windows-portable.zip Hayate)

ISCC=$(find "$WINEPREFIX/drive_c" -iname ISCC.exe 2>/dev/null | head -1)
if [ -n "$ISCC" ]; then wine "$ISCC" packaging/windows/hayate.iss; else echo "Inno Setup not found - portable zip only"; fi
echo "Done: dist/"
