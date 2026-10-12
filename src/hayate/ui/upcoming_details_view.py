from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QScrollArea, QFrame, QPushButton, QSizePolicy
)
from hayate.core.anilist import AnimeMedia
from hayate.ui.details_view import clean_html, RelationCard, get_net_mgr
from PyQt6.QtCore import QUrl
from PyQt6.QtGui import QPixmap
from PyQt6.QtNetwork import QNetworkRequest, QNetworkReply

class UpcomingDetailsView(QWidget):
    back_requested = pyqtSignal()
    relation_selected = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.anime = None
        self._init_ui()

    def _init_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("background: transparent;")

        container = QWidget()
        self.layout = QVBoxLayout(container)
        self.layout.setContentsMargins(40, 24, 40, 40)
        self.layout.setSpacing(20)

        # Back Navigation
        top_bar = QHBoxLayout()
        back_btn = QPushButton("← Back to Home")
        back_btn.setFixedSize(130, 34)
        back_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        back_btn.setStyleSheet("""
            QPushButton {
                background-color: #242436;
                color: #e2e8f0;
                border-radius: 8px;
                border: 1px solid #3d3d52;
                font-weight: 600;
            }
            QPushButton:hover { background-color: #2f2f48; }
        """)
        back_btn.clicked.connect(self.back_requested.emit)
        top_bar.addWidget(back_btn)
        top_bar.addStretch()
        self.layout.addLayout(top_bar)

        # Hero Card
        hero_card = QFrame()
        hero_card.setStyleSheet("""
            QFrame {
                background-color: #1e1e2d;
                border-radius: 16px;
                border: 1.5px solid #2f2f44;
            }
        """)
        hero_layout = QHBoxLayout(hero_card)
        hero_layout.setContentsMargins(28, 28, 28, 28)
        hero_layout.setSpacing(28)

        self.poster_lbl = QLabel()
        self.poster_lbl.setFixedSize(160, 230)
        self.poster_lbl.setScaledContents(True)
        self.poster_lbl.setStyleSheet("border-radius: 10px; background-color: #12121a; border: 1px solid #2f2f44;")
        hero_layout.addWidget(self.poster_lbl)

        info_layout = QVBoxLayout()
        info_layout.setSpacing(10)

        pill = QLabel("UPCOMING • NOT YET AIRED")
        pill.setStyleSheet("""
            background-color: rgba(245, 158, 11, 0.15);
            color: #f59e0b;
            font-size: 11px;
            font-weight: 800;
            padding: 4px 10px;
            border-radius: 6px;
            border: 1px solid rgba(245, 158, 11, 0.3);
        """)
        pill.setFixedWidth(175)
        info_layout.addWidget(pill)

        self.title_lbl = QLabel()
        self.title_lbl.setStyleSheet("font-size: 24px; font-weight: 900; color: #ffffff; border: none; background: transparent;")
        self.title_lbl.setWordWrap(True)
        info_layout.addWidget(self.title_lbl)

        self.meta_lbl = QLabel()
        self.meta_lbl.setStyleSheet("font-size: 13px; color: #94a3b8; font-weight: 600; border: none; background: transparent;")
        info_layout.addWidget(self.meta_lbl)

        self.desc_lbl = QLabel()
        self.desc_lbl.setStyleSheet("font-size: 13px; color: #cbd5e1; line-height: 1.5; border: none; background: transparent;")
        self.desc_lbl.setWordWrap(True)
        info_layout.addWidget(self.desc_lbl)
        info_layout.addStretch()

        hero_layout.addLayout(info_layout)
        self.layout.addWidget(hero_card)

        # Broadcasting Notice Card (Compact & Fixed Height)
        self.status_card = QFrame()
        self.status_card.setFixedHeight(120)
        self.status_card.setStyleSheet("""
            QFrame {
                background-color: #1e1e2d;
                border-radius: 16px;
                border: 1.5px solid #2f2f44;
            }
        """)
        status_layout = QVBoxLayout(self.status_card)
        status_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        status_layout.setSpacing(6)

        notice_title = QLabel("Broadcasting Schedule Pending")
        notice_title.setStyleSheet("font-size: 16px; font-weight: 800; color: #e2e8f0; border: none; background: transparent;")
        notice_sub = QLabel("Episodes and streaming channels will appear here once official release dates begin.")
        notice_sub.setStyleSheet("font-size: 13px; color: #64748b; border: none; background: transparent;")
        status_layout.addWidget(notice_title, alignment=Qt.AlignmentFlag.AlignCenter)
        status_layout.addWidget(notice_sub, alignment=Qt.AlignmentFlag.AlignCenter)
        self.layout.addWidget(self.status_card)

        # Connected Seasons & Relations Card (Framed to match DetailsView)
        self.rel_card = QFrame()
        self.rel_card.setStyleSheet("""
            QFrame {
                background-color: #1e1e2d;
                border-radius: 16px;
                border: 1.5px solid #2f2f44;
            }
        """)
        rel_card_layout = QVBoxLayout(self.rel_card)
        rel_card_layout.setContentsMargins(28, 20, 28, 28)
        rel_card_layout.setSpacing(16)

        rel_lbl = QLabel("Connected Seasons & Relations")
        rel_lbl.setStyleSheet("font-size: 19px; font-weight: 800; color: #ffffff; border: none; background: transparent;")
        rel_card_layout.addWidget(rel_lbl)

        rel_scroll = QScrollArea()
        rel_scroll.setWidgetResizable(True)
        rel_scroll.setFixedHeight(265)
        rel_scroll.setFrameShape(QFrame.Shape.NoFrame)
        rel_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        rel_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        rel_scroll.setStyleSheet("background: transparent; border: none;")

        rel_container = QWidget()
        rel_container.setStyleSheet("background: transparent;")
        self.rel_layout = QHBoxLayout(rel_container)
        self.rel_layout.setContentsMargins(0, 0, 0, 0)
        self.rel_layout.setSpacing(14)
        rel_scroll.setWidget(rel_container)
        rel_card_layout.addWidget(rel_scroll)

        self.layout.addWidget(self.rel_card)
        self.layout.addStretch()

        scroll.setWidget(container)
        root.addWidget(scroll)

    def set_anime(self, anime: AnimeMedia):
        self.anime = anime
        self.title_lbl.setText(anime.title)
        genres = " • ".join(anime.genres[:3]) if anime.genres else "Anime"
        eps_info = f"{anime.episodes} Total Episodes" if anime.episodes else "Episodes TBA"
        self.meta_lbl.setText(f"{anime.year or 'TBA'}  |  {eps_info}  |  {genres}")
        self.desc_lbl.setText(clean_html(anime.description or "No description available."))

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
            def _on_poster_ready(r=self._poster_reply):
                if r and r.error() == QNetworkReply.NetworkError.NoError:
                    data = r.readAll()
                    pix = QPixmap()
                    if pix.loadFromData(data):
                        self.poster_lbl.setPixmap(pix)
                if r:
                    r.deleteLater()
                if getattr(self, "_poster_reply", None) is r:
                    self._poster_reply = None
            self._poster_reply.finished.connect(_on_poster_ready)

        # Build relations
        while self.rel_layout.count():
            item = self.rel_layout.takeAt(0)
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
                self.rel_layout.addWidget(card)
            self.rel_layout.addStretch()
        else:
            self.rel_card.setVisible(False)
