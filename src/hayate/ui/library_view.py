from PyQt6.QtCore import QPoint, Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (QGridLayout, QHBoxLayout, QLabel, QLineEdit,
                             QPushButton, QScrollArea, QVBoxLayout, QWidget)

from hayate import library_db as ldb
from hayate.core.theme import theme
from hayate.ui.main_window import AnimeMedia, PosterCard, _ANIME_META_CACHE

CARD_W, CARD_H, GAP, CAPTION_H = 185, 305, 20, 22
TABS = [("Watching", "watching"), ("Plan to Watch", "plan_to_watch"),
        ("Watch Later", "watch_later"), ("Completed", "completed"),
        ("On Hold", "on_hold"), ("Dropped", "dropped"), ("♥ Favorites", "fav")]
LABELS = {k: l for l, k in TABS}
TAB_QSS = ("QPushButton { background: transparent; border: none; padding: 6px 12px;"
           " font-weight: 600; border-bottom: 2px solid transparent; }"
           "QPushButton:checked { font-weight: 800; border-bottom: 2px solid palette(highlight); }")


class LibraryView(QWidget):
    back_requested = pyqtSignal()
    card_clicked = pyqtSignal(AnimeMedia)
    card_hovered = pyqtSignal(AnimeMedia, QPoint, QWidget)
    scrolled = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._tab = "plan_to_watch"
        self._items = []
        self._captions = []
        self._cols = 0

        root = QVBoxLayout(self)
        root.setContentsMargins(28, 20, 28, 20)
        root.setSpacing(14)

        head = QHBoxLayout()
        head.setSpacing(14)
        back = QPushButton("← Back")
        back.setCursor(Qt.CursorShape.PointingHandCursor)
        back.clicked.connect(self.back_requested.emit)
        head.addWidget(back)
        title = QLabel("Library")
        title.setStyleSheet("font-size: 22px; font-weight: 800; background: transparent;")
        head.addWidget(title)
        head.addStretch()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search library…")
        self.search.setClearButtonEnabled(True)
        self.search.setFixedWidth(240)
        head.addWidget(self.search)
        root.addLayout(head)

        tabs = QHBoxLayout()
        tabs.setSpacing(4)
        self._tab_btns = {}
        for label, key in TABS:
            b = QPushButton(label)
            b.setCheckable(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setStyleSheet(TAB_QSS)
            b.clicked.connect(lambda _, k=key: self._set_tab(k))
            tabs.addWidget(b)
            self._tab_btns[key] = b
        tabs.addStretch()
        root.addLayout(tabs)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        body = QWidget()
        body.setObjectName("lv_body")
        body.setStyleSheet("QWidget#lv_body { background: transparent; }")
        body_l = QVBoxLayout(body)
        body_l.setContentsMargins(0, 0, 0, 0)
        self.empty = QLabel("")
        self.empty.setStyleSheet("font-size: 14px; background: transparent;")
        body_l.addWidget(self.empty)
        gw = QWidget()
        gw.setStyleSheet("background: transparent;")
        self.grid = QGridLayout(gw)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(GAP)
        body_l.addWidget(gw)
        body_l.addStretch()
        self.scroll.setWidget(body)
        root.addWidget(self.scroll, 1)
        self.scroll.verticalScrollBar().valueChanged.connect(lambda _: self.scrolled.emit())

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(200)
        self._debounce.timeout.connect(self.refresh)
        self.search.textChanged.connect(lambda _: self._debounce.start())
        theme.changed.connect(self._restyle)

    def _restyle(self):
        for c in self._captions:
            c.setStyleSheet(f"color: {theme.rgba('text_secondary')}; font-size: 11px;"
                            " font-weight: 600; background: transparent;")

    def _set_tab(self, key):
        self._tab = key
        self.refresh()

    def _make_item(self, r):
        cached = _ANIME_META_CACHE.get(r["anilist_id"])
        media = AnimeMedia(
            id=r["anilist_id"], title=r["title"], cover_url=r["cover_url"] or "",
            banner_url="", description=cached.description if cached else "",
            episodes=max(r["total_eps"] or 0, 1))
        w = QWidget()
        w.setFixedSize(CARD_W, CARD_H + CAPTION_H)
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        tip = "Remove from Favorites" if self._tab == "fav" else "Remove from this list"
        card = PosterCard(media, priority=50, removable=True, remove_tip=tip)
        card.clicked.connect(self.card_clicked.emit)
        card.hovered.connect(self.card_hovered.emit)
        card.remove_requested.connect(self._remove)
        lay.addWidget(card)
        cap = QLabel((LABELS.get(r["status"], "") + ("  ♥" if r["favorite"] else "")).strip())
        cap.setFixedHeight(CAPTION_H)
        self._captions.append(cap)
        lay.addWidget(cap)
        return w

    def refresh(self):
        con = ldb.db()
        counts = ldb.counts(con)
        for label, key in TABS:
            b = self._tab_btns[key]
            b.setChecked(key == self._tab)
            b.setText(f"{label} ({counts.get(key, 0)})")
        for w in self._items:
            self.grid.removeWidget(w)
            w.deleteLater()
        self._items.clear()
        self._captions.clear()
        rows = ldb.search(con, self._tab, self.search.text().strip())
        self.empty.setText("" if rows else "Nothing here yet.")
        self.empty.setVisible(not rows)
        self._items = [self._make_item(r) for r in rows]
        self._restyle()
        QTimer.singleShot(0, lambda: self._reflow(True))

    def _reflow(self, force=False):
        cols = max(1, (self.scroll.viewport().width() + GAP) // (CARD_W + GAP))
        if cols == self._cols and not force:
            return
        self._cols = cols
        align = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop
        for w in self._items:
            self.grid.removeWidget(w)
        for c in range(self.grid.columnCount() + 1):
            self.grid.setColumnStretch(c, 0)
        for i, w in enumerate(self._items):
            self.grid.addWidget(w, i // cols, i % cols, align)
        self.grid.setColumnStretch(cols, 1)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        QTimer.singleShot(0, self._reflow)

    def showEvent(self, e):
        super().showEvent(e)
        QTimer.singleShot(0, self.refresh)

    def _remove(self, anime):
        self.scrolled.emit()
        con = ldb.db()
        r = ldb.get_entry(con, anime.id)
        if not r:
            return
        status, fav = r["status"], r["favorite"]
        if self._tab == "fav":
            fav = 0
        else:
            status = None
        if status is None and not fav:
            ldb.remove_entry(con, anime.id)
        else:
            ldb.put(con, anime.id, r["title"], r["cover_url"], r["total_eps"], status, fav)
        self.refresh()
