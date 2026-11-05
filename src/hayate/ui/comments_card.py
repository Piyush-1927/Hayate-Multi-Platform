import re, threading
from html import escape
from hayate.core.theme import theme
from PyQt6.QtCore import Qt, QPoint, QTimer, pyqtSignal
from PyQt6.QtGui import QGuiApplication, QPainter, QPen, QColor
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                             QPushButton, QFrame, QScrollArea, QTextBrowser)

MARGIN = 16   # gap kept to the video edges
SNAP = 28     # release within this many px of an edge -> snap to it
MINW, MINH = 240, 160


class _DragHeader(QWidget):
    """Only this bar drags the card, so the comment list can still scroll."""

    def __init__(self, card):
        super().__init__(card)
        self._card = card
        self.setFixedHeight(38)
        self.setCursor(Qt.CursorShape.SizeAllCursor)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 0, 8, 0)
        title = QLabel("Comments")
        self.title = title
        lay.addWidget(title)
        lay.addStretch(1)
        close = QPushButton("✕")
        close.setFixedSize(24, 24)
        close.setCursor(Qt.CursorShape.PointingHandCursor)
        close.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.close_btn = close
        close.clicked.connect(card.close_card)
        lay.addWidget(close)

    def restyle(self):
        self.title.setStyleSheet("color:%s; font-size:13px; font-weight:800; background:transparent; border:none;"
                                 % theme.c("text_primary").name())
        self.close_btn.setStyleSheet(
            "QPushButton { background: transparent; color: %s; border: none; font-size: 13px; }"
            "QPushButton:hover { color: %s; }" % (theme.c("text_secondary").name(), theme.c("danger").name()))

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._card._drag_begin(e.globalPosition().toPoint())
            e.accept()

    def mouseMoveEvent(self, e):
        if e.buttons() & Qt.MouseButton.LeftButton:
            self._card._drag_move(e.globalPosition().toPoint())
            e.accept()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._card._drag_end()
            e.accept()


class _Grip(QWidget):
    def __init__(self, card, side):
        super().__init__(card)
        self._card, self._side = card, side
        self.setFixedSize(18, 14)
        self.setAutoFillBackground(False)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setCursor(Qt.CursorShape.SizeFDiagCursor if side == "right"
                       else Qt.CursorShape.SizeBDiagCursor)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setPen(QPen(theme.c("ui_border"), 1.4))
        w, h = self.width(), self.height()
        for i in (4, 8):
            if self._side == "right":
                p.drawLine(w - 3, h - 3 - i, w - 3 - i, h - 3)
            else:
                p.drawLine(3, h - 3 - i, 3 + i, h - 3)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._card._resize_begin(e.globalPosition().toPoint(), self._side)
            e.accept()

    def mouseMoveEvent(self, e):
        if e.buttons() & Qt.MouseButton.LeftButton:
            self._card._resize_move(e.globalPosition().toPoint())
            e.accept()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._card._resize_end()
            e.accept()


class CommentsCard(QWidget):
    """Draggable comments card over the video.
    as_child=True  (GL embedding): plain child of the video widget.
    as_child=False (native wid):   frameless top-level, like the floating pill."""

    _loaded = pyqtSignal(int, object, object)

    def __init__(self, video, as_child):
        if as_child:
            super().__init__(video)
        else:
            super().__init__(video.window(), Qt.WindowType.ToolTip | Qt.WindowType.FramelessWindowHint
                             | Qt.WindowType.WindowDoesNotAcceptFocus)
            self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._video, self._child = video, as_child
        self._open, self._last, self._drag_origin, self._drag_xy = False, None, None, (0, 0)
        self._rs = None
        self._fx, self._fy = 1.0, 0.0   # 0..1 across the free space; default top-right
        self._wf, self._hf = 0.26, 0.45  # size as a fraction of the video area
        try:
            from hayate.core.settings import load_settings
            pos = load_settings().get("comments_card_pos")
            if isinstance(pos, (list, tuple)) and len(pos) == 2:
                self._fx = min(1.0, max(0.0, float(pos[0])))
                self._fy = min(1.0, max(0.0, float(pos[1])))
            sz = load_settings().get("comments_card_size")
            if isinstance(sz, (list, tuple)) and len(sz) == 2:
                self._wf = min(0.9, max(0.1, float(sz[0])))
                self._hf = min(0.9, max(0.1, float(sz[1])))
        except Exception:
            pass
        self._aid, self._loaded_id = None, None
        self._loaded.connect(self._on_loaded)
        self._build()
        self.restyle()
        theme.changed.connect(self.restyle)
        theme.changed.connect(self._web_theme)
        self._timer = QTimer(self)
        self._timer.setInterval(120)
        self._timer.timeout.connect(self.sync)
        self.hide()

    def _build(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self._frame = frame = QFrame(self)
        frame.setObjectName("cc_frame")
        outer.addWidget(frame)
        v = QVBoxLayout(frame)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        self._hdr = _DragHeader(self)
        v.addWidget(self._hdr)
        scroll = self._scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }"
                             " QScrollArea > QWidget > QWidget { background: transparent; }")
        body = QWidget()
        bl = QVBoxLayout(body)
        bl.setContentsMargins(14, 10, 14, 14)
        self.empty = QLabel("No comments yet.")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        bl.addWidget(self.empty)
        bl.addStretch(1)
        scroll.setWidget(body)
        v.addWidget(scroll, 1)
        self.view = QTextBrowser()
        self.view.setOpenExternalLinks(True)
        self.view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.view.setFrameShape(QFrame.Shape.NoFrame)
        self.view.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.view.document().setDocumentMargin(10)
        self.view.hide()
        v.addWidget(self.view, 1)
        self.web, self._web_n, self._v = None, None, v
        scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        foot = QWidget()
        foot.setObjectName("cc_foot")
        foot.setStyleSheet("QWidget#cc_foot { background: transparent; border: none; }")
        foot.setFixedHeight(14)
        fl = QHBoxLayout(foot)
        fl.setContentsMargins(0, 0, 0, 0)
        fl.addWidget(_Grip(self, "left"))
        fl.addStretch(1)
        fl.addWidget(_Grip(self, "right"))
        v.addWidget(foot)

    # ---- open / close ----
    def toggle(self):
        if self._open:
            self.close_card()
        else:
            self._open = True
            self._timer.start()
            self._load()
            self.sync()

    def close_card(self):
        if getattr(self, "_building", False):
            import traceback
            print("[comments] close_card during web build:")
            traceback.print_stack(limit=8)
            return
        self._open = False
        self._timer.stop()
        self.hide()
        w, self.web, self._web_n = self.web, None, None
        if w is not None:
            w.hide()
            w.deleteLater()

    def restyle(self, *_):
        self._frame.setStyleSheet(
            "QFrame#cc_frame { background: %s; border: 1px solid %s; border-radius: 12px; }"
            % (theme.rgba("card_background", 0.92), theme.rgba("card_border")))
        self._hdr.restyle()
        bg = theme.c("card_background").name()
        sb = ("QScrollBar:vertical { background: %s; width: 8px; margin: 4px 2px 4px 0; border: none; }"
              "QScrollBar::handle:vertical { background: %s; border-radius: 3px; min-height: 30px; }"
              "QScrollBar::handle:vertical:hover { background: %s; }"
              "QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; background: none; border: none; }"
              "QScrollBar::add-page, QScrollBar::sub-page { background: %s; }"
              % (bg, theme.c("ui_border").name(), theme.c("accent").name(), bg))
        self.view.setStyleSheet("QTextBrowser { background: transparent; border: none; }" + sb)
        self._scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }"
                                   " QScrollArea > QWidget > QWidget { background: transparent; }" + sb)
        self.empty.setStyleSheet("color:%s; font-size:13px; font-weight:600; background:transparent; border:none;"
                                 % theme.c("text_secondary").name())
        for g in self.findChildren(_Grip):
            g.update()
        if getattr(self, "_res", None):
            self._render()

    # ---- AniList comments ----
    def set_media(self, anilist_id):
        aid = int(anilist_id) if anilist_id else None
        if aid != self._aid:
            self._aid, self._loaded_id = aid, None
            self._show_msg("Loading…" if aid else "No comments yet.")
            if self._open:
                self._load()

    def _show_msg(self, text):
        self._res = None
        self.empty.setText(text)
        self.view.hide()
        self._scroll.show()

    def _gtheme(self):
        return "transparent_dark" if theme.c("card_background").lightness() < 128 else "light"

    def _ensure_web(self):
        if self.web is None:
            self._building = True
            from PyQt6.QtWidgets import QApplication
            from PyQt6.QtGui import QDesktopServices
            from PyQt6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile
            from PyQt6.QtWebEngineWidgets import QWebEngineView

            def _ext(u, pg):
                if u.scheme() in ("http", "https"):
                    QDesktopServices.openUrl(u)
                    pg.deleteLater()

            class _Page(QWebEnginePage):
                def createWindow(self, _t):
                    tmp = QWebEnginePage(self.profile(), self)
                    tmp.urlChanged.connect(lambda u: _ext(u, tmp))
                    return tmp

            prof = CommentsCard._prof = getattr(CommentsCard, "_prof", None) or QWebEngineProfile("hayate-comments", QApplication.instance())  # persistent: keeps GitHub login
            self.web = QWebEngineView(self)
            self.web.setPage(_Page(prof, self.web))
            self.web.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
            self._v.insertWidget(self._v.count() - 1, self.web, 1)
            self.web.hide()
            self._web_theme()
            self._building = False
        return self.web

    def _web_theme(self, *_):
        if self.web is None:
            return
        self.web.page().setBackgroundColor(theme.c("card_background"))
        self.web.page().runJavaScript(
            "var f=document.querySelector('iframe.giscus-frame');"
            "if(f)f.contentWindow.postMessage({giscus:{setConfig:{theme:'%s'}}},'https://giscus.app');"
            % self._gtheme())

    def _load(self):
        from PyQt6.QtCore import QUrl
        from hayate.core.comments_web import PAGE_URL, discussion_for
        n = discussion_for(self._aid)
        if not n:
            if self.web is not None:
                self.web.hide()
            self._show_msg("No discussion for this anime yet.\n(AniList id: %s)" % (self._aid or "unknown"))
            return
        try:
            w = self._ensure_web()
        finally:
            self._building = False
        if w is None:
            return
        self._scroll.hide()
        self.view.hide()
        w.show()
        if n != self._web_n:
            self._web_n = n
            w.load(QUrl("%s?n=%d&theme=%s" % (PAGE_URL, n, self._gtheme())))

    def _on_loaded(self, aid, res, err):
        if aid != self._aid:
            return
        if err:
            self._loaded_id = None  # retry next open
            self._show_msg("Couldn't load comments.")
            return
        if not res:
            self._show_msg("No AniList discussion yet.")
            return

        self._res = res
        self._render()

    def _render(self):
        res = self._res
        fg, dim = theme.c("text_primary").name(), theme.c("text_secondary").name()
        self.view.document().setDefaultStyleSheet(
            "body{color:%s;font-size:12px} a{color:%s}" % (fg, theme.c("accent").name()))

        def clean(h):
            h = re.sub(r"<span[^>]*spoiler[^>]*>.*?</span>", "<i>[spoiler]</i>", h, flags=re.S)
            return re.sub(r"<img[^>]*>", "", h)
        parts = [f'<p><a href="{res["url"]}">{escape(res["title"])} ↗</a></p>']
        for c in res["comments"]:
            parts.append(f'<p style="margin-left:{c["depth"] * 14}px">'
                         f'<b style="color:{fg}">{escape(c["user"])}</b> '
                         f'<span style="color:{dim}">♥ {c["likes"]}</span><br>{clean(c["html"])}</p>')
        if not res["comments"]:
            parts.append("<p>No comments yet.</p>")
        self.view.setHtml("".join(parts))
        self._scroll.hide()
        self.view.show()

    # ---- geometry ----
    def _metrics(self):
        vw, vh = self._video.width(), self._video.height()
        w = int(min(max(MINW, self._wf * vw), vw - 2 * MARGIN))
        h = int(min(max(MINH, self._hf * vh), vh - 2 * MARGIN))
        return vw, vh, w, h, max(MARGIN, vw - w - MARGIN), max(MARGIN, vh - h - MARGIN)

    def _xy(self):
        vw, vh, w, h, hx, hy = self._metrics()
        return (round(MARGIN + self._fx * (hx - MARGIN)),
                round(MARGIN + self._fy * (hy - MARGIN)))

    def _set_xy(self, x, y):
        vw, vh, w, h, hx, hy = self._metrics()
        self._fx = min(1.0, max(0.0, (x - MARGIN) / (hx - MARGIN))) if hx > MARGIN else 1.0
        self._fy = min(1.0, max(0.0, (y - MARGIN) / (hy - MARGIN))) if hy > MARGIN else 0.0

    def sync(self):
        v = self._video
        if not self._open or not v.isVisible():
            if self.isVisible():
                self.hide()
            return
        if (not self._child and QGuiApplication.applicationState()
                != Qt.ApplicationState.ApplicationActive):
            if self.isVisible():
                self.hide()
            return
        if v.width() < 100 or v.height() < 100:
            return
        _, _, w, h, _, _ = self._metrics()
        x, y = self._xy()
        if self._child:
            geo = (x, y, w, h)
        else:
            g = v.mapToGlobal(QPoint(x, y))
            geo = (g.x(), g.y(), w, h)
        if geo != self._last:
            self._last = geo
            self.setGeometry(*geo)
        if not self.isVisible():
            self.show()
            self.raise_()

    # ---- resizing / persistence ----
    def _save(self):
        try:
            from hayate.core.settings import load_settings, save_settings
            st = load_settings()
            st["comments_card_pos"] = [round(self._fx, 4), round(self._fy, 4)]
            st["comments_card_size"] = [round(self._wf, 4), round(self._hf, 4)]
            save_settings(st)
        except Exception:
            pass

    def _resize_begin(self, gpos, side):
        x, y = self._xy()
        _, _, w, h, _, _ = self._metrics()
        self._rs = (gpos, side, x, y, w, h)

    def _resize_move(self, gpos):
        if self._rs is None:
            return
        g0, side, x, y, w, h = self._rs
        d = gpos - g0
        vw, vh = self._video.width(), self._video.height()
        if side == "right":
            nw = min(max(MINW, w + d.x()), vw - MARGIN - x)
            nx = x
        else:
            right = x + w
            nw = min(max(MINW, w - d.x()), right - MARGIN)
            nx = right - nw
        nh = min(max(MINH, h + d.y()), vh - MARGIN - y)
        self._wf, self._hf = nw / vw, nh / vh
        self._set_xy(nx, y)
        self.sync()

    def _resize_end(self):
        self._rs = None
        self._save()
        self.sync()

    # ---- dragging ----
    def _drag_begin(self, gpos):
        self._drag_origin, self._drag_xy = gpos, self._xy()

    def _drag_move(self, gpos):
        if self._drag_origin is None:
            return
        d = gpos - self._drag_origin
        self._set_xy(self._drag_xy[0] + d.x(), self._drag_xy[1] + d.y())
        self.sync()

    def _drag_end(self):
        self._drag_origin = None
        _, _, _, _, hx, hy = self._metrics()
        x, y = self._xy()
        if x - MARGIN < SNAP:
            self._fx = 0.0
        elif hx - x < SNAP:
            self._fx = 1.0
        if y - MARGIN < SNAP:
            self._fy = 0.0
        elif hy - y < SNAP:
            self._fy = 1.0
        self._save()
        self.sync()
