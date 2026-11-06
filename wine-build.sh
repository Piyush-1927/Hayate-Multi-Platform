#!/usr/bin/env bash
# usage: ./wine-build.sh [--console|--windowed]   (use --console first: shows tracebacks)
set -e
export WINEPREFIX=~/.wine-hayate WINEARCH=win64 WINEDEBUG=-all
R="$(pwd)"; W() { winepath -w "$R/$1"; }
PY="wine $WINEPREFIX/drive_c/users/$USER/AppData/Local/Programs/Python/Python312/python.exe"
$PY -m pip install -r requirements.txt pyinstaller pillow
rm -rf build dist
$PY -m PyInstaller --noconfirm --clean ${1:---console} --name Hayate \
  --icon "$(W packaging/windows/hayate.ico)" \
  --add-data "$(W src/hayate/assets);hayate/assets" \
  --add-data "$(W packaging/windows/hayate.ico);." \
  --add-data "$(W app_icon.png);." \
  --hidden-import PyQt6.QtSvg \
  --paths "$(W src)" --collect-submodules hayate \
  "$(W packaging/windows/launcher.py)"
cp app_icon.png dist/Hayate/
curl -L -o dist/Hayate/yt-dlp.exe https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe
for f in packaging/windows/*mpv*.dll; do cp "$f" dist/Hayate/; cp "$f" dist/Hayate/_internal/ 2>/dev/null || true; done
python3 -c "import shutil; shutil.make_archive('dist/Hayate-Windows-portable','zip','dist','Hayate')"
ISCC="$WINEPREFIX/drive_c/users/$USER/AppData/Local/Programs/Inno Setup 6/ISCC.exe"
[ -f "$ISCC" ] || ISCC="$WINEPREFIX/drive_c/Program Files (x86)/Inno Setup 6/ISCC.exe"
wine "$ISCC" "$(W packaging/windows/hayate.iss)"
ls -la dist
