"""Inline giscus comments for the merged player page (GL video path only)."""
from PyQt6.QtCore import QEvent, QObject, Qt, QTimer, QUrl, QUrlQuery
from PyQt6.QtGui import QColor, QDesktopServices
from PyQt6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineScript
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWidgets import QApplication, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from hayate.core.comments_web import PAGE_URL, discussion_for, report_mailto, request_parts, open_mailto

_PROFILE = None


def _profile():
    global _PROFILE
    if _PROFILE is None:
        _PROFILE = QWebEngineProfile("hayate-comments", QApplication.instance())  # persistent: keeps login
    return _PROFILE


def _dark():
    try:
        from hayate.ui.comments_card import theme
        return theme.c("card_background").lightness() < 128
    except Exception:
        return True


from hayate.core.comments_web import CARD_JS as _CARD_JS
_GIT_JS = '(function(){\n var id="__ID__",n=0,st=0;\n function vis(e){var r=e.getBoundingClientRect();return r.width>0&&r.height>0;}\n function findEdit(){\n  var sels=[\'[role="menuitem"]\',\'button\',\'a\',\'li\'];\n  for(var k=0;k<sels.length;k++){\n   var it=document.querySelectorAll(sels[k]);\n   for(var i=0;i<it.length;i++){\n    if(vis(it[i])&&(it[i].textContent||"").trim()==="__ACT__")return it[i];\n   }\n  }\n  return null;\n }\n function tick(){\n  if(st===3||++n>16)return;\n  if(st===0){\n   var c=document.getElementById("discussioncomment-"+id);\n   if(c){\n    c.scrollIntoView({block:"center"});\n    var b=null,bs=c.querySelectorAll("button,summary");\n    for(var k=0;k<bs.length;k++){\n     var t=bs[k],al=(t.getAttribute("aria-label")||"");\n     if(/reaction/i.test(al))continue;\n     if(t.querySelector("svg.octicon-kebab-horizontal")){b=t;break;}\n    }\n    if(b){st=1;n=0;b.click();}\n   }\n  }else if(st===1){\n   var e=findEdit();\n   if(e){st=3;e.click();return;}\n  }\n  setTimeout(tick,300);\n }\n setTimeout(tick,600);\n})();'


class _Page(QWebEnginePage):
    def acceptNavigationRequest(self, url, typ, main):
        if url.scheme() == "hayate-mail":
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
            open_mailto(report_mailto(bytes(url.toEncoded()).decode()))
            return False
        if url.scheme() == "hayate-edit":
            cb = getattr(self, "on_edit", None)
            if cb:
                cb(url.path().strip("/"))
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


class InlineComments(QWidget):
    MIN_H = 300

    def __init__(self, scroll_getter, media_getter, parent=None):
        super().__init__(parent)
        self._sc, self._media = scroll_getter, media_getter
        self._cur = None
        self._content_h = self.MIN_H
        self.setStyleSheet("background: transparent;")

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 18, 0, 0)
        lay.setSpacing(8)
        head = QHBoxLayout()
        title = QLabel("Comments")
        title.setStyleSheet("font-size: 18px; font-weight: 700; color: #ffffff; background: transparent;")
        self._ep = QLabel()
        self._ep.setStyleSheet("font-size: 12px; color: #94a3b8; background: transparent;"
                               " border: 1px solid #3a3a50; border-radius: 12px; padding: 3px 12px;")
        self._ep.hide()
        head.addWidget(title)
        head.addWidget(self._ep)
        head.addStretch(1)
        lay.addLayout(head)

        self._view = QWebEngineView(self)
        self._edit_wins = []
        self._closing = False
        pg = _Page(_profile(), self._view)
        pg.on_edit = self._open_editor
        self._view.setPage(pg)
        self._view.setStyleSheet("background: transparent; border: none;")
        self._view.page().setBackgroundColor(QColor(0, 0, 0, 0))
        self._view.setFixedHeight(self.MIN_H)
        s = QWebEngineScript()
        s.setName("hayate-inline-css")
        s.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentReady)
        s.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
        s.setRunsOnSubFrames(False)
        s.setSourceCode("(function(){var s=document.createElement('style');"
                        "s.textContent='html,body{overflow:hidden!important;height:auto!important;min-height:0!important}iframe.giscus-frame{max-height:none!important}';"
                        "(document.head||document.documentElement).appendChild(s);})();")
        self._view.page().scripts().insert(s)
        cs = QWebEngineScript()
        cs.setName("hayate-editbtn")
        cs.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentReady)
        cs.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
        cs.setRunsOnSubFrames(True)
        cs.setSourceCode(_CARD_JS)
        _ls = QWebEngineScript()
        _ls.setName("hayate-login-sync")
        _ls.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentCreation)
        _ls.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
        _ls.setRunsOnSubFrames(True)
        from hayate.core.comments_web import ls_script
        _ls.setSourceCode(ls_script())
        self._view.page().scripts().insert(_ls)
        self._view.page().scripts().insert(cs)
        self._view.loadFinished.connect(self._hook_wheel)
        self._view.installEventFilter(self)
        lay.addWidget(self._view)

        self._tick = QTimer(self)
        self._tick.setInterval(800)
        self._tick.timeout.connect(self._poll)
        self._tick.start()
        self._mt = QTimer(self)
        self._mt.setInterval(500)
        self._mt.timeout.connect(self._measure)
        self._mt.start()
        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self._teardown)

    # ----- sizing / wheel -----
    def _on_size(self, sz):
        h = max(self.MIN_H, int(sz.height()))
        if abs(h - self._content_h) > 1:
            self._content_h = h
            self._view.setFixedHeight(h)

    _JS = ("(function(){var d=document,b=d.body;if(!b)return 0;"
           "var h=b.getBoundingClientRect().bottom+(window.scrollY||0);"
           "var f=d.querySelector('iframe.giscus-frame');"
           "if(f){var r=f.getBoundingClientRect(),sh=parseFloat(f.style.height)||0;"
           "h=Math.max(h,r.top+(window.scrollY||0)+Math.max(sh,r.height));}"
           "return Math.ceil(h)+8;})()")

    def _measure(self):
        if self._cur is None or not self.isVisible():
            return
        self._view.page().runJavaScript(self._JS, self._on_h)

    def _on_h(self, h):
        try:
            h = max(self.MIN_H, int(h))
        except (TypeError, ValueError):
            return
        if abs(h - self._content_h) > 1:
            self._content_h = h
            self._view.setFixedHeight(h)

    def _hook_wheel(self, _ok=True):
        for w in [self._view.focusProxy()] + self._view.findChildren(QWidget):
            if w is not None:
                w.installEventFilter(self)

    def eventFilter(self, o, ev):
        t = ev.type()
        if t == QEvent.Type.ChildAdded and o is self._view:
            c = ev.child()
            if isinstance(c, QWidget):
                c.installEventFilter(self)
        elif t == QEvent.Type.Wheel and not ev.modifiers():
            sc = self._sc()
            if sc is not None:
                QApplication.sendEvent(sc.viewport(), ev)  # page scrolls, not the web view
                return True
        return False

    # ----- loading -----
    def _media_now(self):
        aid, ep = self._media()
        try:
            aid = int(aid) if aid else None
        except (TypeError, ValueError):
            aid = None
        try:
            ep = int(ep) if ep not in (None, "") else None
        except (TypeError, ValueError):
            ep = None
        return aid, ep

    def _near_view(self):
        sc = self._sc()
        if sc is None:
            return True
        top = self.mapTo(sc.viewport(), self.rect().topLeft()).y()
        return top < sc.viewport().height() + 300

    def _poll(self):
        if not self.isVisible():
            return
        m = self._media_now()
        self._ep.setText("Episode %d" % m[1] if m[1] else "")
        self._ep.setVisible(bool(m[1]))
        if m != self._cur and self._near_view():
            self.load_now()

    def load_now(self):
        m = self._media_now()
        if m == self._cur:
            return
        self._cur = m
        aid, ep = m
        self._content_h = self.MIN_H
        self._view.setFixedHeight(self.MIN_H)
        dark = _dark()
        fg, sub = ("#e8e8f0", "#9a9ab0") if dark else ("#1a1a24", "#666677")
        n = discussion_for(aid)
        if not aid:
            self._view.setHtml("<body style='background:transparent;color:%s;font:14px sans-serif;"
                               "text-align:center;padding-top:40px'>Comments are unavailable.</body>" % fg)
        elif n:
            self._view.load(QUrl("%s?n=%d&theme=%s&a=%s&ep=%s" % (
                PAGE_URL, n, "transparent_dark" if dark else "light", aid, ep or "")))
        else:
            from html import escape as _esc
            self._view.setHtml(
                "<body style='background:transparent;color:%s;font:14px sans-serif;text-align:center;padding:36px 20px 0'>"
                "<div>No discussion for this anime yet.<br>"
                "<span style='color:%s;font-size:12px'>(AniList id: %s)</span></div>"
                "<p style='margin:18px 0 6px'>Send an email to the developer to start a discussion for this anime.</p>"
                "<a href='%s' style='display:inline-block;margin-top:8px;padding:9px 18px;border-radius:8px;"
                "background:#2aa9f0;color:#fff;text-decoration:none;font-weight:600'>"
                "&#9993; Email developer to start a discussion</a>"
                "<p style='color:%s;font-size:12px'>Opens your email app with the AniList id filled in.</p></body>"
                % (fg, sub, aid, _esc("hayate-mail:%s" % aid, quote=True), sub))

    # ----- edit / delete (opens the comment on GitHub, same as the pop-out window) -----
    def _open_editor(self, cid):
        act = "Edit"
        if cid.startswith("del:"):
            act, cid = "Delete", cid[4:]
        elif cid.startswith("rep:"):
            act, cid = "Report content", cid[4:]
        disc = None
        if "-" in cid:
            disc, cid = cid.split("-", 1)
        if not disc and self._cur:
            n = discussion_for(self._cur[0])
            disc = str(n) if n else None
        if not disc or not cid.isdigit():
            return
        ev = QWebEngineView()
        ev.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        ev.setPage(QWebEnginePage(_profile(), ev))
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
        ev.load(QUrl("https://github.com/Piyush-1927/hayate-comments/discussions/%s#discussioncomment-%s"
                     % (disc, cid)))
        ev.show()
        ev.raise_()
        ev.activateWindow()
        self._edit_wins.append(ev)
        ev.destroyed.connect(lambda *_: self._edit_closed(ev))

    def _edit_closed(self, ev):
        try:
            self._edit_wins.remove(ev)
        except ValueError:
            pass
        if not self._closing:
            QTimer.singleShot(600, self._reload)

    def _reload(self):
        if self._closing:
            return
        self._cur = None
        self.load_now()

    def _teardown(self):
        self._closing = True
        try:
            from PyQt6 import sip
            pg = self._view.page()
            for o in list(self._edit_wins) + [self._view, pg]:
                if not sip.isdeleted(o):
                    sip.delete(o)
        except Exception:
            pass


_CLR_KEEP = []


def clear_comment_login():
    """Sign out of comments: cookies, giscus/GitHub localStorage and the saved login file."""
    import os
    from PyQt6.QtCore import QUrl
    prof = _profile()
    try:
        prof.cookieStore().deleteAllCookies()
    except Exception:
        pass
    try:
        prof.clearHttpCache()
    except Exception:
        pass
    try:
        os.remove(os.path.expanduser("~/.config/hayate/giscus_login.json"))
    except OSError:
        pass
    page = QWebEnginePage(prof)
    _CLR_KEEP.append(page)

    def _done(_ok):
        def _fin(*_):
            _refresh_live_views()
            try:
                _CLR_KEEP.remove(page)
            except ValueError:
                pass
            page.deleteLater()
        page.runJavaScript("try{localStorage.clear();sessionStorage.clear();}catch(e){}", _fin)

    page.loadFinished.connect(_done)
    page.load(QUrl(PAGE_URL))


def _refresh_live_views():
    """Drop the in-memory login from open comment views and reload them signed out."""
    from PyQt6.QtWebEngineWidgets import QWebEngineView
    from hayate.core.comments_web import ls_script
    prof = _profile()
    for v in QApplication.allWidgets():
        if not isinstance(v, QWebEngineView):
            continue
        try:
            pg = v.page()
            if pg is None or pg.profile() is not prof:
                continue
            sc = pg.scripts()
            for old in sc.find("hayate-login-sync"):
                sc.remove(old)
            ns = QWebEngineScript()
            ns.setName("hayate-login-sync")
            ns.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentCreation)
            ns.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
            ns.setRunsOnSubFrames(True)
            ns.setSourceCode(ls_script())
            sc.insert(ns)
            pg.runJavaScript("(function(){var f=document.querySelector('iframe.giscus-frame');"
                             "if(f&&f.contentWindow)f.contentWindow.postMessage({hayate:{clear:true}},'https://giscus.app');})();")
            pg.runJavaScript("try{localStorage.clear();sessionStorage.clear();}catch(e){}")
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(600, lambda v=v: v.reload())
        except Exception:
            import logging
            logging.exception("refresh comments view failed")
