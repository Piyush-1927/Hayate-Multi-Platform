"""Comments in a separate process, so QtWebEngine never shares a process with the libmpv surface."""
import os
import subprocess
import sys

from hayate.core.comments_web import PAGE_URL, discussion_for


class CommentsLauncher:
    """Drop-in for CommentsCard: same set_media / toggle / close_card calls.
    C: closed -> open; open but behind Hayate -> bring forward; in front -> close."""

    def __init__(self, video=None, as_child=False):
        self._aid, self._proc, self._state = None, None, None
        self._ep = None
        try:
            from PyQt6.QtWidgets import QApplication
            QApplication.instance().applicationStateChanged.connect(self._hay_state)
        except Exception:
            pass

    def _hay_state(self, st):
        from PyQt6.QtCore import Qt
        if not self._alive():
            return
        m = "hon\n" if st == Qt.ApplicationState.ApplicationActive else "hoff\n"
        try:
            try:
                self._proc.stdin.write(m.encode())
            except TypeError:
                self._proc.stdin.write(m)
            self._proc.stdin.flush()
        except Exception:
            pass

    def _alive(self):
        return self._proc is not None and self._proc.poll() is None

    def _front(self):
        try:
            with open(self._state) as f:
                return f.read().strip() == "1"
        except Exception:
            return False

    def set_media(self, anilist_id, episode=None):
        aid = int(anilist_id) if anilist_id else None
        try:
            ep = int(episode) if episode not in (None, "") else None
        except (TypeError, ValueError):
            ep = None
        if aid != self._aid or ep != self._ep:
            self._aid, self._ep = aid, ep
            self.close_card()

    def toggle(self):
        if self._alive():
            self.close_card()
            return
        try:
            from hayate.ui.comments_card import theme
            c = theme.c("card_background")
            dark, bg, bd = c.lightness() < 128, c.name(), theme.c("card_border").name()
        except Exception:
            dark, bg, bd = True, "#1c1c28", "#3a3a50"
        n = discussion_for(self._aid)
        if n:
            arg = "%s?n=%d&theme=%s&a=%s&ep=%s" % (PAGE_URL, n, "transparent_dark" if dark else "light",
                                                   self._aid or "", self._ep or "")
        else:
            arg = "nodisc:%s" % (self._aid or "unknown")
        import tempfile
        self._state = os.path.join(tempfile.gettempdir(), "hayate_comments_%d.state" % os.getpid())
        try:
            with open(self._state, "w") as f:
                f.write("1")
            self._proc = subprocess.Popen(
                [sys.executable, "-m", "hayate.ui.comments_window", arg, bg, bd, self._state],
                stdin=subprocess.PIPE, env=os.environ.copy())
        except Exception:
            self._proc = None

    def close_card(self):
        if self._alive():
            try:
                self._proc.stdin.write(b"quit\n")
                self._proc.stdin.flush()
                self._proc.stdin.close()
            except Exception:
                try:
                    self._proc.terminate()
                except Exception:
                    pass
        self._proc = None
        try:
            if self._state:
                os.remove(self._state)
        except Exception:
            pass


def _cfg_file():
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "hayate", "comments_window.json")


def _load_geo():
    try:
        import json
        with open(_cfg_file()) as f:
            d = json.load(f)
        return [int(d[k]) for k in ("x", "y", "w", "h")]
    except Exception:
        return None


def _save_geo(x, y, w, h):
    try:
        import json
        p = _cfg_file()
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as f:
            json.dump({"x": x, "y": y, "w": w, "h": h}, f)
    except Exception:
        pass


def _main(arg, bg, bd, state):
    from PyQt6.QtCore import Qt, QTimer, QUrl
    from PyQt6.QtGui import QColor, QDesktopServices
    from PyQt6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile
    from PyQt6.QtWebEngineWidgets import QWebEngineView
    from PyQt6.QtWidgets import QApplication, QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

    app = QApplication(sys.argv)
    prof = QWebEngineProfile("hayate-comments", app)  # persistent: keeps the GitHub login
    col = QColor(bg)
    fg = "#e8e8f0" if col.lightness() < 128 else "#1a1a24"
    sub = "#9a9ab0" if col.lightness() < 128 else "#666677"

    class Page(QWebEnginePage):
        def acceptNavigationRequest(self, url, typ, main):
            if url.scheme() == "hayate-mail":
                from PyQt6.QtCore import QUrlQuery
                from hayate.core.comments_web import request_parts
                to, subj, body = request_parts(url.path().strip("/"))
                u = QUrl("mailto:" + to)
                q = QUrlQuery()
                q.addQueryItem("subject", subj)
                q.addQueryItem("body", body)
                u.setQuery(q)
                QDesktopServices.openUrl(u)
                return False
            if url.scheme() == "hayate-ls":
                from hayate.core.comments_web import ls_save
                ls_save(bytes(url.toEncoded()).decode())
                return False
            if url.scheme() == "hayate-report":
                from hayate.core.comments_web import report_mailto, open_mailto
                open_mailto(report_mailto(bytes(url.toEncoded()).decode()))
                return False
            if url.scheme() == "hayate-edit":
                open_editor(url.path().strip("/"))
                return False
            return super().acceptNavigationRequest(url, typ, main)

        def createWindow(self, _t):
            tmp = QWebEnginePage(self.profile(), self)

            def go(u):
                if u.scheme() in ("http", "https"):
                    QDesktopServices.openUrl(u)
                    tmp.deleteLater()
            tmp.urlChanged.connect(go)
            return tmp

    class Hdr(QWidget):
        def mousePressEvent(self, e):
            if e.button() == Qt.MouseButton.LeftButton and self.window().windowHandle():
                self.window().windowHandle().startSystemMove()

    class Grip(QLabel):
        def __init__(self):
            super().__init__("\u25e2")
            self.setFixedSize(16, 14)
            self.setCursor(Qt.CursorShape.SizeFDiagCursor)
            self.setStyleSheet("color:%s; background:transparent; border:none; font-size:10px;" % sub)

        def mousePressEvent(self, e):
            if e.button() == Qt.MouseButton.LeftButton and self.window().windowHandle():
                self.window().windowHandle().startSystemResize(Qt.Edge.RightEdge | Qt.Edge.BottomEdge)

    class Win(QWidget):
        M = 6

        def __init__(self):
            super().__init__()
            self.setMouseTracking(True)

        def resizeEvent(self, ev):
            super().resizeEvent(ev)
            for g in getattr(self, '_grips', []):
                g.place()

        def _edges(self, pos):
            m, e = self.M, Qt.Edge(0)
            if pos.x() <= m:
                e |= Qt.Edge.LeftEdge
            if pos.x() >= self.width() - m:
                e |= Qt.Edge.RightEdge
            if pos.y() <= m:
                e |= Qt.Edge.TopEdge
            if pos.y() >= self.height() - m:
                e |= Qt.Edge.BottomEdge
            return e

        def mouseMoveEvent(self, ev):
            e = self._edges(ev.position().toPoint())
            L, R, T, B = Qt.Edge.LeftEdge, Qt.Edge.RightEdge, Qt.Edge.TopEdge, Qt.Edge.BottomEdge
            if e in (L | T, R | B):
                self.setCursor(Qt.CursorShape.SizeFDiagCursor)
            elif e in (R | T, L | B):
                self.setCursor(Qt.CursorShape.SizeBDiagCursor)
            elif e in (L, R):
                self.setCursor(Qt.CursorShape.SizeHorCursor)
            elif e in (T, B):
                self.setCursor(Qt.CursorShape.SizeVerCursor)
            else:
                self.unsetCursor()

        def mousePressEvent(self, ev):
            e = self._edges(ev.position().toPoint())
            if ev.button() == Qt.MouseButton.LeftButton and e and self.windowHandle():
                self.windowHandle().startSystemResize(e)

    win = Win()
    win.setWindowTitle("Hayate Comments")
    win.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
    win.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
    from PyQt6.QtCore import QRect
    w, h = 440, 640
    scr = app.primaryScreen().availableGeometry()
    x, y = scr.right() - w - 19, scr.top() + 20  # default: top-right
    g = _load_geo()
    if g and any(sc.availableGeometry().intersects(QRect(g[0], g[1], g[2], 40)) for sc in app.screens()):
        x, y, w, h = g[0], g[1], max(320, g[2]), max(360, g[3])
    win.resize(w, h)
    win.setMinimumSize(320, 360)
    win.move(x, y)
    outer = QVBoxLayout(win)
    outer.setContentsMargins(6, 6, 6, 6)
    frame = QFrame()
    frame.setObjectName("f")
    frame.setStyleSheet("QFrame#f { background: rgba(%d,%d,%d,0.92); border: 1px solid %s; border-radius: 12px; }"
                        % (col.red(), col.green(), col.blue(), bd))
    outer.addWidget(frame)
    v = QVBoxLayout(frame)
    v.setContentsMargins(1, 1, 1, 1)
    v.setSpacing(0)

    hdr = Hdr()
    hdr.setFixedHeight(34)
    hl = QHBoxLayout(hdr)
    hl.setContentsMargins(14, 0, 8, 0)
    t = QLabel("Comments")
    t.setStyleSheet("color:%s; font-size:12px; font-weight:700; background:transparent; border:none;" % fg)
    x = QPushButton("\u2715")
    x.setFixedSize(24, 24)
    x.setCursor(Qt.CursorShape.PointingHandCursor)
    x.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    x.setStyleSheet("QPushButton { color:%s; background:transparent; border:none; border-radius:6px; font-size:12px; }"
                    "QPushButton:hover { background: rgba(255,80,80,0.35); color:#fff; }" % sub)
    x.clicked.connect(app.quit)
    hl.addWidget(t)
    hl.addStretch(1)
    hl.addWidget(x)
    v.addWidget(hdr)

    loading = QLabel("Loading comments\u2026")
    loading.setAlignment(Qt.AlignmentFlag.AlignCenter)
    loading.setStyleSheet("color:%s; font-size:13px; font-weight:600; background:transparent; border:none;" % sub)
    v.addWidget(loading)

    view = QWebEngineView()
    view.setPage(Page(prof, view))
    view.setStyleSheet("background: transparent; border: none;")
    view.page().setBackgroundColor(QColor(0, 0, 0, 0))
    v.addWidget(view, 1)

    foot = QHBoxLayout()
    foot.setContentsMargins(0, 0, 4, 2)
    foot.addStretch(1)
    v.addLayout(foot)

    class CornerGrip(QWidget):
        S = 14

        def __init__(self, corner):
            super().__init__(win)
            self.corner = corner
            L, R, T, B = Qt.Edge.LeftEdge, Qt.Edge.RightEdge, Qt.Edge.TopEdge, Qt.Edge.BottomEdge
            self.edges = {"tl": L | T, "tr": R | T, "bl": L | B, "br": R | B}[corner]
            self.setFixedSize(self.S, self.S)
            self.setCursor(Qt.CursorShape.SizeFDiagCursor if corner in ("tl", "br")
                           else Qt.CursorShape.SizeBDiagCursor)

        def place(self):
            x = 0 if self.corner in ("tl", "bl") else win.width() - self.S
            y = 0 if self.corner in ("tl", "tr") else win.height() - self.S
            self.move(x, y)
            self.raise_()

        def mousePressEvent(self, e):
            if e.button() == Qt.MouseButton.LeftButton and win.windowHandle():
                win.windowHandle().startSystemResize(self.edges)

    win._grips = [CornerGrip(c) for c in ("tl", "tr", "bl", "br")]
    for g in win._grips:
        g.place()
        g.show()

    class EditLink(QLabel):
        def __init__(self, url):
            super().__init__("Edit on GitHub \u2197", win)
            self.url = url
            self.setCursor(Qt.CursorShape.PointingHandCursor)
            self.setStyleSheet("color:%s; background:transparent; border:none; font-size:11px;" % sub)
            self.adjustSize()

        def place(self):
            self.move(win.width() - self.width() - 44, 10)
            self.raise_()

        def mousePressEvent(self, e):
            if e.button() == Qt.MouseButton.LeftButton:
                from PyQt6.QtGui import QDesktopServices
                QDesktopServices.openUrl(QUrl(self.url))


    from PyQt6.QtWebEngineCore import QWebEngineScript
    _css = ("html{color-scheme:dark}::-webkit-scrollbar{width:8px;height:8px;background:transparent}"
            "::-webkit-scrollbar-track,::-webkit-scrollbar-corner{background:transparent}"
            "::-webkit-scrollbar-button{display:none}"
            "::-webkit-scrollbar-thumb{background:rgba(255,255,255,.22);border-radius:4px}"
            "::-webkit-scrollbar-thumb:hover{background:rgba(255,255,255,.38)}")
    _sc = QWebEngineScript()
    _sc.setName("hayate-scrollbar")
    _sc.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentReady)
    _sc.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
    _sc.setRunsOnSubFrames(True)
    _sc.setSourceCode("(function(){var s=document.createElement('style');s.textContent='" + _css
                      + "';(document.head||document.documentElement).appendChild(s);})();")
    view.page().scripts().insert(_sc)

    from hayate.core.comments_web import CARD_JS as _CARD_JS
    _GIT_JS = '(function(){\n var id="__ID__",n=0,st=0;\n function vis(e){var r=e.getBoundingClientRect();return r.width>0&&r.height>0;}\n function findEdit(){\n  var sels=[\'[role="menuitem"]\',\'button\',\'a\',\'li\'];\n  for(var k=0;k<sels.length;k++){\n   var it=document.querySelectorAll(sels[k]);\n   for(var i=0;i<it.length;i++){\n    if(vis(it[i])&&(it[i].textContent||"").trim()==="__ACT__")return it[i];\n   }\n  }\n  return null;\n }\n function tick(){\n  if(st===3||++n>16)return;\n  if(st===0){\n   var c=document.getElementById("discussioncomment-"+id);\n   if(c){\n    c.scrollIntoView({block:"center"});\n    var b=null,bs=c.querySelectorAll("button,summary");\n    for(var k=0;k<bs.length;k++){\n     var t=bs[k],al=(t.getAttribute("aria-label")||"");\n     if(/reaction/i.test(al))continue;\n     if(t.querySelector("svg.octicon-kebab-horizontal")){b=t;break;}\n    }\n    if(b){st=1;n=0;b.click();}\n   }\n  }else if(st===1){\n   var e=findEdit();\n   if(e){st=3;e.click();return;}\n  }\n  setTimeout(tick,300);\n }\n setTimeout(tick,600);\n})();'
    _edit_wins = []

    def open_editor(cid):
        import urllib.parse as _up
        act = "Edit"
        if cid.startswith("del:"):
            act, cid = "Delete", cid[4:]
        elif cid.startswith("rep:"):
            act, cid = "Report content", cid[4:]
        disc = None
        if "-" in cid:
            disc, cid = cid.split("-", 1)
        _n = [disc] if disc else _up.parse_qs(_up.urlparse(arg).query).get("n")
        if not _n or not cid.isdigit():
            return
        ev = QWebEngineView()
        ev.setPage(QWebEnginePage(prof, ev))
        sc = QWebEngineScript()
        sc.setName("hayate-edit")
        sc.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentReady)
        sc.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
        sc.setRunsOnSubFrames(False)
        sc.setSourceCode(_GIT_JS.replace("__ID__", cid).replace("__ACT__", act))
        ev.page().scripts().insert(sc)
        ev.setWindowTitle("%s - GitHub" % act)
        ev.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        ev.resize(1100, 850)
        ev.load(QUrl("https://github.com/Piyush-1927/hayate-comments/discussions/%s#discussioncomment-%s" % (_n[0], cid)))
        ev.show()
        ev.raise_()
        ev.activateWindow()
        _edit_wins.append(ev)

    _cs = QWebEngineScript()
    _cs.setName("hayate-editbtn")
    _cs.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentReady)
    _cs.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
    _cs.setRunsOnSubFrames(True)
    _cs.setSourceCode(_CARD_JS)
    _ls = QWebEngineScript()
    _ls.setName("hayate-login-sync")
    _ls.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentCreation)
    _ls.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
    _ls.setRunsOnSubFrames(True)
    from hayate.core.comments_web import ls_script
    _ls.setSourceCode(ls_script())
    view.page().scripts().insert(_ls)
    view.page().scripts().insert(_cs)

    if arg.startswith("nodisc:"):
        loading.hide()
        from html import escape as _esc
        _aid = arg[7:]
        view.setHtml(
            "<body style='background:transparent;color:%s;font:14px sans-serif;text-align:center;padding:36px 20px 0'>"
            "<div>No discussion for this anime yet.<br>"
            "<span style='color:%s;font-size:12px'>(AniList id: %s)</span></div>"
            "<p style='margin:18px 0 6px'>Send an email to the developer to start a discussion for this anime.</p>"
            "<a href='%s' style='display:inline-block;margin-top:8px;padding:9px 18px;border-radius:8px;"
            "background:#2aa9f0;color:#fff;text-decoration:none;font-weight:600'>"
            "&#9993; Email developer to start a discussion</a>"
            "<p style='color:%s;font-size:12px'>Opens your email app with the AniList id filled in.</p></body>"
            % (fg, sub, _esc(_aid), _esc("hayate-mail:" + _aid, quote=True), sub))
    elif arg.startswith("msg:"):
        loading.hide()
        view.setHtml("<body style='background:transparent;color:%s;font:14px sans-serif;text-align:center;"
                     "padding-top:40px'>%s</body>" % (fg, arg[4:].replace("\n", "<br>")))
    else:
        view.loadFinished.connect(lambda _ok: loading.hide())
        view.load(QUrl(arg))
    win.show()


    from PyQt6.QtCore import QEvent, QObject

    class DragFilter(QObject):
        def eventFilter(self, o, ev):
            if ev.type() == QEvent.Type.MouseMove and isinstance(o, QWidget) and o.window() is win:
                L, R, T, B = Qt.Edge.LeftEdge, Qt.Edge.RightEdge, Qt.Edge.TopEdge, Qt.Edge.BottomEdge
                F, K, H, V = (Qt.CursorShape.SizeFDiagCursor, Qt.CursorShape.SizeBDiagCursor,
                              Qt.CursorShape.SizeHorCursor, Qt.CursorShape.SizeVerCursor)
                cur = {L | T: F, R | B: F, R | T: K, L | B: K, L: H, R: H, T: V, B: V}.get(
                    win._edges(win.mapFromGlobal(ev.globalPosition().toPoint())))
                win.setCursor(cur) if cur is not None else win.unsetCursor()
                return False
            if ev.type() != QEvent.Type.MouseButtonPress or ev.button() != Qt.MouseButton.LeftButton:
                return False
            if not isinstance(o, QWidget) or o.window() is not win:
                return False
            wh = win.windowHandle()
            if not wh:
                return False
            ed = win._edges(win.mapFromGlobal(ev.globalPosition().toPoint()))
            if ed:
                wh.startSystemResize(ed)
                return True
            if isinstance(o, (CornerGrip, Grip, EditLink)):
                return False
            if isinstance(o, QPushButton) or o is view or view.isAncestorOf(o):
                return False
            wh.startSystemMove()
            return True

    win._drag_filter = DragFilter()
    app.installEventFilter(win._drag_filter)

    save_t = QTimer()
    save_t.setSingleShot(True)
    save_t.setInterval(300)
    save_t.timeout.connect(lambda: _save_geo(win.x(), win.y(), win.width(), win.height()))

    class GeoWatch(QObject):
        def eventFilter(self, o, ev):
            if ev.type() in (QEvent.Type.Move, QEvent.Type.Resize):
                save_t.start()
            return False

    win._geo_watch = GeoWatch()
    win.installEventFilter(win._geo_watch)
    app.aboutToQuit.connect(lambda: _save_geo(win.x(), win.y(), win.width(), win.height()))

    class KeyClose(QObject):
        def eventFilter(self, o, ev):
            if (ev.type() == QEvent.Type.KeyPress and ev.key() in (Qt.Key.Key_C, Qt.Key.Key_Escape)
                    and not ev.modifiers() and isinstance(o, QWidget) and o.window() is win):
                view.page().runJavaScript(
                    "(function(){var e=document.activeElement;if(!e)return false;"
                    "var t=e.tagName;return t==='IFRAME'||t==='INPUT'||t==='TEXTAREA'"
                    "||t==='SELECT'||!!e.isContentEditable;})()",
                    lambda typing: None if typing else app.quit())
            return False

    win._key_close = KeyClose()
    app.installEventFilter(win._key_close)

    import threading
    import time
    from PyQt6.QtCore import QObject, pyqtSignal

    def setst(v):
        try:
            with open(state, "w") as f:
                f.write(v)
        except Exception:
            pass

    class Cmd(QObject):
        raise_it = pyqtSignal()
        gone = pyqtSignal()
        hay = pyqtSignal(bool)

    cmd, last = Cmd(), [0.0]

    def bring():
        last[0] = time.monotonic()
        win.hide()
        win.show()
        win.raise_()
        win.activateWindow()
        setst("1")

    app.setQuitOnLastWindowClosed(False)
    flags = {"me": True, "hay": True, "hid": False}
    chk = QTimer()
    chk.setSingleShot(True)

    def evaluate():
        if time.monotonic() - last[0] < 0.8:
            chk.start(300)
            return
        if flags["me"] or flags["hay"]:
            if flags["hid"]:
                flags["hid"] = False
                win.show()
                win.raise_()
        elif win.isVisible():
            flags["hid"] = True
            win.hide()
            setst("0")

    chk.timeout.connect(evaluate)
    _b = bring

    def bring():
        flags["hid"] = False
        _b()

    def on_state(st):
        flags["me"] = st == Qt.ApplicationState.ApplicationActive
        chk.start(300)
        if time.monotonic() - last[0] < 0.6:  # ignore the hide/show flicker
            return
        setst("1" if flags["me"] else "0")

    cmd.raise_it.connect(bring)
    QTimer.singleShot(150, bring)
    cmd.gone.connect(app.quit)
    cmd.hay.connect(lambda v: (flags.__setitem__("hay", v), chk.start(300)))
    app.applicationStateChanged.connect(on_state)
    setst("1")

    def reader():
        for line in sys.stdin:
            if line.strip() == "raise":
                cmd.raise_it.emit()
            elif line.strip() == "hon":
                cmd.hay.emit(True)
            elif line.strip() == "hoff":
                cmd.hay.emit(False)
            elif line.strip() == "quit":
                break
        cmd.gone.emit()  # Hayate closed the pipe

    threading.Thread(target=reader, daemon=True).start()

    ppid = os.getppid()
    tm = QTimer()  # quit if Hayate dies (reparenting detection works on Linux/macOS)
    tm.timeout.connect(lambda: app.quit() if os.getppid() != ppid else None)
    tm.start(2000)
    rc = app.exec()
    try:
        from PyQt6 import sip
        pg = view.page()
        for o in (view, pg, prof):
            if not sip.isdeleted(o):
                sip.delete(o)
    except Exception:
        pass
    os._exit(rc)


if __name__ == "__main__":
    _main(sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4])
