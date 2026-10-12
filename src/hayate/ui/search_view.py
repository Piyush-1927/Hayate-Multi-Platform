import re
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QPoint
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QPushButton, QScrollArea, QFrame, QProgressBar
)
from hayate.core.anilist import search_anime_sync, AnimeMedia
from hayate.core.workers import run_worker

class SearchTaskWorker(QThread):
    data_ready = pyqtSignal(list, bool)
    error_occurred = pyqtSignal(str)

    def __init__(self, query: str):
        super().__init__()
        self.query = query

    def run(self):
        try:
            queries = [self.query.strip()]
            q_lower = self.query.strip().lower()

            # Expand common unspaced / abbreviated anime franchise names
            expansions = {
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
            for k, v in expansions.items():
                if k in q_lower:
                    expanded = re.sub(re.escape(k), v, self.query, flags=re.I).strip()
                    if expanded not in queries:
                        queries.append(expanded)

            # Strip "season X" / "sX" to pull the entire franchise if specific season fails
            clean_base = re.sub(r'\b(?:season|s)\s*\d+\b', '', self.query, flags=re.I).strip()
            if clean_base and clean_base not in queries:
                queries.append(clean_base)

            seen_ids = set()
            combined_results = []
            had_adult_flag = False

            for q in queries:
                raw_res = search_anime_sync(q)
                if isinstance(raw_res, tuple) and len(raw_res) == 2:
                    res_list, had_adult = raw_res
                else:
                    res_list, had_adult = (raw_res if isinstance(raw_res, list) else []), False
                
                if had_adult:
                    had_adult_flag = True

                for item in res_list:
                    if item.id not in seen_ids:
                        seen_ids.add(item.id)
                        combined_results.append(item)

                if len(combined_results) >= 20:
                    break

            self.data_ready.emit(combined_results, had_adult_flag)
        except Exception as e:
            self.error_occurred.emit(str(e))

class SearchView(QWidget):
    anime_selected = pyqtSignal(AnimeMedia)
    back_requested = pyqtSignal()
    open_settings_requested = pyqtSignal()
    card_hovered = pyqtSignal(AnimeMedia, QPoint, QWidget)

    def __init__(self, poster_card_cls, parent=None):
        super().__init__(parent)
        self.poster_card_cls = poster_card_cls
        self.current_query = ""
        self.worker = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(40, 24, 40, 40)
        layout.setSpacing(18)

        # Header bar
        header = QHBoxLayout()
        back_btn = QPushButton("← Back to Home")
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
        self.grid_layout.setContentsMargins(0, 10, 0, 20)
        self.grid_layout.setSpacing(22)
        self.scroll.setWidget(self.grid_container)

        layout.addWidget(self.scroll)

    def execute_search(self, query: str):
        self.current_query = query.strip()
        self.title_lbl.setText(f"Results for: {self.current_query}")
        self.progress.setVisible(True)
        self._clear_grid()

        self._search_token = getattr(self, "_search_token", 0) + 1
        token = self._search_token
        worker = SearchTaskWorker(self.current_query)
        worker.data_ready.connect(
            lambda items, adult, t=token: t == self._search_token and self._render_results(items, adult))
        worker.error_occurred.connect(
            lambda msg, t=token: t == self._search_token and self._render_error(msg))
        self.worker = worker
        run_worker(worker)

    def _clear_grid(self):
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
                icon = QLabel("🛡️")
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
                icon = QLabel("🔍")
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

        cols = 6
        for idx, anime in enumerate(items):
            card = self.poster_card_cls(anime, priority=200)
            card.clicked.connect(self.anime_selected.emit)
            card.hovered.connect(self.card_hovered.emit)
            row = idx // cols
            col = idx % cols
            self.grid_layout.addWidget(card, row, col)

        num_rows = (len(items) - 1) // cols + 1
        self.grid_layout.setRowStretch(num_rows, 1)
        self.grid_layout.setColumnStretch(cols, 1)

    def _render_error(self, err_msg: str):
        self.progress.setVisible(False)
        self._clear_grid()
        err_lbl = QLabel(f"Network error searching anime:\n{err_msg}")
        err_lbl.setStyleSheet("color: #f87171; font-size: 14px; font-weight: 700;")
        self.grid_layout.addWidget(err_lbl, 0, 0)
