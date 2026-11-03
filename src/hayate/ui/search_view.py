from hayate.core.assets import ic as _ic
import re
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QPoint, QTimer, QEvent
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QPushButton, QScrollArea, QFrame, QProgressBar
)
from hayate.core.anilist import search_anime_sync, AnimeMedia
from hayate.core.search_rank import merge_rank, title_score, tokens, norm, typo_variants
from hayate.core.workers import run_worker

class SearchTaskWorker(QThread):
    data_ready = pyqtSignal(list, bool)
    batch_ready = pyqtSignal(list, bool, bool)
    done = pyqtSignal()
    error_occurred = pyqtSignal(str)

    FIRST = 16
    EXPANSIONS = {
        "rezero": "Re:Zero",
        "re zero": "Re:Zero",
        "danmachi": "Is It Wrong to Try to Pick Up Girls in a Dungeon",
        "oregairu": "My Teen Romantic Comedy SNAFU",
        "kono suba": "KonoSuba",
        "konosuba": "KonoSuba",
        "boku no hero": "My Hero Academia",
        "mha": "My Hero Academia",
        "aot": "Attack on Titan",
        "snk": "Shingeki no Kyojin",
        "jjk": "Jujutsu Kaisen",
        "kimetsu": "Demon Slayer",
    }

    def __init__(self, query: str):
        super().__init__()
        self.query = query
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def _jobs(self, q, n):
        low = q.lower()
        jobs = []
        for k, v in self.EXPANSIONS.items():
            if k in low:
                e = re.sub(re.escape(k), v, q, flags=re.I).strip()
                if e.lower() != low:
                    jobs.append((e, 300, 6, False, 1, 16))
        base = re.sub(r"\b(?:season|s)\s*\d+\b", "", q, flags=re.I).strip()
        if base and base.lower() != low:
            jobs.append((base, 200, 4, False, 1, 12))
        toks = sorted(set(tokens(q)), key=len, reverse=True)[:3]
        if n < 15 and (len(toks) > 1 or (toks and toks[0] != norm(q))):
            jobs += [(t, 60, 2, False, 1, 12) for t in toks]
        if n < 4:
            jobs += [(v, 0, 0, True, 1, 12) for v in typo_variants(q)]
        return jobs

    def run(self):
        q = self.query.strip()
        from concurrent.futures import ThreadPoolExecutor
        pool = ThreadPoolExecutor(max_workers=1)
        full_f = pool.submit(lambda: search_anime_sync(q, per_page=35))
        try:
            res, adult = search_anime_sync(q, per_page=self.FIRST)
        except Exception as e:
            try:
                res, adult = full_f.result()
            except Exception:
                self.error_occurred.emit(str(e))
                return
        if self._cancel:
            return
        groups = [(res, 300, 6, False)]
        first = merge_rank(q, groups)
        shown = {m.id for m in first}
        self.batch_ready.emit(first, adult, False)
        n = len(res)
        try:
            fres, fadult = full_f.result()
            groups = [(fres, 300, 6, False)]
            adult = adult or fadult
            n = len(fres)
            extra = [m for m in merge_rank(q, groups) if m.id not in shown]
            if extra and not self._cancel:
                shown |= {m.id for m in extra}
                self.batch_ready.emit(extra, adult, False)
        except Exception:
            pass
        pool.shutdown(wait=False)
        try:
            weak = not first or max(title_score(m, q) for m in first) < 600
            jobs = self._jobs(q, n)
            if jobs and not self._cancel:
                from concurrent.futures import ThreadPoolExecutor

                def one(j):
                    try:
                        return search_anime_sync(j[0], page=j[4], per_page=j[5])
                    except Exception:
                        return None

                with ThreadPoolExecutor(max_workers=len(jobs)) as ex:
                    outs = list(ex.map(one, jobs))
                for j, o in zip(jobs, outs):
                    if o is None:
                        continue
                    groups.append((o[0], j[1], j[2], j[3]))
                    adult = adult or o[1]
                if not self._cancel:
                    full = merge_rank(q, groups)
                    if weak:
                        self.batch_ready.emit(full, adult, True)
                    else:
                        self.batch_ready.emit([m for m in full if m.id not in shown], adult, False)
        except Exception:
            pass
        self.done.emit()


class SearchView(QWidget):
    anime_selected = pyqtSignal(AnimeMedia)
    back_requested = pyqtSignal()
    open_settings_requested = pyqtSignal()
    card_hovered = pyqtSignal(AnimeMedia, QPoint, QWidget)
    scrolled = pyqtSignal()

    def __init__(self, poster_card_cls, parent=None):
        super().__init__(parent)
        self.poster_card_cls = poster_card_cls
        self.current_query = ""
        self.worker = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(40, 24, 8, 40)
        layout.setSpacing(18)

        # Header bar
        header = QHBoxLayout()
        back_btn = _ic(QPushButton("Back to Home"), "arrow-left")
        back_btn.setFixedHeight(36)
        back_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        back_btn.setStyleSheet("""
            QPushButton {
                background-color: #212130;
                color: #e2e8f0;
                border: 1.5px solid #3d3d52;
                border-radius: 8px;
                padding: 0 16px;
                font-weight: 600;
            }
            QPushButton:hover { background-color: #2d2d42; }
        """)
        back_btn.clicked.connect(self.back_requested.emit)
        header.addWidget(back_btn)

        self.title_lbl = QLabel("Search Results")
        self.title_lbl.setStyleSheet("font-size: 22px; font-weight: 800; color: #ffffff;")
        header.addWidget(self.title_lbl)
        header.addStretch()
        layout.addLayout(header)

        # Progress bar
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setFixedHeight(2)
        self.progress.setTextVisible(False)
        self.progress.setVisible(False)
        layout.addWidget(self.progress)

        # Scrollable Grid Area
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")

        self.grid_container = QWidget()
        self.grid_container.setStyleSheet("background: transparent;")
        self.grid_layout = QGridLayout(self.grid_container)
        self.grid_layout.setContentsMargins(0, 10, 32, 20)
        self.grid_layout.setSpacing(22)
        self.scroll.setWidget(self.grid_container)

        layout.addWidget(self.scroll)
        hbar = self.scroll.horizontalScrollBar()
        hbar.rangeChanged.connect(lambda *_: hbar.setValue(0))
        self.scroll.viewport().installEventFilter(self)

    def execute_search(self, query: str):
        self.current_query = query.strip()
        self.title_lbl.setText(f"Results for: {self.current_query}")
        self.progress.setVisible(True)
        self._clear_grid()

        self._search_token = getattr(self, "_search_token", 0) + 1
        token = self._search_token
        try:
            if self.worker is not None:
                self.worker.cancel()
        except Exception:
            pass
        self._items, self._shown_ids, self._adult_seen = [], set(), False
        worker = SearchTaskWorker(self.current_query)
        worker.batch_ready.connect(
            lambda items, adult, rep, t=token: t == self._search_token and self._on_batch(items, adult, rep))
        worker.done.connect(
            lambda t=token: t == self._search_token and self._on_done())
        worker.error_occurred.connect(
            lambda msg, t=token: t == self._search_token and self._render_error(msg))
        self.worker = worker
        run_worker(worker)

    def _on_batch(self, items, adult, replace):
        self._adult_seen = self._adult_seen or adult
        if replace or not self._shown_ids:
            if not items:
                return
            self._items = list(items)
            self._shown_ids = {m.id for m in items}
            self._render_results(self._items, adult)
            self.progress.setVisible(True)
        else:
            new = [m for m in items if m.id not in self._shown_ids]
            if not new:
                return
            self._shown_ids |= {m.id for m in new}
            self._items += new
            self._fill_gen += 1
            self._fill(self._items, self._fill_gen, len(self._cards))

    def _on_done(self):
        self.progress.setVisible(False)
        if not self._shown_ids:
            self._render_results([], self._adult_seen)

    def _clear_grid(self):
        self._cards = []
        self._fill_gen = getattr(self, "_fill_gen", 0) + 1
        while self.grid_layout.count():
            item = self.grid_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _render_results(self, items: list[AnimeMedia], had_adult_hits: bool):
        self.progress.setVisible(False)
        self._clear_grid()

        if not items:
            empty_box = QFrame()
            empty_box.setObjectName("clean_empty_box")
            empty_box.setStyleSheet("""
                QFrame#clean_empty_box {
                    background-color: #1e1e2d;
                    border: 1.5px solid #2f2f44;
                    border-radius: 16px;
                }
                QLabel {
                    background: transparent;
                    border: none;
                }
            """)
            empty_box.setFixedSize(540, 280)

            eb_layout = QVBoxLayout(empty_box)
            eb_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
            eb_layout.setContentsMargins(32, 24, 32, 28)
            eb_layout.setSpacing(10)

            if had_adult_hits:
                icon = _ic(QLabel(""), "shield")
                icon.setStyleSheet("font-size: 38px;")
                icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
                eb_layout.addWidget(icon)

                msg = QLabel("Filtered Due to 18+ Safety Settings")
                msg.setStyleSheet("font-size: 18px; font-weight: 800; color: #f87171;")
                msg.setAlignment(Qt.AlignmentFlag.AlignCenter)
                eb_layout.addWidget(msg)

                sub = QLabel(
                    f"Results matching \"{self.current_query}\" contain 18+ or NSFW media "
                    "which are hidden by default.\n\nYou can enable adult content under Settings if you are 18+."
                )
                sub.setStyleSheet("font-size: 12px; color: #94a3b8; line-height: 1.4;")
                sub.setWordWrap(True)
                sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
                eb_layout.addWidget(sub)

                eb_layout.addSpacing(6)
                settings_btn = QPushButton("Open Settings (18+)")
                settings_btn.setFixedHeight(34)
                settings_btn.setCursor(Qt.CursorShape.PointingHandCursor)
                settings_btn.setStyleSheet("""
                    QPushButton {
                        background-color: #212130;
                        color: #38bdf8;
                        border: 1.5px solid #38bdf8;
                        border-radius: 6px;
                        font-weight: 700;
                        font-size: 12px;
                        padding: 0 16px;
                    }
                    QPushButton:hover { background-color: #38bdf8; color: #090a0f; }
                """)
                settings_btn.clicked.connect(self.open_settings_requested.emit)
                eb_layout.addWidget(settings_btn, alignment=Qt.AlignmentFlag.AlignCenter)
            else:
                icon = _ic(QLabel(""), "search")
                icon.setStyleSheet("font-size: 38px;")
                icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
                eb_layout.addWidget(icon)

                msg = QLabel(f"No results found for \"{self.current_query}\"")
                msg.setStyleSheet("font-size: 18px; font-weight: 800; color: #ffffff;")
                msg.setAlignment(Qt.AlignmentFlag.AlignCenter)
                eb_layout.addWidget(msg)

                sub = QLabel("Check your spelling or try searching for the main franchise name.")
                sub.setStyleSheet("font-size: 13px; color: #94a3b8;")
                sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
                eb_layout.addWidget(sub)

            # Center container wrapper: fills the viewport and centers empty_box cleanly
            center_container = QWidget()
            center_container.setStyleSheet("background: transparent;")
            v_wrap = QVBoxLayout(center_container)
            v_wrap.setContentsMargins(0, 0, 0, 0)
            v_wrap.addStretch(1)

            h_wrap = QHBoxLayout()
            h_wrap.setContentsMargins(0, 0, 0, 0)
            h_wrap.addStretch(1)
            h_wrap.addWidget(empty_box)
            h_wrap.addStretch(1)

            v_wrap.addLayout(h_wrap)
            v_wrap.addStretch(1)

            self.grid_layout.addWidget(center_container, 0, 0, 1, 6)
            return

        self._cards = []
        self._fill_gen = getattr(self, "_fill_gen", 0) + 1
        self._cols = self._calc_cols()
        self._fill(items, self._fill_gen, 0)

    CARD_W, GAP, BATCH = 185, 22, 12

    RIGHT = 16

    def _usable(self):
        return max(self.CARD_W, self.scroll.viewport().width() - self.RIGHT)

    def _calc_cols(self):
        return max(1, (self._usable() + self.GAP) // (self.CARD_W + self.GAP))

    def _apply_gap(self):
        cols = getattr(self, "_cols", 1)
        gap = self.GAP
        if cols > 1:
            gap = max(self.GAP, min(34, (self._usable() - cols * self.CARD_W) // (cols - 1)))
        used = cols * self.CARD_W + (cols - 1) * gap
        extra = max(0, self.scroll.viewport().width() - used - 2)
        left = extra // 2
        self.grid_layout.setHorizontalSpacing(gap)
        self.grid_layout.setContentsMargins(left, 10, extra - left, 20)

    def _relayout(self):
        cards = getattr(self, "_cards", [])
        cols = self._calc_cols()
        if cards and cols != self._cols:
            self._cols = cols
            for i, c in enumerate(cards):
                self.grid_layout.addWidget(c, i // cols, i % cols)
            self._set_stretch()
        else:
            self._apply_gap()

    def _set_stretch(self):
        g = self.grid_layout
        for c in range(g.columnCount() + 1):
            g.setColumnStretch(c, 0)
        for r in range(g.rowCount() + 1):
            g.setRowStretch(r, 0)
        g.setColumnStretch(self._cols, 1)
        g.setRowStretch((len(self._cards) - 1) // self._cols + 1, 1)
        self._apply_gap()

    def _hook_scroll(self):
        if not getattr(self, "_scroll_hooked", False):
            self._scroll_hooked = True
            self.scroll.verticalScrollBar().valueChanged.connect(lambda _v: self.scrolled.emit())

    def _fill(self, items, gen, start):
        if gen != self._fill_gen:
            return
        self._hook_scroll()
        for anime in items[start:start + self.BATCH]:
            card = self.poster_card_cls(anime, priority=200)
            card.clicked.connect(self.anime_selected.emit)
            card.hovered.connect(self.card_hovered.emit)
            i = len(self._cards)
            self._cards.append(card)
            self.grid_layout.addWidget(card, i // self._cols, i % self._cols)
        if start + self.BATCH < len(items):
            QTimer.singleShot(0, lambda: self._fill(items, gen, start + self.BATCH))
        else:
            self._set_stretch()

    def eventFilter(self, obj, ev):
        if obj is self.scroll.viewport() and ev.type() == QEvent.Type.Resize:
            QTimer.singleShot(0, self._relayout)
        return super().eventFilter(obj, ev)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        QTimer.singleShot(0, self._relayout)

    def _render_error(self, err_msg: str):
        self.progress.setVisible(False)
        self._clear_grid()
        err_lbl = QLabel(f"Network error searching anime:\n{err_msg}")
        err_lbl.setStyleSheet("color: #f87171; font-size: 14px; font-weight: 700;")
        self.grid_layout.addWidget(err_lbl, 0, 0)
