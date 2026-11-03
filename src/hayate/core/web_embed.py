"""Optional embedded web player (QtWebEngine + Widevine). WEB_OK is False when unavailable."""
import glob, os

WEB_OK = False
WebPane = None
_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
       "Chrome/124.0.0.0 Safari/537.36")


def _find_cdm():
    c = ["/usr/lib/chromium/WidevineCdm/_platform_specific/linux_x64/libwidevinecdm.so",
         "/opt/google/chrome/WidevineCdm/_platform_specific/linux_x64/libwidevinecdm.so"]
    c += glob.glob("/usr/lib/**/libwidevinecdm.so", recursive=True)
    c += sorted(glob.glob(os.path.expanduser("~/.config/*/**/libwidevinecdm.so"), recursive=True), reverse=True)
    return next((p for p in c if os.path.exists(p)), None)


_cdm = _find_cdm() if os.name == "posix" else None
if _cdm:
    os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = (
        os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", "") + f" --widevine-path={_cdm}").strip()
    try:
        from PyQt6.QtCore import QUrl
        from PyQt6.QtWebEngineCore import QWebEngineProfile, QWebEnginePage, QWebEngineSettings
        from PyQt6.QtWebEngineWidgets import QWebEngineView
        WEB_OK = True
    except Exception:
        WEB_OK = False

if WEB_OK:
    _PROFILE = None

    def _profile():
        global _PROFILE
        if _PROFILE is None:
            p = QWebEngineProfile("hayate-disney")
            p.setPersistentStoragePath(os.path.expanduser("~/.local/share/hayate/webplayer"))
            p.setHttpUserAgent(_UA)
            A = QWebEngineSettings.WebAttribute
            for a in (A.PlaybackRequiresUserGesture,):
                p.settings().setAttribute(a, False)
            for a in (A.FullScreenSupportEnabled, A.PluginsEnabled):
                p.settings().setAttribute(a, True)
            _PROFILE = p
        return _PROFILE

    class WebPane(QWebEngineView):
        def __init__(self, owner):
            super().__init__(owner)
            self._owner = owner
            self.setPage(QWebEnginePage(_profile(), self))
            self.page().fullScreenRequested.connect(self._fs)

        def _fs(self, req):
            req.accept()
            if req.toggleOn() != self._owner.window().isFullScreen():
                self._owner._toggle_fullscreen()

        def open(self, url):
            self.load(QUrl(url))
