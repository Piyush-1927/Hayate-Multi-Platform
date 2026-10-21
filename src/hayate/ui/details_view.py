import re
import time
from datetime import datetime
import certifi
import httpx
from hayate.core import net
from PyQt6.QtCore import Qt, pyqtSignal, QTimer, QUrl
from PyQt6.QtGui import QPixmap
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkRequest, QNetworkReply
from PyQt6.QtWidgets import (
    QComboBox,
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QPushButton, QLabel, QScrollArea, QFrame,
    QProgressBar, QMessageBox, QLineEdit
)
from hayate.core.anilist import AnimeMedia
from hayate.core.player import PlayerController
from hayate.core.history import history_mgr
from hayate.core.theme import theme

def clean_html(text: str) -> str:
    if not text:
        return "No description available."
    text = re.sub(r'<br\s*/?>', ' ', text)
    text = re.sub(r'<[^>]+>', '', text)
    return " ".join(text.split()).strip()

_NET_MGR = None

def get_net_mgr() -> QNetworkAccessManager:
    global _NET_MGR
    if _NET_MGR is None:
        _NET_MGR = QNetworkAccessManager()
    _NET_MGR.setProxy(net.qt_proxy())
    return _NET_MGR

class RelationCard(QFrame):
    clicked = pyqtSignal(dict)

    def __init__(self, rel: dict, parent=None):
        super().__init__(parent)
        self.rel = rel
        self._reply = None
        self.setFixedSize(140, 230)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet("""
            QFrame {
                background-color: #1e1e2d;
                border-radius: 10px;
                border: 1.5px solid #2f2f44;
            }
            QFrame:hover {
                border-color: #38bdf8;
                background-color: #27273a;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        self.img_lbl = QLabel()
        self.img_lbl.setFixedSize(124, 160)
        self.img_lbl.setStyleSheet("background-color: #242436; border-radius: 6px; border: none;")
        self.img_lbl.setScaledContents(True)
        layout.addWidget(self.img_lbl)

        self.type_lbl = QLabel(rel.get("relation_type", "RELATED"))
        self.type_lbl.setStyleSheet("color: #38bdf8; font-size: 10px; font-weight: 800; border: none; background: transparent;")
        layout.addWidget(self.type_lbl)

        self.title_lbl = QLabel(rel.get("title", "Unknown"))
        self.title_lbl.setStyleSheet("color: #ffffff; font-size: 11px; font-weight: 600; border: none; background: transparent;")
        self.title_lbl.setWordWrap(True)
        layout.addWidget(self.title_lbl)
        layout.addStretch()

        cover_url = rel.get("cover_url")
        if cover_url:
            req = QNetworkRequest(QUrl(cover_url))
            self._reply = get_net_mgr().get(req)
            self._reply.finished.connect(self._on_image_ready)

    def _on_image_ready(self):
        if not self._reply:
            return
        if self._reply.error() == QNetworkReply.NetworkError.NoError:
            data = self._reply.readAll()
            pix = QPixmap()
            if pix.loadFromData(data):
                self.img_lbl.setPixmap(pix)
        self._reply.deleteLater()
        self._reply = None

    def cleanup(self):
        if self._reply:
            try:
                self._reply.abort()
            except Exception:
                pass
            self._reply.deleteLater()
            self._reply = None

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.rel)

class DetailsView(QWidget):
    back_requested = pyqtSignal()
    relation_selected = pyqtSignal(int)
    play_requested = pyqtSignal(object, int)
    _play_started = pyqtSignal(object, int)
    _play_failed = pyqtSignal(object, int, str, str)

    def __init__(self, player: PlayerController, parent=None):
        super().__init__(parent)
        self.player = player
        self.anime: AnimeMedia | None = None
        self._play_started.connect(self._on_play_started)
        self._play_failed.connect(self._on_play_failed)
        self.active_ep: int = 1
        self.current_chunk: int = 0
        self.ep_buttons: dict[int, QPushButton] = {}
        self._watched: set[int] = set()
        theme.changed.connect(self._restyle_eps)
        self.chunk_buttons: list[QPushButton] = []

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll_area.setStyleSheet("QScrollArea { border: none; background: #171722; }")

        container = QWidget()
        container.setStyleSheet("background: #171722;")
        self.content_layout = QVBoxLayout(container)
        self.content_layout.setContentsMargins(40, 24, 40, 40)
        self.content_layout.setSpacing(28)
        self.scroll_area.setWidget(container)
        main_layout.addWidget(self.scroll_area)

        # Back Header
        top_bar = QHBoxLayout()
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
        top_bar.addWidget(back_btn)
        top_bar.addStretch()
        self.content_layout.addLayout(top_bar)

        # Banner Card
        self.hero_card = QFrame()
        self.hero_card.setFixedHeight(320)
        self.hero_card.setStyleSheet("""
            QFrame {
                background-color: #1e1e2d;
                border-radius: 16px;
                border: 1.5px solid #2f2f44;
            }
        """)
        h_layout = QHBoxLayout(self.hero_card)
        h_layout.setContentsMargins(28, 24, 28, 24)
        h_layout.setSpacing(24)

        self.poster_lbl = QLabel()
        self.poster_lbl.setFixedSize(180, 260)
        self.poster_lbl.setStyleSheet("background-color: #242436; border-radius: 10px; border: none;")
        self.poster_lbl.setScaledContents(True)
        h_layout.addWidget(self.poster_lbl)

        info_layout = QVBoxLayout()
        info_layout.setSpacing(10)

        self.title_lbl = QLabel()
        self.title_lbl.setStyleSheet("color: #ffffff; font-size: 26px; font-weight: 800; border: none; background: transparent;")
        self.title_lbl.setWordWrap(True)
        info_layout.addWidget(self.title_lbl)

        self.meta_lbl = QLabel()
        self.meta_lbl.setStyleSheet("color: #38bdf8; font-size: 13px; font-weight: 600; border: none; background: transparent;")
        info_layout.addWidget(self.meta_lbl)

        self.desc_lbl = QLabel()
        self.desc_lbl.setStyleSheet("color: #cbd5e1; font-size: 12px; line-height: 1.4; border: none; background: transparent;")
        self.desc_lbl.setWordWrap(True)
        info_layout.addWidget(self.desc_lbl)
        info_layout.addStretch()

        h_layout.addLayout(info_layout, stretch=1)
        self.content_layout.addWidget(self.hero_card)

        # Episodes Card
        self.ep_card = QFrame()
        self.ep_card.setStyleSheet("""
            QFrame {
                background-color: #1e1e2d;
                border-radius: 16px;
                border: 1.5px solid #2f2f44;
            }
        """)
        ep_card_layout = QVBoxLayout(self.ep_card)
        ep_card_layout.setContentsMargins(28, 20, 28, 24)
        ep_card_layout.setSpacing(14)

        # Header controls
        ep_header = QHBoxLayout()
        ep_header.setSpacing(12)
        self.ep_title_lbl = QLabel("Episodes")
        self.ep_title_lbl.setStyleSheet("font-size: 19px; font-weight: 800; color: #ffffff; border: none; background: transparent;")
        ep_header.addWidget(self.ep_title_lbl)
        ep_header.addStretch()

        # Jump to episode input
        self.jump_input = QLineEdit()
        self.jump_input.setPlaceholderText("Jump #...")
        self.jump_input.setFixedSize(85, 34)
        self.jump_input.setStyleSheet("""
            QLineEdit {
                background-color: #242436;
                color: #ffffff;
                border: 1px solid #3d3d52;
                border-radius: 6px;
                padding: 0 8px;
                font-size: 12px;
            }
            QLineEdit:focus { border: 1px solid #38bdf8; }
        """)
        self.jump_input.returnPressed.connect(self.on_jump_requested)
        ep_header.addWidget(self.jump_input)

        self.audio_combo = QComboBox()
        self.audio_combo.setFixedHeight(34)
        self.audio_combo.setCursor(Qt.CursorShape.PointingHandCursor)
        self.audio_combo.setStyleSheet("""
            QComboBox {
                background-color: #212130;
                color: #e2e8f0;
                border: 1px solid #3d3d52;
                border-radius: 8px;
                padding: 0 10px;
                font-weight: 700;
                font-size: 12px;
            }
            QComboBox:hover { border-color: #38bdf8; color: #ffffff; }
            QComboBox::drop-down { border: none; }
            QComboBox QAbstractItemView {
                background-color: #212130;
                color: #ffffff;
                selection-background-color: #38bdf8;
                selection-color: #090a0f;
                border: 1px solid #3d3d52;
                outline: none;
            }
        """)
        self.audio_combo.addItem("Sub (Original)", "sub")
        self.audio_combo.addItem("Eng Dub", "dub_eng")
        self.audio_combo.addItem("Hindi Dub", "dub_hin")
        self.audio_combo.addItem("Any Dub", "any_dub")

        # Sync with existing saved preference
        found_idx = self.audio_combo.findData(self._saved_audio())
        if found_idx >= 0:
            self.audio_combo.setCurrentIndex(found_idx)
        self.audio_combo.currentIndexChanged.connect(self._on_audio_pref_changed)
        ep_header.addWidget(self.audio_combo)

        self.play_main_btn = QPushButton("▶ Play EP 1")
        self.play_main_btn.setFixedHeight(34)
        self.play_main_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.play_main_btn.setStyleSheet("""
            QPushButton {
                background-color: #38bdf8;
                color: #090a0f;
                font-weight: 800;
                font-size: 13px;
                border-radius: 8px;
                padding: 0 16px;
                border: none;
            }
            QPushButton:hover { background-color: #7dd3fc; }
        """)
        self.play_main_btn.clicked.connect(lambda: self.play_episode(self.active_ep))
        ep_header.addWidget(self.play_main_btn)
        ep_card_layout.addLayout(ep_header)

        # Chunk Switcher Scroll Area (1 - 100, 101 - 200, ...)
        self.chunk_scroll = QScrollArea()
        self.chunk_scroll.setFixedHeight(44)
        self.chunk_scroll.setWidgetResizable(True)
        self.chunk_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.chunk_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.chunk_scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")

        self.chunk_widget = QWidget()
        self.chunk_widget.setStyleSheet("background: transparent;")
        self.chunk_layout = QHBoxLayout(self.chunk_widget)
        self.chunk_layout.setContentsMargins(0, 0, 0, 0)
        self.chunk_layout.setSpacing(8)
        self.chunk_scroll.setWidget(self.chunk_widget)
        ep_card_layout.addWidget(self.chunk_scroll)

        self.ep_progress = QProgressBar()
        self.ep_progress.setRange(0, 0)
        self.ep_progress.setFixedHeight(2)
        self.ep_progress.setTextVisible(False)
        self.ep_progress.setVisible(False)
        ep_card_layout.addWidget(self.ep_progress)

        self.grid_container = QWidget()
        self.grid_container.setStyleSheet("background: transparent; border: none;")
        self.grid_layout = QGridLayout(self.grid_container)
        self.grid_layout.setContentsMargins(0, 4, 0, 0)
        self.grid_layout.setSpacing(10)
        ep_card_layout.addWidget(self.grid_container)

        self.content_layout.addWidget(self.ep_card)

        # Relations Section
        self.rel_card = QFrame()
        self.rel_card.setStyleSheet("""
            QFrame {
                background-color: #1e1e2d;
                border-radius: 16px;
                border: 1.5px solid #2f2f44;
            }
        """)
        rel_layout = QVBoxLayout(self.rel_card)
        rel_layout.setContentsMargins(28, 20, 28, 24)
        rel_layout.setSpacing(16)

        rel_title = QLabel("Connected Seasons & Relations")
        rel_title.setStyleSheet("font-size: 19px; font-weight: 800; color: #ffffff; border: none; background: transparent;")
        rel_layout.addWidget(rel_title)

        rel_scroll = QScrollArea()
        rel_scroll.setFixedHeight(250)
        rel_scroll.setWidgetResizable(True)
        rel_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        rel_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        rel_scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")

        self.rel_widget = QWidget()
        self.rel_widget.setStyleSheet("background: transparent; border: none;")
        self.rel_inner_layout = QHBoxLayout(self.rel_widget)
        self.rel_inner_layout.setContentsMargins(0, 0, 0, 0)
        self.rel_inner_layout.setSpacing(16)
        rel_scroll.setWidget(self.rel_widget)
        rel_layout.addWidget(rel_scroll)

        self.content_layout.addWidget(self.rel_card)

    def set_anime(self, anime: AnimeMedia):
        self.anime = anime
        self.active_ep = 1
        self.current_chunk = 0
        self.ep_buttons.clear()
        self.jump_input.clear()

        self.title_lbl.setText(anime.title)
        genres = " • ".join(anime.genres[:3]) if anime.genres else "Anime"
        self.meta_lbl.setText(f"{anime.year or 'N/A'}  |  {anime.episodes} Total Episodes  |  {genres}")
        self.desc_lbl.setText(clean_html(anime.description))
        self.ep_title_lbl.setText(f"Episodes ({anime.episodes} total)")
        self.play_main_btn.setText("▶ Play EP 1")

        if hasattr(self, "_poster_reply") and self._poster_reply:
            try:
                self._poster_reply.abort()
            except Exception:
                pass
            self._poster_reply.deleteLater()
            self._poster_reply = None

        if anime.cover_url:
            req = QNetworkRequest(QUrl(anime.cover_url))
            self._poster_reply = get_net_mgr().get(req)
            def _on_poster_done(r=self._poster_reply):
                if r and r.error() == QNetworkReply.NetworkError.NoError:
                    data = r.readAll()
                    pix = QPixmap()
                    if pix.loadFromData(data):
                        self.poster_lbl.setPixmap(pix)
                if r:
                    r.deleteLater()
                if getattr(self, "_poster_reply", None) is r:
                    self._poster_reply = None
            self._poster_reply.finished.connect(_on_poster_done)

        total_ep = max(1, anime.episodes)
        self._build_chunk_bar(total_ep)
        start = history_mgr.get_resume_episode(anime.id, total_ep)
        self.active_ep = start
        self._load_chunk((start - 1) // 100)
        if start in self.ep_buttons and getattr(self, "scroll_area", None):
            QTimer.singleShot(0, lambda b=self.ep_buttons.get(start): b and self.scroll_area.ensureWidgetVisible(b))

        # Relations
        while self.rel_inner_layout.count():
            item = self.rel_inner_layout.takeAt(0)
            w = item.widget()
            if w:
                if hasattr(w, "cleanup"):
                    w.cleanup()
                w.deleteLater()

        if anime.relations:
            self.rel_card.setVisible(True)
            for rel in anime.relations:
                card = RelationCard(rel)
                card.clicked.connect(lambda r: self.relation_selected.emit(r["id"]))
                self.rel_inner_layout.addWidget(card)
            self.rel_inner_layout.addStretch()
        else:
            self.rel_card.setVisible(False)

    def _build_chunk_bar(self, total_ep: int):
        while self.chunk_layout.count():
            item = self.chunk_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        self.chunk_buttons.clear()
        if total_ep <= 100:
            self.chunk_scroll.setVisible(False)
            return

        self.chunk_scroll.setVisible(True)
        chunk_count = (total_ep - 1) // 100 + 1

        for c in range(chunk_count):
            start = c * 100 + 1
            end = min((c + 1) * 100, total_ep)
            btn = QPushButton(f"{start} - {end}")
            btn.setFixedHeight(30)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setStyleSheet("""
                QPushButton {
                    background-color: #242436;
                    color: #94a3b8;
                    border: 1px solid #383852;
                    border-radius: 6px;
                    padding: 0 12px;
                    font-weight: 700;
                    font-size: 11px;
                }
                QPushButton:hover { background-color: #2d2d44; color: #ffffff; }
            """)
            btn.clicked.connect(lambda _, chunk=c: self._load_chunk(chunk))
            self.chunk_layout.addWidget(btn)
            self.chunk_buttons.append(btn)

        self.chunk_layout.addStretch()

    def _load_chunk(self, chunk_index: int):
        self.current_chunk = chunk_index
        total_ep = max(1, self.anime.episodes if self.anime else 1)

        for i, c_btn in enumerate(self.chunk_buttons):
            if i == chunk_index:
                c_btn.setStyleSheet("""
                    QPushButton {
                        background-color: #38bdf8;
                        color: #090a0f;
                        border: none;
                        border-radius: 6px;
                        padding: 0 12px;
                        font-weight: 800;
                        font-size: 11px;
                    }
                """)
            else:
                c_btn.setStyleSheet("""
                    QPushButton {
                        background-color: #242436;
                        color: #94a3b8;
                        border: 1px solid #383852;
                        border-radius: 6px;
                        padding: 0 12px;
                        font-weight: 700;
                        font-size: 11px;
                    }
                    QPushButton:hover { background-color: #2d2d44; color: #ffffff; }
                """)

        # Clear existing episode buttons
        while self.grid_layout.count():
            item = self.grid_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.ep_buttons.clear()

        start_ep = chunk_index * 100 + 1
        end_ep = min((chunk_index + 1) * 100, total_ep)
        cols = 10

        for i in range(start_ep, end_ep + 1):
            btn = QPushButton(f"EP {i}")
            btn.setFixedSize(75, 36)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setStyleSheet("""
                QPushButton {
                    background-color: #242436;
                    color: #e2e8f0;
                    border: 1px solid #383852;
                    border-radius: 6px;
                    font-weight: 600;
                    font-size: 12px;
                }
                QPushButton:hover {
                    background-color: #2d2d44;
                    border-color: #38bdf8;
                }
            """)
            btn.clicked.connect(lambda _, ep=i: self.on_ep_btn_clicked(ep))
            offset = i - start_ep
            row = offset // cols
            col = offset % cols
            self.grid_layout.addWidget(btn, row, col)
            self.ep_buttons[i] = btn

        # Anchor layout: push remaining space down and right
        num_rows = (end_ep - start_ep) // cols + 1
        self.grid_layout.setRowStretch(num_rows, 1)
        self.grid_layout.setColumnStretch(cols, 1)

        if self.active_ep in self.ep_buttons:
            self._highlight_button(self.active_ep)
        else:
            self._restyle_eps()

    def on_jump_requested(self):
        text = self.jump_input.text().strip()
        if not text.isdigit() or not self.anime:
            return
        ep = int(text)
        if 1 <= ep <= self.anime.episodes:
            target_chunk = (ep - 1) // 100
            if target_chunk != self.current_chunk:
                self._load_chunk(target_chunk)
            self.on_ep_btn_clicked(ep)
        else:
            QMessageBox.information(
                self, "Invalid Episode",
                f"Please enter an episode number between 1 and {self.anime.episodes}."
            )

    def on_ep_btn_clicked(self, ep: int):
        # Select only. Playback starts from the Play button.
        self._highlight_button(ep)

    def _ep_qss(self, num: int) -> str:
        if num == self.active_ep:
            return """
                QPushButton {
                    background-color: #38bdf8;
                    color: #090a0f;
                    border: none;
                    border-radius: 6px;
                    font-weight: 800;
                    font-size: 12px;
                }
            """
        if num in self._watched:
            return f"""
                QPushButton {{
                    background-color: {theme.rgba('button_background', 0.45)};
                    color: {theme.rgba('text_secondary', 0.85)};
                    border: 1px solid {theme.rgba('ui_border', 0.5)};
                    border-radius: 6px;
                    font-weight: 600;
                    font-size: 12px;
                }}
                QPushButton:hover {{
                    background-color: {theme.rgba('hover')};
                    color: {theme.rgba('text_primary')};
                    border-color: {theme.rgba('accent')};
                }}
            """
        return """
            QPushButton {
                background-color: #242436;
                color: #e2e8f0;
                border: 1px solid #383852;
                border-radius: 6px;
                font-weight: 600;
                font-size: 12px;
            }
            QPushButton:hover {
                background-color: #2d2d44;
                border-color: #38bdf8;
            }
        """

    def _restyle_eps(self):
        anime = getattr(self, "anime", None)
        self._watched = history_mgr.get_watched_episodes(anime.id) if anime else set()
        for num, btn in self.ep_buttons.items():
            btn.setStyleSheet(self._ep_qss(num))

    def _highlight_button(self, ep: int):
        self.active_ep = ep
        self.play_main_btn.setText(f"▶ Play EP {ep}")
        self._restyle_eps()

    def auto_play_episode(self, ep_num: int):
        def _do_play():
            try:
                if self.scroll_area:
                    bar = self.scroll_area.verticalScrollBar()
                    if bar:
                        bar.setValue(bar.maximum())
                target_chunk = (ep_num - 1) // 100
                if target_chunk != self.current_chunk:
                    self._load_chunk(target_chunk)
                self._highlight_button(ep_num)
                self.play_episode(ep_num)
            except Exception as e:
                print(f"[DetailsView] auto_play error: {e}")

        QTimer.singleShot(150, _do_play)

    def _unreleased_message(self, ep: int):
        """Uses AniList data to spot episodes that have not aired yet (provider independent)."""
        a = self.anime
        if not a:
            return None
        if getattr(a, "status", None) == "NOT_YET_RELEASED":
            when = ""
            if a.start_date and a.start_date[0]:
                when = " It is scheduled for " + "-".join(str(p) for p in a.start_date if p) + "."
            return f"'{a.title}' hasn't started airing yet.{when}"
        if a.airing_episode and a.airing_time and ep >= a.airing_episode and a.airing_time > time.time():
            when = datetime.fromtimestamp(a.airing_time).strftime("%a %d %b, %H:%M")
            return f"EP {ep} hasn't aired yet. EP {a.airing_episode} is scheduled for {when}."
        return None

    def _saved_audio(self):
        from hayate.core.settings import load_settings
        st = load_settings()
        aid = str(self.anime.id) if getattr(self, "anime", None) else None
        return st.get("anime_audio_overrides", {}).get(aid) or st.get("preferred_audio", "sub")

    def showEvent(self, event):
        super().showEvent(event)
        i = self.audio_combo.findData(self._saved_audio())
        if i >= 0 and i != self.audio_combo.currentIndex():
            self.audio_combo.blockSignals(True)
            self.audio_combo.setCurrentIndex(i)
            self.audio_combo.blockSignals(False)

    def _on_audio_pref_changed(self, idx: int):
        if not self.anime:
            return
        val = self.audio_combo.itemData(idx)
        from hayate.core.settings import load_settings, save_settings
        st = load_settings()
        st.setdefault("anime_audio_overrides", {})[str(self.anime.id)] = val
        save_settings(st)

    def play_episode(self, ep: int):
        if not self.anime:
            return

        msg = self._unreleased_message(ep)
        if msg:
            QMessageBox.information(self, "Not Released Yet", msg)
            return

        self.play_requested.emit(self.anime, ep)

    def _on_play_started(self, anime, ep: int):
        self.ep_progress.setVisible(False)
        history_mgr.record_watch(
            anime_id=anime.id,
            title=anime.title,
            episode=ep,
            cover_url=anime.cover_url,
            banner_url=anime.banner_url,
            total_episodes=anime.episodes
        )

    def _on_play_failed(self, anime, ep: int, kind: str, message: str):
        self.ep_progress.setVisible(False)
        if kind == "no_stream":
            prov = self.player.active_provider()
            name = getattr(prov, "label", "") or prov.name
            if self.player.has_alternate_providers():
                hint = "Try switching to another provider in your settings."
            else:
                hint = "No other provider is installed yet."
            QMessageBox.information(
                self,
                "Not Available on This Provider",
                f"EP {ep} of '{anime.title}' isn't available on your current provider ({name}).\n\n"
                f"This season or episode may not be in its catalogue yet. {hint}"
            )
        else:
            QMessageBox.warning(self, "Playback Problem", message)
