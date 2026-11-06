from PyQt6.QtCore import Qt, QEvent
from PyQt6.QtWidgets import QFrame, QLabel, QScrollArea, QVBoxLayout, QWidget

from hayate.ui.player_view import PlayerView

try:
    from hayate.ui.details_view import RelationCard
except Exception:
    RelationCard = None


from PyQt6.QtCore import QUrl, pyqtSignal
from PyQt6.QtGui import QPixmap
from PyQt6.QtNetwork import QNetworkReply, QNetworkRequest
from PyQt6.QtWidgets import QHBoxLayout


# ---- theme-aware styling: old hardcoded hex -> central theme role (hot-reloads) ----
import re as _re, weakref as _weakref
from hayate.core.theme import theme as _theme

_HEX_ROLE = {
    "#1f1a17": ("button_background", 1.0),
    "#2c231e": ("hover", 1.0),
    "#120e0c": ("app_background", 1.0),
    "#171724": ("card_background", 1.0),
    "#26263a": ("card_border", 1.0),
    "#e2e8f0": ("text_primary", 1.0),
    "#ffffff": ("text_primary", 1.0),
    "#cbd5e1": ("text_primary", 0.85),
    "#94a3b8": ("text_secondary", 1.0),
    "#fb923c": ("accent", 1.0),
}
_SKINNED = _weakref.WeakKeyDictionary()


def _themed(tpl):
    def sub(m):
        r = _HEX_ROLE.get(m.group(0).lower())
        return _theme.rgba(*r) if r else m.group(0)
    return _re.sub(r"#[0-9a-fA-F]{6}", sub, tpl)


def _skin(w, tpl):
    _SKINNED[w] = tpl
    w.setStyleSheet(_themed(tpl))


def _retheme(*_):
    for w, tpl in list(_SKINNED.items()):
        try:
            w.setStyleSheet(_themed(tpl))
        except RuntimeError:
            pass


_theme.changed.connect(_retheme)


from PyQt6.QtCore import QObject as _QObj, QEvent as _QEv
from PyQt6.QtWidgets import QApplication as _QApp


class _WheelGuard(_QObj):
    """Wheel over the episode panel or Connected Series scrolls only that panel, never the page."""
    def __init__(self, view):
        super().__init__(view)
        self._v = view

    def eventFilter(self, obj, ev):
        if ev.type() == _QEv.Type.Wheel:
            w = _QApp.widgetAt(ev.globalPosition().toPoint())
            while w is not None:
                if w is self._v.sidebar or w is self._v.rel_panel:
                    ev.accept()
                    return True
                w = w.parentWidget()
        return False


class _RelRow(QFrame):
    """Related entry: thumbnail on the left, relation type + title on the right."""
    clicked = pyqtSignal(object)

    def __init__(self, rel, parent=None):
        super().__init__(parent)
        self.rel = rel
        self.setFixedHeight(92)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        _skin(self, "QFrame { background-color: #1f1a17; border: none; border-radius: 8px; }"
            "QFrame:hover { background-color: #2c231e; }")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 10, 0)
        lay.setSpacing(12)
        self.thumb = QLabel()
        self.thumb.setFixedSize(66, 92)
        self.thumb.setScaledContents(True)
        _skin(self.thumb, "background: #120e0c; border-radius: 8px;")
        lay.addWidget(self.thumb)
        col = QVBoxLayout()
        col.setSpacing(4)
        title = rel.get("title") or rel.get("name") or rel.get("title_romaji") or "Unknown"
        if isinstance(title, dict):
            title = title.get("english") or title.get("romaji") or "Unknown"
        t = QLabel(str(title))
        t.setWordWrap(True)
        _skin(t, "color: #e2e8f0; font-size: 13px; font-weight: 600; background: transparent;")
        kind = QLabel(str(rel.get("relation_type") or "").replace("_", " ").title())
        _skin(kind, "color: #fb923c; font-size: 11px; font-weight: 700; background: transparent;")
        col.addStretch()
        col.addWidget(t)
        col.addWidget(kind)
        col.addStretch()
        lay.addLayout(col, 1)
        url = rel.get("cover_url") or rel.get("cover") or rel.get("image") or rel.get("poster")
        if isinstance(url, dict):
            url = url.get("large") or url.get("medium")
        if url:
            try:
                from hayate.ui.details_view import get_net_mgr
                self._reply = get_net_mgr().get(QNetworkRequest(QUrl(str(url))))
                self._reply.finished.connect(self._img_done)
            except Exception:
                pass

    def _img_done(self):
        r = getattr(self, "_reply", None)
        try:
            if r is not None and r.error() == QNetworkReply.NetworkError.NoError:
                pix = QPixmap()
                if pix.loadFromData(r.readAll()):
                    self.thumb.setPixmap(pix)
            if r is not None:
                r.deleteLater()
        except RuntimeError:
            pass

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.rel)
        super().mousePressEvent(e)


from PyQt6.QtCore import QTimer as _LcTimer, Qt as _LcQt
from PyQt6.QtWidgets import QLabel as _LcLabel, QVBoxLayout as _LcVBox, QWidget as _LcWidget


class _LazyComments(_LcWidget):
    """Light stand-in for InlineComments. QtWebEngine is optional and slow to import
    (~2.5s), so the real widget is built after the page is shown, or earlier when
    the comments button is pressed (load_now)."""
    DELAY_MS = 1200
    PLACEHOLDER_H = 380  # floor kept for good: placeholder and real widget occupy the same space

    def __init__(self, scroll_getter, media_getter, parent=None):
        super().__init__(parent)
        self._args = (scroll_getter, media_getter)
        self._real = None
        self._failed = False
        self._scheduled = False
        self.setStyleSheet("background: transparent;")
        self.setMinimumHeight(self.PLACEHOLDER_H)
        self._lay = _LcVBox(self)
        self._lay.setContentsMargins(0, 0, 0, 0)
        self._ph = _LcWidget(self)
        pl = _LcVBox(self._ph)
        pl.setContentsMargins(0, 18, 0, 0)
        pl.setSpacing(8)
        title = _LcLabel("Comments")
        title.setStyleSheet("font-size: 18px; font-weight: 700; color: #ffffff; background: transparent;")
        self._note = _LcLabel("Loading comments...")
        self._note.setStyleSheet("font-size: 13px; color: #94a3b8; background: transparent;")
        pl.addWidget(title)
        pl.addWidget(self._note)
        pl.addStretch(1)
        self._lay.addWidget(self._ph)

    def showEvent(self, ev):
        super().showEvent(ev)
        if not self._scheduled and self._real is None and not self._failed:
            self._scheduled = True
            _LcTimer.singleShot(self.DELAY_MS, self._build)

    def _build(self):
        from PyQt6 import sip
        if sip.isdeleted(self) or self._real is not None or self._failed:
            return self._real
        try:
            from hayate.ui.comments_inline import InlineComments
            real = InlineComments(self._args[0], self._args[1], self)
        except Exception:
            logging.exception("inline comments unavailable")
            self._failed = True
            self._note.setText("Comments are unavailable (QtWebEngine could not be loaded).")
            self.setMinimumHeight(0)
            return None
        self._real = real
        self._lay.removeWidget(self._ph)
        self._ph.hide()
        self._ph.deleteLater()
        self._lay.addWidget(real, 0, _LcQt.AlignmentFlag.AlignTop)
        real.show()
        return real

    def load_now(self):
        real = self._build()
        if real is not None:
            real.load_now()


class MergedPlayerView(PlayerView):
    """Three columns: episode list | 16:9 video + controls | connected series."""

    def __init__(self, player_ctrl, parent=None):
        super().__init__(player_ctrl, parent)
        self._details = None
        _theme.changed.connect(self._retheme_info)

        # Left: room for 15 episodes
        self.ep_list.setFixedHeight(15 * 40)
        self.body_layout.setAlignment(self.sidebar, Qt.AlignmentFlag.AlignTop)
        self.sidebar.installEventFilter(self)

        # Middle: keep the video area 16:9
        self.card_viewport.installEventFilter(self)
        self._info_widget = self._build_info()
        self._wrap_page_scroll()
        self._wheel_guard = _WheelGuard(self)
        self.page_scroll.viewport().installEventFilter(self._wheel_guard)

        # Top-left button now goes home
        self.btn_back.setText("Back to Home")
        try:
            self.btn_back.clicked.disconnect()
        except Exception:
            pass
        self.btn_back.clicked.connect(self._go_home)

        # Right: connected series, stacked vertically
        self.rel_panel = QFrame()
        self.rel_panel.setFixedWidth(340)
        _skin(self.rel_panel, "QFrame { background-color: #171724; border: 1px solid #26263a; border-radius: 12px; }")
        pl = QVBoxLayout(self.rel_panel)
        pl.setContentsMargins(14, 14, 14, 14)
        pl.setSpacing(10)
        t = QLabel("Connected Series")
        _skin(t, "font-size: 16px; font-weight: 800; color: #ffffff; border: none; background: transparent;")
        pl.addWidget(t)
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        sc.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        self._rel_host = QWidget()
        self._rel_host.setStyleSheet("background: transparent; border: none;")
        self._rel_lay = QVBoxLayout(self._rel_host)
        self._rel_lay.setContentsMargins(0, 0, 0, 0)
        self._rel_lay.setSpacing(8)
        self._rel_lay.addStretch()
        sc.setWidget(self._rel_host)
        pl.addWidget(sc, 1)
        self.rel_col = QWidget()
        self.rel_col.setFixedWidth(340)
        _rc = QVBoxLayout(self.rel_col)
        _rc.setContentsMargins(0, 0, 0, 0)
        _rc.setSpacing(0)
        _rc.addWidget(self.rel_panel, 1000)
        _rc.addStretch(1)
        self._page_cols.addWidget(self.rel_col)

    # ----- 16:9 -----
    def eventFilter(self, obj, ev):
        if getattr(self, "_fs_on", False) or getattr(self, "_fs_anim", False):
            if obj is self.card_viewport and ev.type() == QEvent.Type.Resize:
                return False
            if obj is self.page_scroll.viewport() and ev.type() == QEvent.Type.Resize:
                from PyQt6.QtCore import QTimer
                QTimer.singleShot(0, self._fs_fit)
        if obj is self.card_viewport and ev.type() == QEvent.Type.Resize:
            w = self.card_viewport.width()
            h = round(w * 9 / 16)
            if w > 0 and self.card_viewport.height() != h:
                self.card_viewport.setFixedHeight(h)
        return super().eventFilter(obj, ev)

    # ----- wiring to the details data source -----
    def attach_details(self, dv):
        self._details = dv
        orig = dv.set_anime

        def _set_anime(anime, *a, **k):
            orig(anime, *a, **k)
            self.prepare_anime(anime)
            self.update_related(anime)

        dv.set_anime = _set_anime
        dv.hide()

    def _go_home(self):
        if self._details is not None:
            self._details.back_requested.emit()

    def _build_info(self):
        from PyQt6.QtWidgets import QGridLayout, QPushButton
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setFrameShape(QFrame.Shape.NoFrame)
        sc.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        host = QWidget()
        host.setStyleSheet("background: transparent;")
        row = QHBoxLayout(host)
        row.setContentsMargins(0, 8, 0, 8)
        row.setSpacing(20)

        self.info_poster = QLabel()
        self.info_poster.setFixedSize(180, 260)
        self.info_poster.setScaledContents(True)
        _skin(self.info_poster, "background: #120e0c; border-radius: 8px;")
        row.addWidget(self.info_poster, 0, Qt.AlignmentFlag.AlignTop)

        col = QVBoxLayout()
        col.setSpacing(8)
        self.info_title = QLabel()
        self.info_title.setWordWrap(True)
        _skin(self.info_title, "font-size: 26px; font-weight: 700; color: #ffffff; background: transparent;")
        col.addWidget(self.info_title)
        self.info_alt = QLabel()
        self.info_alt.setWordWrap(True)
        _skin(self.info_alt, "font-size: 12px; font-style: italic; color: #94a3b8; background: transparent;")
        col.addWidget(self.info_alt)
        self.info_desc = QLabel()
        self.info_desc.setWordWrap(True)
        _skin(self.info_desc, "font-size: 13px; color: #cbd5e1; background: transparent;")
        col.addWidget(self.info_desc)
        self.info_more = QPushButton("[more]")
        self.info_more.setFlat(True)
        self.info_more.setCursor(Qt.CursorShape.PointingHandCursor)
        _skin(self.info_more, "QPushButton { color: #94a3b8; border: none; background: transparent;"
                                     " text-align: left; font-size: 12px; padding: 0; }"
                                     "QPushButton:hover { color: #fb923c; }")
        self.info_more.clicked.connect(self._toggle_desc)
        col.addWidget(self.info_more, 0, Qt.AlignmentFlag.AlignLeft)

        grid = QGridLayout()
        grid.setHorizontalSpacing(40)
        self.info_left = QLabel()
        self.info_right = QLabel()
        for lb in (self.info_left, self.info_right):
            lb.setWordWrap(True)
            lb.setTextFormat(Qt.TextFormat.RichText)
            lb.setAlignment(Qt.AlignmentFlag.AlignTop)
            _skin(lb, "font-size: 13px; color: #cbd5e1; background: transparent;")
        grid.addWidget(self.info_left, 0, 0)
        grid.addWidget(self.info_right, 0, 1)
        col.addLayout(grid)
        col.addStretch()
        row.addLayout(col, 1)
        self._info_full = ""
        self._info_open = False
        self._info_reply = None
        return host

    def _wrap_page_scroll(self):
        from PyQt6.QtWidgets import QScrollArea, QFrame
        rl = self.root_layout
        body = self.body_layout
        rl.removeItem(body)
        body.setParent(None)
        host = QWidget()
        hl = QVBoxLayout(host)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(0)
        cols = QHBoxLayout()
        cols.setContentsMargins(0, 0, 0, 0)
        cols.setSpacing(max(body.spacing(), 12))
        left = QVBoxLayout()
        left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(12)
        left.addLayout(body)
        left.addWidget(self._info_widget)
        cols.addLayout(left, 1)
        hl.addLayout(cols)
        self._cmt_inline = self._make_inline_comments()
        if self._cmt_inline is not None:
            hl.addWidget(self._cmt_inline)
        hl.addStretch(1)
        self._page_cols = cols
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setFrameShape(QFrame.Shape.NoFrame)
        sc.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        sc.setWidget(host)
        host.setAutoFillBackground(False)
        sc.viewport().setAutoFillBackground(False)
        rl.addWidget(sc, 1)
        self.page_scroll = sc

    def _make_inline_comments(self):
        # GL video is a normal Qt widget, so QtWebEngine can live on this page in-process.
        from hayate.core.playback_platform import detect_playback_platform
        if detect_playback_platform().embedding != "gl":
            return None
        try:
            import importlib.util
            if importlib.util.find_spec("PyQt6.QtWebEngineWidgets") is None:
                return None
            return _LazyComments(
                lambda: getattr(self, "page_scroll", None),
                lambda: (getattr(getattr(self, "anime", None), "id", None), getattr(self, "active_ep", None)),
                self)
        except Exception:
            logging.exception("inline comments unavailable")
            return None

    def _toggle_comments(self):
        w = getattr(self, "_cmt_inline", None)
        if w is None:
            return super()._toggle_comments()  # x11 path: the pop-out comments window
        w.load_now()
        sc = self.page_scroll
        sc.verticalScrollBar().setValue(w.mapTo(sc.widget(), w.rect().topLeft()).y())

    def _render_desc(self):
        full = self._info_full
        short = full if len(full) <= 320 else full[:320].rstrip() + "..."
        self.info_desc.setText(full if self._info_open else short)
        self.info_more.setVisible(len(full) > 320)
        self.info_more.setText("[less]" if self._info_open else "[more]")

    def _toggle_desc(self):
        self._info_open = not self._info_open
        self._render_desc()

    def _retheme_info(self):
        a = getattr(self, "_info_anime", None)
        if a is not None:
            self.update_info(a)

    def update_info(self, anime):
        self._info_anime = anime
        import html as _h
        try:
            self.info_title.setText(anime.title)
            alts = []
            for k in ("title_english", "title_native", "title_romaji"):
                v = getattr(anime, k, None)
                if v and v != anime.title and v not in alts:
                    alts.append(v)
            for v in (getattr(anime, "synonyms", None) or []):
                if v and v != anime.title and v not in alts:
                    alts.append(v)
            self.info_alt.setText("; ".join(alts[:5]))
            self.info_alt.setVisible(bool(alts))

            try:
                from hayate.ui.details_view import clean_html
                d = clean_html(anime.description)
            except Exception:
                d = anime.description or ""
            self._info_full = d or "No description available."
            self._info_open = False
            self._render_desc()

            def first(*names):
                for n in names:
                    v = getattr(anime, n, None)
                    if v not in (None, "", [], 0):
                        return v
                return None

            def names(v):
                out = []
                for x in (v or []):
                    out.append(x.get("name") if isinstance(x, dict) else str(x))
                return ", ".join(o for o in out if o)

            dur = first("duration", "episode_duration")
            if isinstance(dur, int):
                dur = f"{dur} min"
            year = first("year", "season_year")
            season = first("season")
            prem = f"{season} {year}".title() if season and year else year
            left = [("Type", first("format", "type", "media_type")),
                    ("Premiered", prem),
                    ("Status", first("status")),
                    ("Genres", ", ".join(anime.genres) if anime.genres else None)]
            right = [("Score", first("average_score", "score", "mean_score")),
                     ("Duration", dur),
                     ("Episodes", anime.episodes or "?"),
                     ("Studios", names(first("studios")))]

            def fmt(rows):
                return "<br>".join(
                    '<span style="color:%s">%s:</span> %s' % (_theme.rgba("text_secondary"), k, _h.escape(str(v).replace("_", " ")))
                    for k, v in rows if v not in (None, ""))
            self.info_left.setText(fmt(left))
            self.info_right.setText(fmt(right))

            self.info_poster.clear()
            old = self._info_reply
            self._info_reply = None
            if old is not None:
                try:
                    old.finished.disconnect()
                    old.abort()
                    old.deleteLater()
                except Exception:
                    pass
            if anime.cover_url:
                from hayate.ui.details_view import get_net_mgr
                r = get_net_mgr().get(QNetworkRequest(QUrl(anime.cover_url)))
                self._info_reply = r

                def _done(r=r):
                    try:
                        if r.error() == QNetworkReply.NetworkError.NoError:
                            pix = QPixmap()
                            if pix.loadFromData(r.readAll()):
                                self.info_poster.setPixmap(pix)
                        r.deleteLater()
                        if self._info_reply is r:
                            self._info_reply = None
                    except RuntimeError:
                        pass
                r.finished.connect(_done)
        except Exception:
            import logging
            logging.exception("MergedPlayerView.update_info failed")

    def update_related(self, anime):
        self.update_info(anime)
        while self._rel_lay.count() > 1:
            item = self._rel_lay.takeAt(0)
            w = item.widget()
            if w is not None:
                if hasattr(w, "cleanup"):
                    w.cleanup()
                w.deleteLater()
        if RelationCard is None:
            return
        rels = [r for r in (anime.relations or []) if r.get("relation_type") != "CHARACTER"]
        for rel in rels:
            card = _RelRow(rel)
            card.clicked.connect(lambda r: self._details.relation_selected.emit(r["id"]))
            self._rel_lay.insertWidget(self._rel_lay.count() - 1, card)
        self._fit_rel_panel(len(rels))

    def _fit_rel_panel(self, n):
        # hug the content; the column layout still caps it at the row height
        rows = n * 92 + max(0, n - 1) * 8
        head = 14 * 2 + 24 + (10 if n else 0) + 2
        self.rel_panel.setMaximumHeight(head + rows + 4)

    # ----- fill the list without starting playback -----
    def prepare_anime(self, anime, ep=None):
        try:
            same = getattr(self, "anime", None) is not None and self.anime.id == anime.id
            if not same and not self._playback_shutdown:
                self._shutdown_playback()
            if same:
                return
            if not ep:
                from hayate.core.history import history_mgr
                try:
                    ep = history_mgr.get_resume_episode(anime.id, max(1, anime.episodes or 1))
                except Exception:
                    ep = 1
            self.anime = anime
            self.active_ep = ep
            self.current_chunk = (ep - 1) // 100
            self.title_lbl.setText(anime.title)
            self.side_title.setText(f"Episodes ({anime.episodes})")
            from hayate.core.settings import load_settings
            st = load_settings()
            self._reload_audio_items()
            pref = st.get("anime_audio_overrides", {}).get(str(anime.id)) or st.get("preferred_audio", "sub")
            i = self.audio_combo.findData(pref)
            if i >= 0:
                self.audio_combo.setCurrentIndex(i)
            self._build_chunks(max(1, anime.episodes))
            self._load_chunk(self.current_chunk)
        except Exception:
            import logging
            logging.exception("MergedPlayerView.prepare_anime failed")

    def play_episode(self, ep):
        if self._playback_shutdown:
            self._playback_shutdown = False
            self._playback_error = None
            self._initialize_playback()
            if self.media_player is not None:
                self._connect_player_signals()
            else:
                self._playback_shutdown = True
        super().play_episode(ep)

    def hideEvent(self, event):
        try:
            if not event.spontaneous() and not self._playback_shutdown:
                self._shutdown_playback()
        except Exception:
            pass
        super().hideEvent(event)

    def _toggle_fullscreen(self):
        from PyQt6 import sip
        from PyQt6.QtCore import QTimer, QVariantAnimation, QEasingCurve
        from PyQt6.QtWidgets import QLabel, QGraphicsOpacityEffect
        if sip.isdeleted(self):
            return
        enter = not self.window().isFullScreen()
        rel = getattr(self, "rel_panel", None)
        col = getattr(self, "rel_col", None)
        others = [x for x in (getattr(self, "_info_widget", None), getattr(self, "_cmt_inline", None)) if x is not None]
        if getattr(self, "_fs_a", None) is not None:
            self._fs_a.stop()
        old = getattr(self, "_fs_ov", None)
        if old is not None and not sip.isdeleted(old):
            old.deleteLater()
        ov = QLabel(self)
        ov.setPixmap(self.grab())
        ov.setScaledContents(False)
        ov.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        ov.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        ov.setGeometry(self.rect())
        eff = QGraphicsOpacityEffect(ov)
        ov.setGraphicsEffect(eff)
        ov.show()
        ov.raise_()
        self._fs_ov = ov
        self._fs_anim = True
        if enter:
            self._fs_hidden = {x: x.isHidden() for x in others}
            self._fs_rel_hidden = rel is None or rel.isHidden()
            self._fs_col_hidden = col is None or col.isHidden()
            for x in others:
                x.hide()
            if rel is not None:
                rel.hide()
            if col is not None:
                col.hide()
            self.page_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self.page_scroll.verticalScrollBar().setValue(0)
            self.page_scroll.viewport().installEventFilter(self)
            self._fs_on = True
        else:
            self._fs_on = False
            for x in others:
                x.setVisible(not getattr(self, "_fs_hidden", {}).get(x, False))
            self.page_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
            if col is not None and not getattr(self, "_fs_col_hidden", True):
                col.show()
            if rel is not None and not getattr(self, "_fs_rel_hidden", True):
                rel.show()
        super()._toggle_fullscreen()

        def fade():
            if sip.isdeleted(self) or sip.isdeleted(ov):
                return
            self._fs_anim = False
            self._fs_fit()
            ov.setGeometry(self.rect())
            ov.raise_()

            def step(v):
                if sip.isdeleted(ov):
                    return
                eff.setOpacity(max(0.0, 1.0 - v))

            def fin():
                if not sip.isdeleted(ov):
                    ov.deleteLater()

            an = QVariantAnimation(self)
            an.setDuration(260)
            an.setStartValue(0.0)
            an.setEndValue(1.0)
            an.setEasingCurve(QEasingCurve.Type.InOutQuad)
            an.valueChanged.connect(step)
            an.finished.connect(fin)
            self._fs_a = an
            an.start()

        QTimer.singleShot(120, fade)

    def _fs_fit(self):
        from PyQt6 import sip
        if sip.isdeleted(self) or getattr(self, "_fs_anim", False):
            return
        cv = self.card_viewport
        if getattr(self, "_fs_on", False):
            h = self.page_scroll.viewport().height()
            if h > 0:
                cv.setFixedHeight(h)
        elif cv.width() > 0:
            cv.setFixedHeight(round(cv.width() * 9 / 16))

    # kept so main_window calls still work
    def show_player(self, visible=True):
        pass
