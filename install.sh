#!/usr/bin/env bash
set -e

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_DIR="$HOME/.local/bin"
APP_DIR="$HOME/.local/share/applications"
ICON_DIR="$HOME/.local/share/icons/hicolor/256x256/apps"

echo "==> Installing Hayate for user: $USER"

mkdir -p "$BIN_DIR" "$APP_DIR" "$ICON_DIR"

# Install wrapper script
cat << EOF > "$BIN_DIR/hayate"
#!/usr/bin/env bash
export PYTHONPATH=\"$REPO_DIR/src:\\${PYTHONPATH}\"
exec python -m hayate.main \"\\$@\"
EOF
chmod +x "$BIN_DIR/hayate"
echo "  -> Installed launcher wrapper to $BIN_DIR/hayate"

# Install icon
if [ -f "$REPO_DIR/app_icon.png" ]; then
    cp "$REPO_DIR/app_icon.png" "$ICON_DIR/hayate.png"
    gtk-update-icon-cache "$HOME/.local/share/icons/hicolor" 2>/dev/null || true
    echo "  -> Installed icon to $ICON_DIR/hayate.png"
fi

# Install desktop entry
cat << EOF > "$APP_DIR/hayate.desktop"
[Desktop Entry]
Name=Hayate
Comment=Fast native anime catalog and desktop player
Exec=$BIN_DIR/hayate
Icon=hayate
Terminal=false
Type=Application
Categories=AudioVideo;Player;Network;
StartupWMClass=hayate
EOF

update-desktop-database "$APP_DIR" 2>/dev/null || true
echo "  -> Registered desktop entry at $APP_DIR/hayate.desktop"

echo "==> Installation complete! You can now run \x27hayate\x27 or search for Hayate in KRunner."
