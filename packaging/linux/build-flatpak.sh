#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."
APP=io.github.Piyush_1927.Hayate
VERSION=$(sed -n 's/^version = "\(.*\)"/\1/p' pyproject.toml | head -1)
W="${HAYATE_FLATPAK_DIR:-$HOME/.cache/hayate-flatpak}"
mkdir -p "$W" dist
flatpak remote-add --user --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo
flatpak-builder --user --install-deps-from=flathub --force-clean \
  --state-dir="$W/state" --repo="$W/repo" "$W/build" "$APP.yaml" 2>&1 | tee "$W/build.log"
flatpak build-bundle --runtime-repo=https://dl.flathub.org/repo/flathub.flatpakrepo \
  "$W/repo" "dist/Hayate-$VERSION.flatpak" "$APP"
echo "bundle ready: dist/Hayate-$VERSION.flatpak"
