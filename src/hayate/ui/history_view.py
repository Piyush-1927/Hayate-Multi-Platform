from datetime import date, datetime

from PyQt6.QtCore import QPoint, Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (QGridLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
                             QPushButton, QScrollArea, QVBoxLayout, QWidget)

from hayate.core.history import history_mgr
from hayate.core.theme import theme
from hayate.ui.main_window import AnimeMedia, PosterCard, _ANIME_META_CACHE

CARD_W, CARD_H, GAP, CAPTION_H = 185, 305, 20, 22


class HistoryView(QWidget):
    back_requested = pyqtSignal()
    card_clicked = pyqtSignal(AnimeMedia)
    card_hovered = pyqtSignal(AnimeMedia, QPoint, QWidget)
    scrolled = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._sections = []   # (grid, [item widgets])
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
        title = QLabel("Watch History")
        title.setStyleSheet("font-size: 22px; font-weight: 800; background: transparent;")
        head.addWidget(title)
        self.stats = QLabel("")
        head.addWidget(self.stats)
        head.addStretch()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search history…")
        self.search.setClearButtonEnabled(True)
        self.search.setFixedWidth(240)
        head.addWidget(self.search)
        self.clear_btn = QPushButton("Clear all")
        self.clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.clear_btn.clicked.connect(self._clear_all)
        head.addWidget(self.clear_btn)
        root.addLayout(head)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        self.body = QWidget()
        self.body.setObjectName("hv_body")
        self.body.setStyleSheet("QWidget#hv_body { background: transparent; }")
        self.body_l = QVBoxLayout(self.body)
        self.body_l.setContentsMargins(0, 0, 0, 0)
        self.body_l.setSpacing(18)
        self.scroll.setWidget(self.body)
        root.addWidget(self.scroll, 1)
        self.scroll.verticalScrollBar().valueChanged.connect(lambda _: self.scrolled.emit())

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(200)
        self._debounce.timeout.connect(self.refresh)
        self.search.textChanged.connect(lambda _: self._debounce.start())

        theme.changed.connect(self._restyle)
        self._restyle()

    # ---- styling ---------------------------------------------------------
    def _style_caption(self, cap):
        cap.setStyleSheet(f"color: {theme.rgba('text_secondary')}; font-size: 11px;"
                          " font-weight: 600; background: transparent;")

    def _restyle(self):
        self.stats.setStyleSheet(f"color: {theme.rgba('text_secondary')}; font-size: 12px;"
                                 " font-weight: 600; background: transparent;")
        for c in self._captions:
            self._style_caption(c)

    # ---- building --------------------------------------------------------
    def _make_item(self, h):
        watched = h.get("watched_episodes", [])
        last = int(h.get("last_episode") or 0)
        total = int(h.get("total_episodes") or 0)
        done = len(watched)
        progress = min(100.0, done / total * 100.0) if total > 0 else 0.0

        cached = _ANIME_META_CACHE.get(h["id"])
        media = AnimeMedia(
            id=h["id"], title=h.get("title", ""), cover_url=h.get("cover_url", ""),
            banner_url=h.get("banner_url", ""),
            description=cached.description if cached else "",
            episodes=max(total, last, 1))

        if total and done >= total:
            text = "Completed"
        else:
            text = f"Ep {last}" + (f" · {done}/{total} watched" if total else f" · {done} watched")

        w = QWidget()
        w.setFixedSize(CARD_W, CARD_H + CAPTION_H)
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        card = PosterCard(media, progress_percent=progress, priority=50, removable=True)
        card.clicked.connect(self.card_clicked.emit)
        card.hovered.connect(self.card_hovered.emit)
        card.remove_requested.connect(self._remove)
        lay.addWidget(card)
        cap = QLabel(text)
        cap.setFixedHeight(CAPTION_H)
        self._style_caption(cap)
        self._captions.append(cap)
        lay.addWidget(cap)
        return w

    def refresh(self):
        vbar = self.scroll.verticalScrollBar()
        keep = vbar.value()
        q = self.search.text().strip().lower()
        items = [h for h in history_mgr.get_all_history()
                 if "id" in h and (not q or q in str(h.get("title", "")).lower())]

        self._sections.clear()
        self._captions.clear()
        while self.body_l.count():
            it = self.body_l.takeAt(0)
            if it.widget():
                it.widget().deleteLater()

        eps = sum(len(h.get("watched_episodes", [])) for h in history_mgr.data.values())
        self.stats.setText(f"{len(history_mgr.data)} anime · {eps} episodes watched")
        self.clear_btn.setEnabled(bool(history_mgr.data))

        if not items:
            empty = QLabel("No matches." if q else "Nothing watched yet.")
            empty.setStyleSheet("font-size: 14px; background: transparent;")
            self.body_l.addWidget(empty)
            self.body_l.addStretch()
            return

        today = date.today()
        groups = {}
        for h in items:
            d = datetime.fromtimestamp(h.get("updated_at", 0) or 0).date()
            g = "Today" if d == today else ("This week" if 0 <= (today - d).days < 7 else "Earlier")
            groups.setdefault(g, []).append(h)

        for name in ("Today", "This week", "Earlier"):
            if name not in groups:
                continue
            sec = QWidget()
            sec.setStyleSheet("background: transparent;")
            sl = QVBoxLayout(sec)
            sl.setContentsMargins(0, 0, 0, 0)
            sl.setSpacing(10)
            hd = QLabel(f"{name}  ·  {len(groups[name])}")
            hd.setStyleSheet("font-size: 16px; font-weight: 800; background: transparent;")
            sl.addWidget(hd)
            gw = QWidget()
            grid = QGridLayout(gw)
            grid.setContentsMargins(0, 0, 0, 0)
            grid.setSpacing(GAP)
            sl.addWidget(gw)
            self._sections.append((grid, [self._make_item(h) for h in groups[name]]))
            self.body_l.addWidget(sec)
        self.body_l.addStretch()

        QTimer.singleShot(0, lambda: (self._reflow(True), vbar.setValue(keep)))

    # ---- layout ----------------------------------------------------------
    def _reflow(self, force=False):
        cols = max(1, (self.scroll.viewport().width() + GAP) // (CARD_W + GAP))
        if cols == self._cols and not force:
            return
        self._cols = cols
        align = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop
        for grid, widgets in self._sections:
            for w in widgets:
                grid.removeWidget(w)
            for c in range(grid.columnCount() + 1):
                grid.setColumnStretch(c, 0)
            for i, w in enumerate(widgets):
                grid.addWidget(w, i // cols, i % cols, align)
            grid.setColumnStretch(cols, 1)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        QTimer.singleShot(0, self._reflow)

    def showEvent(self, e):
        super().showEvent(e)
        QTimer.singleShot(0, self._reflow)

    # ---- actions ---------------------------------------------------------
    def _remove(self, anime):
        self.scrolled.emit()  # hides the hover preview
        if history_mgr.remove(anime.id):
            self.refresh()

    def _clear_all(self):
        if not history_mgr.data:
            return
        r = QMessageBox.question(
            self, "Clear watch history",
            f"Remove all {len(history_mgr.data)} anime from your watch history?\n\n"
            "A backup copy (history.json.bak) is kept.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if r == QMessageBox.StandardButton.Yes:
            history_mgr.clear()
            self.refresh()
