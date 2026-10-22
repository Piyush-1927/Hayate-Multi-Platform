# Maintainer: Piyush
pkgname=hayate-git
pkgver=0.2.0
pkgrel=1
pkgdesc="Fast, native anime catalog and desktop streaming client"
arch=('any')
url="https://github.com/Piyush-1927/Hayate-Multi-Platform"
license=('GPL-3.0-or-later')
depends=(
  'python'
  'python-pyqt6'
  'python-mpv'
  'mpv'
  'python-requests'
  'yt-dlp'
  'python-httpx'
)
makedepends=(
  'git'
  'python-build'
  'python-installer'
  'python-setuptools'
  'python-wheel'
)
provides=('hayate')
conflicts=('hayate')
source=("git+https://github.com/Piyush-1927/Hayate-Multi-Platform.git")
sha256sums=('SKIP')

pkgver() {
  cd "$srcdir/Hayate-Multi-Platform"
  local version
  version=$(sed -n 's/^version = "\(.*\)"/\1/p' pyproject.toml | head -1)
  printf "%s" "$version"
}

build() {
  cd "$srcdir/Hayate-Multi-Platform"
  python -m build --wheel --no-isolation
}

package() {
  cd "$srcdir/Hayate-Multi-Platform"
  python -m installer --destdir="$pkgdir" dist/*.whl

  install -Dm644 app_icon.png \
    "$pkgdir/usr/share/icons/hicolor/256x256/apps/hayate.png"

  install -Dm644 /dev/stdin \
    "$pkgdir/usr/share/applications/hayate.desktop" <<'DESKTOP'
[Desktop Entry]
Name=Hayate
Comment=Fast native anime catalog and desktop player
Exec=hayate
Icon=hayate
Terminal=false
Type=Application
Categories=AudioVideo;Player;Network;
StartupWMClass=hayate
DESKTOP
}
