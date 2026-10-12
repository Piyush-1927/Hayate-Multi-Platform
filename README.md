# Hayate

Fast, modern, native anime catalog and desktop streaming client built with PyQt6.

## Features
- **AniList Catalog**: Browse trending shows, seasonal releases, and detailed release schedules.
- **Embedded Player**: Hardware-accelerated native playback with MPV / FFmpeg integration.
- **Local Network Sync**: Built-in LAN server and room discovery to stream directly between devices on the same Wi-Fi network.
- **Clean Configuration**: Fully isolated configuration and watch history located in `~/.config/hayate/`.

## Running Locally
```bash
git clone https://github.com/Piyush-1927/Hayate.git
cd Hayate
PYTHONPATH=src python -m hayate.main
```

## Desktop Integration
```bash
mkdir -p ~/.local/bin ~/.local/share/applications ~/.local/share/icons/hicolor/256x256/apps
cp app_icon.png ~/.local/share/icons/hicolor/256x256/apps/hayate.png
gtk-update-icon-cache ~/.local/share/icons/hicolor 2>/dev/null || true
cp hayate.desktop ~/.local/share/applications/
update-desktop-database ~/.local/share/applications/ 2>/dev/null || true
```
