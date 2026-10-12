# Maintainer: Piyush <https://github.com/Piyush-1927>
pkgname=hayate-git
pkgver=0.1.0
pkgrel=1
pkgdesc="Fast, native anime catalog and desktop streaming client"
arch=('any')
url="https://github.com/Piyush-1927/Hayate"
license=('GPL-3.0-or-later')
depends=('python' 'python-pyqt6' 'python-requests' 'yt-dlp' 'qt6-multimedia-ffmpeg')
makedepends=('git' 'python-build' 'python-installer' 'python-setuptools' 'python-wheel')
provides=('hayate')
conflicts=('hayate')
source=("git+https://github.com/Piyush-1927/Hayate.git")
sha256sums=('SKIP')

pkgver() {
  cd "$srcdir/Hayate"
  printf "0.1.0.r%s.%s" "$(git rev-list --count HEAD)" "$(git rev-parse --short HEAD)"
}

build() {
  cd "$srcdir/Hayate"
  python -m build --wheel --no-isolation
}

package() {
  cd "$srcdir/Hayate"
  python -m installer --destdir="$pkgdir" dist/*.whl

  # Install icon
  install -Dm644 app_icon.png "$pkgdir/usr/share/icons/hicolor/256x256/apps/hayate.png"

  # Install desktop entry
  install -Dm644 /dev/stdin "$pkgdir/usr/share/applications/hayate.desktop" << 'DESKTOP'
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
