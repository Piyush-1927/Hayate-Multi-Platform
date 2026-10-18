import re
import certifi
import httpx
from PyQt6.QtCore import (
    Qt, QThread, pyqtSignal, QRectF, QTimer, QPoint, QPointF,
    QPropertyAnimation, QEasingCurve, pyqtProperty
)
from PyQt6.QtGui import (
    QPixmap, QImage, QPainter, QBrush,
    QLinearGradient, QColor, QPainterPath, QPen,
    QFontMetrics, QTextLayout, QWheelEvent, QCloseEvent,
    QMouseEvent, QCursor
)
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLineEdit, QPushButton, QLabel, QScrollArea,
    QProgressBar, QMessageBox, QFrame, QStackedWidget, QSizePolicy,
    QApplication
)
from hayate.core.anilist import (
    get_home_shelves_sync, search_anime_sync, get_anime_by_id_sync, AnimeMedia
)
from hayate.core.player import PlayerController
from hayate.core.workers import run_worker, shutdown_workers
from hayate.core.history import history_mgr
from hayate.core.image_cache import image_mgr
from hayate.ui.details_view import DetailsView
from hayate.ui.player_view import PlayerView
from hayate.ui.upcoming_details_view import UpcomingDetailsView
from hayate.ui.schedule_view import ScheduleView
from hayate.ui.settings_view import SettingsView
from hayate.ui.search_view import SearchView

def clean_html(text: str) -> str:
    if not text:
        return "No description available."
    text = re.sub(r'<br\s*/?>', ' ', text)
    text = re.sub(r'<[^>]+>', '', text)
    return " ".join(text.split()).strip()

_SPOTLIGHT_PIX_CACHE: dict[int, QPixmap] = {}
_ANIME_META_CACHE: dict[int, AnimeMedia] = {}


class SpotlightPreviewWorker(QThread):
    def __init__(self, items: list[AnimeMedia]):
        super().__init__()
        self.items = items

    def run(self):
        for anime in self.items[1:]:
            url = getattr(anime, "banner_url", None) or getattr(anime, "cover_url", None)
            if not url or anime.id in _SPOTLIGHT_PIX_CACHE:
                continue
            image_mgr.load(url, lambda pix, aid=anime.id: self._save(aid, pix), priority=50)
            self.msleep(120)

    def _save(self, aid: int, pix: QPixmap):
        _SPOTLIGHT_PIX_CACHE[aid] = pix


class HomeShelvesWorker(QThread):
    data_ready = pyqtSignal(dict)
    error_occurred = pyqtSignal(str)

    def run(self):
        try:
            shelves = get_home_shelves_sync()
            self.data_ready.emit(shelves)
        except Exception as e:
            self.error_occurred.emit(str(e))


class SearchWorker(QThread):
    data_ready = pyqtSignal(list)
    error_occurred = pyqtSignal(str)

    def __init__(self, query: str):
        super().__init__()
        self.query = query

    def run(self):
        try:
            results = search_anime_sync(self.query)
            self.data_ready.emit(results)
        except Exception as e:
            self.error_occurred.emit(str(e))


class SingleAnimeWorker(QThread):
    data_ready = pyqtSignal(object)

    def __init__(self, anime_id: int):
        super().__init__()
        self.anime_id = anime_id

    def run(self):
        try:
            media = get_anime_by_id_sync(self.anime_id)
            self.data_ready.emit(media)
        except Exception:
            self.data_ready.emit(None)


class HamburgerButton(QPushButton):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(38, 38)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet("""
            QPushButton {
                background-color: #212130;
                border: 1.5px solid #3d3d52;
                border-radius: 8px;
            }
            QPushButton:hover { background-color: #2d2d42; border-color: #4f4f6b; }
        """)

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor("#f1f5f9"), 2.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        cx = self.width() // 2
        cy = self.height() // 2
        bar_len = 7
        painter.drawLine(cx - bar_len, cy - 5, cx + bar_len, cy - 5)
        painter.drawLine(cx - bar_len, cy,     cx + bar_len, cy)
        painter.drawLine(cx - bar_len, cy + 5, cx + bar_len, cy + 5)


class ElidedParagraphLabel(QWidget):
    def __init__(self, text: str = "", max_lines: int = 5, parent=None):
        super().__init__(parent)
        self._text = text
        self._max_lines = max_lines
        self.setStyleSheet("background: transparent; border: none;")

    def setText(self, text: str):
        self._text = text
        self.update()

    def paintEvent(self, event):
        if not self._text:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QColor("#cbd5e1"))
        font = self.font()
        font.setPointSize(10)
        font.setWeight(400)
        painter.setFont(font)

        metrics = QFontMetrics(font)
        line_height = int(metrics.lineSpacing() * 1.35)
        width = self.width()

        # How many lines really fit in the widget's height (so the last line is never cut off)
        fit_lines = max(1, (self.height() - metrics.height()) // line_height + 1)
        max_lines = min(self._max_lines, fit_lines)

        layout = QTextLayout(self._text, font)
        layout.beginLayout()
        y = 0
        line_count = 0
        while True:
            line = layout.createLine()
            if not line.isValid():
                break
            line.setLineWidth(width)
            line_count += 1
            if line_count == max_lines:
                start = line.textStart()
                elided = metrics.elidedText(self._text[start:], Qt.TextElideMode.ElideRight, width)
                painter.drawText(0, y + metrics.ascent(), elided)
                break
            else:
                line.draw(painter, QRectF(0, y, width, line_height).topLeft())
                y += line_height
        layout.endLayout()


class ElidedTitleLabel(QWidget):
    def __init__(self, text: str = "", max_lines: int = 2, parent=None):
        super().__init__(parent)
        self._text = text
        self._max_lines = max_lines
        self.setStyleSheet("background: transparent; border: none;")

    def setText(self, text: str):
        self._text = text
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QColor("#f1f5f9"))
        font = self.font()
        font.setPointSize(10)
        font.setBold(True)
        painter.setFont(font)

        metrics = QFontMetrics(font)
        line_height = metrics.lineSpacing()
        width = self.width()

        layout = QTextLayout(self._text, font)
        layout.beginLayout()
        y = 0
        line_count = 0
        while True:
            line = layout.createLine()
            if not line.isValid():
                break
            line.setLineWidth(width)
            line_count += 1
            if line_count == self._max_lines:
                start = line.textStart()
                elided = metrics.elidedText(self._text[start:], Qt.TextElideMode.ElideRight, width)
                painter.drawText(0, y + metrics.ascent(), elided)
                break
            else:
                line.draw(painter, QRectF(0, y, width, line_height).topLeft())
                y += line_height
        layout.endLayout()


class NavigationMenuPopup(QFrame):
    """Floating menu anchored to the hamburger button."""
    schedule_clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setStyleSheet("""
            QFrame#NavMenuBox {
                background-color: #1a1a27;
                border: 1px solid #323247;
                border-radius: 12px;
            }
            QPushButton {
                background: transparent;
                color: #e2e8f0;
                border: none;
                text-align: left;
                padding: 10px 14px;
                font-weight: 600;
                font-size: 13px;
                border-radius: 8px;
            }
            QPushButton:hover {
                background-color: #26263b;
                color: #38bdf8;
            }
        """)

        container = QFrame(self)
        container.setObjectName("NavMenuBox")
        
        box_layout = QVBoxLayout(self)
        box_layout.setContentsMargins(0, 0, 0, 0)
        box_layout.addWidget(container)

        l = QVBoxLayout(container)
        l.setContentsMargins(8, 8, 8, 8)
        l.setSpacing(4)

        btn_sched = QPushButton("📅  Weekly Airing Schedule")
        btn_sched.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_sched.clicked.connect(self._on_sched)
        l.addWidget(btn_sched)

    def _on_sched(self):
        self.hide()
        self.schedule_clicked.emit()


class HoverPreviewPopup(QFrame):
    play_requested = pyqtSignal(AnimeMedia, int)
    details_requested = pyqtSignal(AnimeMedia)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.ToolTip | Qt.WindowType.FramelessWindowHint | Qt.WindowType.NoDropShadowWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(320, 395)
        self.anime: AnimeMedia | None = None
        self.target_ep: int = 1
        self.bg_pixmap: QPixmap | None = None
        self.active_card: QWidget | None = None

        self.watch_timer = QTimer(self)
        self.watch_timer.setInterval(80)
        self.watch_timer.timeout.connect(self._check_cursor_bounds)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(10)

        self.badge_lbl = QLabel("")
        self.badge_lbl.setStyleSheet("color: #38bdf8; font-size: 11px; font-weight: 800; background: transparent;")
        layout.addWidget(self.badge_lbl)

        self.title_lbl = QLabel("")
        self.title_lbl.setStyleSheet("color: #ffffff; font-size: 17px; font-weight: 800; background: transparent;")
        self.title_lbl.setWordWrap(True)
        layout.addWidget(self.title_lbl)

        self.genres_lbl = QLabel("")
        self.genres_lbl.setStyleSheet("color: #94a3b8; font-size: 11px; font-weight: 600; background: transparent;")
        layout.addWidget(self.genres_lbl)

        self.desc_widget = ElidedParagraphLabel(max_lines=5)
        self.desc_widget.setFixedHeight(130)
        layout.addWidget(self.desc_widget)

        layout.addStretch()

        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(10)

        self.play_btn = QPushButton("▶ Play EP 1")
        self.play_btn.setFixedHeight(36)
        self.play_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.play_btn.setStyleSheet("""
            QPushButton {
                background-color: #38bdf8;
                color: #090a0f;
                font-weight: 800;
                font-size: 12px;
                border-radius: 6px;
                border: none;
            }
            QPushButton:hover { background-color: #7dd3fc; }
        """)
        self.play_btn.clicked.connect(self._on_play_clicked)
        btn_layout.addWidget(self.play_btn, stretch=1)

        self.details_btn = QPushButton("Details")
        self.details_btn.setFixedHeight(36)
        self.details_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.details_btn.setStyleSheet("""
            QPushButton {
                background-color: #212130;
                color: #e2e8f0;
                border: 1px solid #3d3d52;
                border-radius: 6px;
                font-weight: 700;
                font-size: 12px;
                padding: 0 12px;
            }
            QPushButton:hover { background-color: #2d2d42; color: #ffffff; }
        """)
        self.details_btn.clicked.connect(self._on_details_clicked)
        btn_layout.addWidget(self.details_btn)

        layout.addLayout(btn_layout)

    def set_anime(self, anime: AnimeMedia):
        # Check global cache if description is missing
        if not anime.description and anime.id in _ANIME_META_CACHE:
            cached = _ANIME_META_CACHE[anime.id]
            if cached.description:
                anime = cached

        self.anime = anime
        self.bg_pixmap = None
        self.title_lbl.setText(anime.title)

        total = max(1, anime.episodes or 1)
        if MainWindow._is_unreleased(anime):
            self.target_ep = None
            self.badge_lbl.setText("UPCOMING • NOT YET AIRED")
            self.play_btn.setVisible(False)
        else:
            self.play_btn.setVisible(True)
            watched = history_mgr.get_watched_episodes(anime.id)
            if watched:
                next_ep = history_mgr.get_next_episode(anime.id, total)
                self.target_ep = next_ep
                self.badge_lbl.setText(f"CONTINUE WATCHING • EP {next_ep}")
                self.play_btn.setText(f"▶ Play EP {next_ep}")
            else:
                self.target_ep = 1
                self.badge_lbl.setText("START WATCHING • EP 1")
                self.play_btn.setText("▶ Play EP 1")

        genres_str = " • ".join(anime.genres[:3]) if anime.genres else "Anime"
        self.genres_lbl.setText(f"{anime.year or 'TV'}   |   {anime.episodes} Ep   |   {genres_str}")
        self.desc_widget.setText(clean_html(anime.description))

        url = getattr(anime, "banner_url", "") or getattr(anime, "cover_url", "")
        if url:
            image_mgr.load(url, self._set_bg)

    def _set_bg(self, pixmap: QPixmap):
        self.bg_pixmap = pixmap
        self.update()

    def track_card(self, card_widget: QWidget):
        self.active_card = card_widget
        self.watch_timer.start()

    def _check_cursor_bounds(self):
        c_pos = QCursor.pos()
        if self.geometry().adjusted(-4, -4, 4, 4).contains(c_pos):
            return

        if self.active_card and self.active_card.isVisible():
            card_origin = self.active_card.mapToGlobal(QPoint(0, 0))
            card_rect = QRectF(
                float(card_origin.x()),
                float(card_origin.y()),
                float(self.active_card.width()),
                float(self.active_card.height())
            )
            # 16px bridge cushion between card and popup
            if card_rect.adjusted(-16, -16, 16, 16).contains(QPointF(c_pos)):
                return

        self.watch_timer.stop()
        self.active_card = None
        self.hide()

    def hideEvent(self, event):
        self.watch_timer.stop()
        self.active_card = None
        super().hideEvent(event)

    def _on_play_clicked(self):
        if self.anime:
            self.play_requested.emit(self.anime, self.target_ep)
            self.hide()

    def _on_details_clicked(self):
        if self.anime:
            self.details_requested.emit(self.anime)
            self.hide()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)

        path = QPainterPath()
        path.addRoundedRect(rect, 14, 14)
        painter.setClipPath(path)

        if self.bg_pixmap and not self.bg_pixmap.isNull():
            scaled = self.bg_pixmap.scaled(
                self.size(),
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation
            )
            painter.drawPixmap(0, 0, scaled)
            grad = QLinearGradient(0, 0, 0, self.height())
            grad.setColorAt(0.0, QColor(18, 18, 26, 220))
            grad.setColorAt(0.4, QColor(18, 18, 26, 245))
            grad.setColorAt(1.0, QColor(18, 18, 26, 255))
            painter.fillRect(self.rect(), QBrush(grad))
        else:
            painter.fillRect(self.rect(), QColor(22, 22, 32))

        painter.setClipping(False)
        painter.setPen(QPen(QColor(56, 189, 248, 140), 1.5))
        painter.drawRoundedRect(rect, 14, 14)


from PyQt6.QtCore import QVariantAnimation as _QVA, QPointF as _QPF
from hayate.core.settings import load_settings as _load_cfg


from PyQt6.QtWidgets import (QGraphicsScene as _GS, QGraphicsPixmapItem as _GPI,
                             QGraphicsBlurEffect as _GBE)
from PyQt6.QtGui import QImage as _QI, QPixmap as _QPM, QPainter as _QPT, QPalette as _PAL
from PyQt6.QtCore import QVariantAnimation as _QVA2, QEasingCurve as _QEC

_APP_BG = "#14141e"   # app background; adjust if transitions flash a different shade
_BLUR_CACHE: dict = {}


def _get_blur(pix):
    key = pix.cacheKey()
    hit = _BLUR_CACHE.get(key)
    if hit is not None:
        return hit
    w, h, dpr = pix.width(), pix.height(), pix.devicePixelRatio()
    sw, sh = max(1, w // 4), max(1, h // 4)
    small = pix.scaled(sw, sh, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
    small.setDevicePixelRatio(1.0)
    pad = 16
    pw, ph = sw + 2 * pad, sh + 2 * pad
    padded = _QPM(pw, ph)
    padded.fill(Qt.GlobalColor.transparent)
    pp = _QPT(padded)
    pp.drawPixmap(0, 0, pw, ph, small)
    pp.drawPixmap(pad, pad, small)
    pp.end()
    scene = _GS()
    item = _GPI(padded)
    fx = _GBE()
    fx.setBlurRadius(7)
    fx.setBlurHints(_GBE.BlurHint.QualityHint)
    item.setGraphicsEffect(fx)
    scene.addItem(item)
    img = _QI(pw, ph, _QI.Format.Format_ARGB32_Premultiplied)
    img.fill(0)
    ip = _QPT(img)
    scene.render(ip, QRectF(0, 0, pw, ph), QRectF(0, 0, pw, ph))
    ip.end()
    out = _QPM.fromImage(img.copy(pad, pad, sw, sh)).scaled(
        w, h, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
    out.setDevicePixelRatio(dpr)
    if len(_BLUR_CACHE) > 1:
        _BLUR_CACHE.clear()
    _BLUR_CACHE[key] = out
    return out


def _draw_blurred(p, target, pix, k, src=None):
    target = QRectF(target)
    src = QRectF(src) if src is not None else QRectF(pix.rect())
    a = min(1.0, max(0.0, k) * 1.5)
    base = p.opacity()
    if a < 0.03 or pix is None or pix.isNull():
        p.drawPixmap(target, pix, src)
        return
    if a < 0.98:
        p.drawPixmap(target, pix, src)
    p.setOpacity(base * a)
    p.drawPixmap(target, _get_blur(pix), src)
    p.setOpacity(base)


class _PageOverlay(QWidget):
    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.snap = None
        self.style_ = "fade"
        self.forward = True
        self.t = 0.0
        self.k = 0.6

    def setup(self, snap, style, forward, k):
        bg = _QPM(snap.size())
        bg.setDevicePixelRatio(snap.devicePixelRatio())
        bg.fill(QColor(_APP_BG))
        pp = _QPT(bg)
        pp.drawPixmap(0, 0, snap)
        pp.end()
        self.snap, self.style_, self.forward, self.t, self.k = bg, style, forward, 0.0, k
        _get_blur(bg)

    def set_t(self, v):
        self.t = float(v)
        self.update()

    def paintEvent(self, _):
        if self.snap is None:
            return
        p = _QPT(self)
        p.setRenderHint(_QPT.RenderHint.SmoothPixmapTransform)
        p.setOpacity(max(0.0, 1.0 - self.t))
        r = QRectF(self.rect())
        if self.style_ == "slide":
            p.translate((-1 if self.forward else 1) * 48 * self.t, 0)
        elif self.style_ == "zoom":
            c = r.center()
            s = 1.0 + 0.05 * self.t
            p.translate(c)
            p.scale(s, s)
            p.translate(-c)
        _draw_blurred(p, r, self.snap, self.k * self.t, QRectF(self.snap.rect()))


class AnimatedStack(QStackedWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        pal = self.palette()
        pal.setColor(_PAL.ColorRole.Window, QColor(_APP_BG))
        self.setPalette(pal)
        self.setAutoFillBackground(True)
        self._ov = _PageOverlay(self)
        self._ov.hide()
        self._a = _QVA2(self)
        self._a.setStartValue(0.0)
        self._a.setEndValue(1.0)
        self._a.setEasingCurve(_QEC.Type.InOutCubic)
        self._a.valueChanged.connect(self._ov.set_t)
        self._a.finished.connect(self._ov.hide)
        self._a.finished.connect(lambda: (setattr(self._ov, 'snap', None), _BLUR_CACHE.clear()))

    def setCurrentWidget(self, w):
        self.setCurrentIndex(self.indexOf(w))

    def setCurrentIndex(self, i):
        cfg = _load_cfg()
        style = cfg.get("page_transition", "fade")
        ms = int(cfg.get("page_transition_ms", 260))
        cur = self.currentIndex()
        if style == "none" or ms <= 0 or i == cur or cur < 0 or not self.isVisible():
            self._a.stop()
            self._ov.hide()
            super().setCurrentIndex(i)
            return
        self._a.stop()
        snap = self.grab()
        super().setCurrentIndex(i)
        self._ov.setGeometry(self.rect())
        self._ov.setup(snap, style, i > cur, cfg.get("transition_blur", 60) / 100.0)
        self._ov.raise_()
        self._ov.show()
        self._a.setDuration(ms)
        self._a.start()


class _BannerVeil(QWidget):
    """Previous frame drawn on top; a growing mask reveals the live new frame."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.snap = None
        self.style_ = "wipe"
        self.forward = True
        self.origin = _QPF(0, 0)
        self.t = 0.0
        self.hide()

    def paintEvent(self, _):
        if self.snap is None:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        r = QRectF(self.rect())
        t, st = self.t, self.style_
        rounded = QPainterPath()
        rounded.addRoundedRect(r.adjusted(1, 1, -1, -1), 16, 16)
        if st == "fade":
            p.setClipPath(rounded)
            p.setOpacity(max(0.0, 1.0 - t))
        else:
            maxr = (r.width() ** 2 + r.height() ** 2) ** 0.5
            keep = QPainterPath()
            if st == "outer":
                keep.addEllipse(self.origin, maxr * (1 - t), maxr * (1 - t))
            elif st in ("grow", "center"):
                c = r.center() if st == "center" else self.origin
                hole = QPainterPath()
                hole.addEllipse(c, maxr * t, maxr * t)
                keep.addRect(r)
                keep = keep.subtracted(hole)
            else:  # wipe
                if self.forward:
                    keep.addRect(QRectF(0, 0, r.width() * (1 - t), r.height()))
                else:
                    keep.addRect(QRectF(r.width() * t, 0, r.width() * (1 - t), r.height()))
            p.setClipPath(rounded.intersected(keep))
        _draw_blurred(p, self.rect(), self.snap, getattr(self, 'k', 0.6) * self.t)


class HeroSpotlightBanner(QWidget):
    banner_clicked = pyqtSignal()
    watch_requested = pyqtSignal()
    prev_requested = pyqtSignal()
    next_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(420)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.banner_pixmap: QPixmap | None = None
        self._slide_offset: float = 0.0
        self._swipe_cooldown: bool = False
        self.current_anime_id: int | None = None

        self.anim = QPropertyAnimation(self, b"slideOffset")
        self.anim.setDuration(550)
        self.anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._veil = _BannerVeil(self)
        self._tr_ms = 550
        self._veil_anim = _QVA(self)
        self._veil_anim.setStartValue(0.0)
        self._veil_anim.setEndValue(1.0)
        self._veil_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._veil_anim.valueChanged.connect(self._veil_tick)
        self._veil_anim.finished.connect(self._veil.hide)
        self._veil_anim.finished.connect(lambda: (setattr(self._veil, 'snap', None), _BLUR_CACHE.clear()))

        self.installEventFilter(self)
        self.installEventFilter(self)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(48, 36, 48, 32)
        layout.setSpacing(0)

        self.content_container = QWidget()
        self.content_container.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.content_container.setMaximumWidth(600)
        self.content_container.setStyleSheet("background: transparent; border: none;")

        c_layout = QVBoxLayout(self.content_container)
        c_layout.setContentsMargins(0, 0, 0, 0)
        c_layout.setSpacing(10)

        self.tag_lbl = QLabel("#1 SPOTLIGHT")
        self.tag_lbl.setStyleSheet("color: #38bdf8; font-size: 12px; font-weight: 800; letter-spacing: 1.5px; background: transparent;")
        c_layout.addWidget(self.tag_lbl)

        self.title_lbl = QLabel("Spotlight Title")
        self.title_lbl.setStyleSheet("color: #ffffff; font-size: 30px; font-weight: 800; background: transparent;")
        self.title_lbl.setWordWrap(True)
        c_layout.addWidget(self.title_lbl)

        self.meta_lbl = QLabel("")
        self.meta_lbl.setStyleSheet("color: #94a3b8; font-size: 13px; font-weight: 600; background: transparent;")
        c_layout.addWidget(self.meta_lbl)

        self.desc_widget = ElidedParagraphLabel(max_lines=4)
        self.desc_widget.setFixedHeight(95)
        c_layout.addWidget(self.desc_widget)

        layout.addWidget(self.content_container)
        layout.addStretch()

        bottom_bar = QHBoxLayout()
        self.watch_btn = QPushButton("▶  Watch Now")
        self.watch_btn.setFixedSize(155, 46)
        self.watch_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.watch_btn.setStyleSheet("""
            QPushButton {
                background-color: #38bdf8;
                color: #090a0f;
                font-weight: 800;
                font-size: 14px;
                border-radius: 8px;
                border: none;
            }
            QPushButton:hover { background-color: #7dd3fc; }
        """)
        self.watch_btn.clicked.connect(self.watch_requested.emit)
        bottom_bar.addWidget(self.watch_btn)
        bottom_bar.addStretch()

        nav_arrows = QHBoxLayout()
        nav_arrows.setSpacing(12)
        arrow_style = """
            QPushButton {
                background-color: rgba(255, 255, 255, 0.12);
                color: #ffffff;
                font-size: 18px;
                font-weight: bold;
                border-radius: 8px;
                border: 1px solid rgba(255, 255, 255, 0.2);
            }
            QPushButton:hover { background-color: rgba(255, 255, 255, 0.22); }
        """
        self.prev_btn = QPushButton("<")
        self.prev_btn.setFixedSize(42, 42)
        self.prev_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.prev_btn.setStyleSheet(arrow_style)
        self.prev_btn.clicked.connect(self.prev_requested.emit)
        nav_arrows.addWidget(self.prev_btn)

        self.next_btn = QPushButton(">")
        self.next_btn.setFixedSize(42, 42)
        self.next_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.next_btn.setStyleSheet(arrow_style)
        self.next_btn.clicked.connect(self.next_requested.emit)
        nav_arrows.addWidget(self.next_btn)

        bottom_bar.addLayout(nav_arrows)
        layout.addLayout(bottom_bar)

    def _veil_tick(self, v):
        self._veil.t = float(v)
        self._veil.update()

    def _begin_transition(self) -> str:
        cfg = _load_cfg()
        style = cfg.get("banner_transition", "slide")
        self._tr_ms = int(cfg.get("banner_transition_ms", 550))
        if style not in ("wipe", "grow", "outer", "center", "fade") \
                or not self.isVisible() or self.banner_pixmap is None:
            self._veil_anim.stop()
            self._veil.hide()
            return "slide"
        self._veil_anim.stop()
        self._veil.snap = self.grab()   # includes a half-finished veil, so interrupts are seamless
        return style

    def _finish_transition(self, style: str, forward: bool):
        if style == "slide":
            self.anim.setDuration(self._tr_ms)
            self.trigger_slide(forward)
            return
        self.anim.stop()
        self.set_slide_offset(0.0)
        v = self._veil
        v.setGeometry(self.rect())
        v.style_, v.forward, v.t = style, forward, 0.0
        v.k = _load_cfg().get('transition_blur', 60) / 100.0
        btn = self.next_btn if forward else self.prev_btn
        v.origin = _QPF(btn.mapTo(self, btn.rect().center()))
        v.raise_()
        v.show()
        self._veil_anim.setDuration(self._tr_ms)
        self._veil_anim.start()

    def get_slide_offset(self) -> float:
        return self._slide_offset

    def set_slide_offset(self, val: float):
        self._slide_offset = val
        self.content_container.move(int(48 + val), int(36))
        self.update()

    slideOffset = pyqtProperty(float, get_slide_offset, set_slide_offset)

    def trigger_slide(self, forward: bool = True):
        start_x = 75.0 if forward else -75.0
        self.anim.stop()
        self.anim.setStartValue(start_x)
        self.anim.setEndValue(0.0)
        self.anim.start()

    def wheelEvent(self, event: QWheelEvent):
        dx = event.pixelDelta().x() or event.angleDelta().x()
        if abs(dx) > 30 and not self._swipe_cooldown:
            self._swipe_cooldown = True
            if dx < 0:
                self.next_requested.emit()
            else:
                self.prev_requested.emit()
            QTimer.singleShot(450, self._reset_swipe)
            event.accept()
            return
        super().wheelEvent(event)

    def _reset_swipe(self):
        self._swipe_cooldown = False

    def set_data(self, anime: AnimeMedia, rank: int, forward: bool = True):
        _style = self._begin_transition()
        self.current_anime_id = anime.id
        self.tag_lbl.setText(f"#{rank} SPOTLIGHT")
        self.title_lbl.setText(anime.title)
        genres = " • ".join(anime.genres[:3]) if anime.genres else "Anime"
        self.meta_lbl.setText(f"{anime.year or 'N/A'}  •  {anime.episodes} Episodes  •  {genres}")
        self.desc_widget.setText(clean_html(anime.description))

        if anime.id in _SPOTLIGHT_PIX_CACHE:
            self.banner_pixmap = _SPOTLIGHT_PIX_CACHE[anime.id]
            self.update()
        else:
            url = getattr(anime, "banner_url", None) or getattr(anime, "cover_url", None)
            if url:
                image_mgr.load(
                    url,
                    lambda pix, aid=anime.id: self._on_banner_loaded(aid, pix),
                    priority=1000
                )

        self._finish_transition(_style, forward)

    def _on_banner_loaded(self, aid: int, pixmap: QPixmap):
        _SPOTLIGHT_PIX_CACHE[aid] = pixmap
        if self.current_anime_id == aid:
            self.banner_pixmap = pixmap
            self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        path = QPainterPath()
        path.addRoundedRect(rect, 16, 16)
        painter.setClipPath(path)

        if self.banner_pixmap and not self.banner_pixmap.isNull():
            scaled = self.banner_pixmap.scaled(
                self.size(),
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation
            )
            x_offset = max(0, scaled.width() - self.width())
            painter.drawPixmap(-x_offset // 2, 0, scaled)
            grad = QLinearGradient(0, 0, self.width(), 0)
            grad.setColorAt(0.0, QColor(20, 20, 30, 255))
            grad.setColorAt(0.48, QColor(20, 20, 30, 230))
            grad.setColorAt(0.78, QColor(20, 20, 30, 110))
            grad.setColorAt(1.0, QColor(20, 20, 30, 50))
            painter.fillRect(self.rect(), QBrush(grad))
        else:
            painter.fillRect(self.rect(), QColor(26, 26, 38))

        painter.setClipping(False)
        painter.setPen(QPen(QColor(255, 255, 255, 20), 1.0))
        painter.drawRoundedRect(rect, 16, 16)


class PosterImageWidget(QWidget):
    def __init__(self, fallback_title: str = "", parent=None):
        super().__init__(parent)
        self.setFixedSize(185, 260)
        self.pixmap: QPixmap | None = None
        self.fallback_title = fallback_title
        self.progress_percent: float = 0.0

    def set_pixmap(self, pixmap: QPixmap):
        self.pixmap = pixmap
        self.update()

    def set_progress(self, percent: float):
        self.progress_percent = max(0.0, min(100.0, percent))
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(rect, 12, 12)
        painter.setClipPath(path)

        if self.pixmap and not self.pixmap.isNull():
            scaled = self.pixmap.scaled(
                self.size(),
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation
            )
            x = (self.width() - scaled.width()) // 2
            y = (self.height() - scaled.height()) // 2
            painter.drawPixmap(x, y, scaled)
        else:
            painter.fillRect(self.rect(), QColor(28, 28, 42))
            painter.setPen(QColor(80, 80, 110))
            font = self.font()
            font.setPointSize(28)
            painter.setFont(font)
            painter.drawText(QRectF(0, 50, self.width(), 60), Qt.AlignmentFlag.AlignCenter, "🎬")

            font.setPointSize(9)
            font.setBold(True)
            painter.setFont(font)
            painter.setPen(QColor(148, 163, 184))
            text_rect = QRectF(12, 120, self.width() - 24, 100)
            painter.drawText(text_rect, Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, self.fallback_title)

        if self.progress_percent > 0:
            bar_height = 4.0
            y_bar = self.height() - bar_height
            painter.fillRect(QRectF(0, y_bar, self.width(), bar_height), QColor(0, 0, 0, 180))
            fill_width = (self.width() * self.progress_percent) / 100.0
            painter.fillRect(QRectF(0, y_bar, fill_width, bar_height), QColor("#38bdf8"))

        painter.setClipping(False)
        painter.setPen(QPen(QColor(60, 60, 85, 140), 1.2))
        painter.drawRoundedRect(rect, 12, 12)


class PosterCard(QWidget):
    clicked = pyqtSignal(AnimeMedia)
    hovered = pyqtSignal(AnimeMedia, QPoint, QWidget)

    def __init__(self, anime: AnimeMedia, progress_percent: float = 0.0, priority: int = 10, parent=None):
        super().__init__(parent)
        self.anime = anime
        self.setFixedSize(185, 305)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        self._press_pos: QPoint | None = None
        self._hover_timer = QTimer(self)
        self._hover_timer.setSingleShot(True)
        self._hover_timer.setInterval(600)
        self._hover_timer.timeout.connect(self._trigger_hover_preview)

        # Cache meta globally
        _ANIME_META_CACHE[anime.id] = anime

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self.poster_widget = PosterImageWidget(fallback_title=anime.title)
        if progress_percent > 0:
            self.poster_widget.set_progress(progress_percent)
        layout.addWidget(self.poster_widget)

        self.title_widget = ElidedTitleLabel(anime.title, max_lines=2)
        self.title_widget.setFixedHeight(36)
        layout.addWidget(self.title_widget)

        if getattr(anime, "cover_url", None):
            image_mgr.load(anime.cover_url, self.poster_widget.set_pixmap, priority=priority)

    def enterEvent(self, event):
        self._hover_timer.start()
        super().enterEvent(event)

    def leaveEvent(self, event):
        # Only stop timer if cursor actually left the entire outer card
        card_rect = QRectF(0, 0, float(self.width()), float(self.height()))
        local_c = self.mapFromGlobal(QCursor.pos())
        if not card_rect.contains(QPointF(local_c)):
            self._hover_timer.stop()
        super().leaveEvent(event)

    def _trigger_hover_preview(self):
        try:
            card_rect = QRectF(0, 0, float(self.width()), float(self.height()))
            local_c = self.mapFromGlobal(QCursor.pos())
            if card_rect.contains(QPointF(local_c)):
                # Calculate global anchor on right side
                global_pos = self.mapToGlobal(QPoint(self.width() + 8, 0))
                self.hovered.emit(self.anime, global_pos, self)
        except Exception as e:
            print(f"[Hover] trigger error: {e}")

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_pos = event.globalPosition().toPoint()
            self._hover_timer.stop()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton and self._press_pos is not None:
            delta = (event.globalPosition().toPoint() - self._press_pos).manhattanLength()
            self._press_pos = None
            if delta < 7:
                self.clicked.emit(self.anime)
        super().mouseReleaseEvent(event)


class DraggableScrollArea(QScrollArea):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._dragging = False
        self._drag_start_x = 0
        self._scroll_start_x = 0

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._drag_start_x = event.globalPosition().toPoint().x()
            self._scroll_start_x = self.horizontalScrollBar().value()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent):
        if self._dragging:
            cur_x = event.globalPosition().toPoint().x()
            delta = cur_x - self._drag_start_x
            if abs(delta) > 5:
                QApplication.setOverrideCursor(Qt.CursorShape.ClosedHandCursor)
            self.horizontalScrollBar().setValue(self._scroll_start_x - delta)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton and self._dragging:
            self._dragging = False
            QApplication.restoreOverrideCursor()
            event.accept()
            return
        super().mouseReleaseEvent(event)


class AnimeShelfWidget(QFrame):
    card_clicked = pyqtSignal(AnimeMedia)
    card_hovered = pyqtSignal(AnimeMedia, QPoint, QWidget)

    def __init__(self, title: str, title_color: str = "#ffffff", parent=None):
        super().__init__(parent)
        self.setObjectName("shelf_frame")
        self.setStyleSheet("""
            QFrame#shelf_frame {
                background-color: #1e1e2d;
                border-radius: 16px;
                border: 1.5px solid #2f2f44;
            }
        """)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 20, 28, 20)
        layout.setSpacing(14)

        self.title_lbl = QLabel(title)
        self.title_lbl.setStyleSheet(f"font-size: 19px; font-weight: 800; color: {title_color}; background: transparent; border: none;")
        layout.addWidget(self.title_lbl)

        self.scroll = DraggableScrollArea()
        self.scroll.setFixedHeight(315)
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")

        self.inner_widget = QWidget()
        self.inner_widget.setStyleSheet("background: transparent; border: none;")
        self.inner_layout = QHBoxLayout(self.inner_widget)
        self.inner_layout.setContentsMargins(0, 0, 0, 0)
        self.inner_layout.setSpacing(20)
        self.scroll.setWidget(self.inner_widget)

        layout.addWidget(self.scroll)

    def set_items(self, anime_list: list[tuple[AnimeMedia, float]] | list[AnimeMedia], base_priority: int = 100):
        while self.inner_layout.count():
            item = self.inner_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        for idx, item in enumerate(anime_list):
            if isinstance(item, tuple):
                anime, progress = item
            else:
                anime, progress = item, 0.0

            priority = base_priority if idx < 10 else 10
            card = PosterCard(anime, progress_percent=progress, priority=priority)
            card.clicked.connect(self.card_clicked.emit)
            card.hovered.connect(self.card_hovered.emit)
            self.inner_layout.addWidget(card)

        self.inner_layout.addStretch()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Hayate")
        self.setMinimumSize(1000, 700)
        screen = QApplication.primaryScreen()
        if screen:
            self.setGeometry(screen.availableGeometry())
        else:
            self.resize(1260, 900)
        self.player = PlayerController()
        self.spotlight_items: list[AnimeMedia] = []
        self.spotlight_index: int = 0
        self.home_shelves_worker: HomeShelvesWorker | None = None
        self.search_worker: SearchWorker | None = None
        self.single_worker: SingleAnimeWorker | None = None
        self.preview_worker: SpotlightPreviewWorker | None = None

        self.spotlight_timer = QTimer(self)
        self.spotlight_timer.setInterval(7500)
        self.spotlight_timer.timeout.connect(self.next_spotlight)

        self.hover_preview = HoverPreviewPopup(self)
        self.hover_preview.play_requested.connect(self.play_anime_episode)
        self.hover_preview.details_requested.connect(self.open_details_view)

        self.setStyleSheet("""
            QMainWindow { background-color: #171722; }
            QWidget {
                color: #e2e8f0;
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            }
            QLineEdit {
                background-color: #212130;
                color: #ffffff;
                border: 1.5px solid #3d3d52;
                border-radius: 8px;
                padding: 6px 14px;
                font-size: 13px;
            }
            QLineEdit:focus { border: 1.5px solid #38bdf8; }
            QScrollArea { border: none; background: transparent; }
        """)

        self.stack = AnimatedStack(self)
        self.setCentralWidget(self.stack)

        self.home_scroll = QScrollArea()
        self.home_scroll.setWidgetResizable(True)
        self.home_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.home_scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")

        home_container = QWidget()
        home_container.setStyleSheet("background-color: #171722;")
        self.root_layout = QVBoxLayout(home_container)
        self.root_layout.setContentsMargins(40, 24, 40, 40)
        self.root_layout.setSpacing(0)
        self.home_scroll.setWidget(home_container)

        nav = QHBoxLayout()
        nav.setSpacing(14)

        self.nav_popup = NavigationMenuPopup(self)
        self.nav_popup.schedule_clicked.connect(self.open_schedule_view)

        self.menu_btn = HamburgerButton()
        self.menu_btn.clicked.connect(self.toggle_nav_popup)
        nav.addWidget(self.menu_btn)

        self.brand_widget = QWidget()
        self.brand_widget.setStyleSheet("background: transparent; border: none;")
        brand_layout = QHBoxLayout(self.brand_widget)
        brand_layout.setContentsMargins(4, 0, 10, 0)
        brand_layout.setSpacing(10)

        self.logo_lbl = QLabel()
        self.logo_lbl.setFixedSize(32, 32)
        from pathlib import Path as _P
        logo_pix = QPixmap()
        for _c in (_P(__file__).resolve().parents[3] / "app_icon.png",
                   _P("app_icon.png"),
                   _P("/usr/share/icons/hicolor/256x256/apps/hayate.png"),
                   _P("/app/share/icons/hicolor/256x256/apps/io.github.Piyush_1927.Hayate.png")):
            if _c.is_file() and logo_pix.load(str(_c)):
                break
        if not logo_pix.isNull():
            self.logo_lbl.setPixmap(logo_pix.scaled(32, 32, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        brand_layout.addWidget(self.logo_lbl)

        self.brand_title = QLabel("Hayate")
        self.brand_title.setStyleSheet("font-size: 18px; font-weight: 800; color: #ffffff;")
        brand_layout.addWidget(self.brand_title)
        nav.addWidget(self.brand_widget)

        self.search_input = QLineEdit()
        self.search_input.setFixedHeight(38)
        self.search_input.setPlaceholderText("Search anime...")
        self.search_input.returnPressed.connect(self.trigger_search)
        nav.addWidget(self.search_input, stretch=1)

        self.filter_btn = QPushButton("Filter")
        self.filter_btn.setFixedHeight(38)
        self.filter_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.filter_btn.setStyleSheet("""
            QPushButton {
                background-color: #212130;
                color: #e2e8f0;
                border: 1.5px solid #3d3d52;
                border-radius: 8px;
                padding: 0 16px;
                font-weight: 600;
                font-size: 13px;
            }
            QPushButton:hover { background-color: #2d2d42; }
        """)
        nav.addWidget(self.filter_btn)

        self.settings_btn = QPushButton("⚙")
        self.settings_btn.setFixedSize(38, 38)
        self.settings_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.settings_btn.setStyleSheet("""
            QPushButton {
                background-color: #212130;
                color: #e2e8f0;
                border: 1.5px solid #3d3d52;
                border-radius: 8px;
                font-size: 16px;
            }
            QPushButton:hover { background-color: #2d2d42; border-color: #38bdf8; }
        """)
        self.settings_btn.clicked.connect(self.open_settings_view)
        nav.addWidget(self.settings_btn)

        self.root_layout.addLayout(nav)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setFixedHeight(2)
        self.progress.setTextVisible(False)
        self.progress.setVisible(False)
        self.root_layout.addWidget(self.progress)
        self.root_layout.addSpacing(24)

        # 1. Spotlight Banner
        self.hero_banner = HeroSpotlightBanner()
        self.hero_banner.banner_clicked.connect(self.on_spotlight_clicked)
        self.hero_banner.watch_requested.connect(self.on_spotlight_watch)
        self.hero_banner.prev_requested.connect(self.prev_spotlight)
        self.hero_banner.next_requested.connect(self.next_spotlight)
        self.root_layout.addWidget(self.hero_banner)
        self.root_layout.addSpacing(32)

        # 2. Continue Watching Shelf
        self.history_shelf = AnimeShelfWidget("Continue Watching", title_color="#38bdf8")
        self._connect_shelf(self.history_shelf)
        self.root_layout.addWidget(self.history_shelf)
        self.history_spacer = QWidget()
        self.history_spacer.setFixedHeight(32)
        self.root_layout.addWidget(self.history_spacer)

        # 3. Trending Shelf
        self.trending_shelf = AnimeShelfWidget("Trending")
        self._connect_shelf(self.trending_shelf)
        self.root_layout.addWidget(self.trending_shelf)
        self.root_layout.addSpacing(32)

        # 4. New Anime Releases Shelf
        self.new_releases_shelf = AnimeShelfWidget("New Anime Releases")
        self._connect_shelf(self.new_releases_shelf)
        self.root_layout.addWidget(self.new_releases_shelf)
        self.root_layout.addSpacing(32)

        # 5. Just Finished Airing Shelf
        self.finished_shelf = AnimeShelfWidget("Just Finished Airing")
        self._connect_shelf(self.finished_shelf)
        self.root_layout.addWidget(self.finished_shelf)
        self.root_layout.addSpacing(32)

        # 6. Upcoming Animes Shelf
        self.upcoming_shelf = AnimeShelfWidget("Upcoming Animes (Confirmed)")
        self._connect_shelf(self.upcoming_shelf)
        self.root_layout.addWidget(self.upcoming_shelf)
        self.root_layout.addSpacing(32)



        self.root_layout.addStretch()
        self.stack.addWidget(self.home_scroll)

        # Details View
        self.details_view = DetailsView(self.player)
        self.details_view.back_requested.connect(self.on_back_to_home)
        self.details_view.relation_selected.connect(self.open_by_id)
        self.details_view.play_requested.connect(self.open_player_view)
        self.stack.addWidget(self.details_view)

        # Player View (Dedicated Theater Page)
        self.player_view = PlayerView(self.player)
        self.player_view.back_requested.connect(self.on_back_to_details)
        self.stack.addWidget(self.player_view)

        # Schedule View
        self.schedule_view = ScheduleView(self.player)
        self.schedule_view.back_requested.connect(self.on_back_to_home)
        self.schedule_view.play_requested.connect(self.play_from_schedule)
        self.schedule_view.anime_selected.connect(self.open_by_id)
        self.stack.addWidget(self.schedule_view)

        # Settings View (Page 3)
        self.settings_view = SettingsView()
        self.settings_view.back_requested.connect(self.on_back_to_home)
        self.settings_view.settings_changed.connect(self.load_home)
        self.stack.addWidget(self.settings_view)

        # Search View (Page 4)
        self.search_view = SearchView(poster_card_cls=PosterCard)
        self.search_view.back_requested.connect(self.on_back_to_home)
        self.search_view.anime_selected.connect(self.open_details_view)
        self.search_view.card_hovered.connect(self._show_hover_preview)
        self.search_view.open_settings_requested.connect(self.open_settings_view)
        self.stack.addWidget(self.search_view)

        # Upcoming (unreleased) titles get their own description page
        self.upcoming_view = UpcomingDetailsView()
        self.upcoming_view.back_requested.connect(self.on_back_to_home)
        self.upcoming_view.relation_selected.connect(self.open_by_id)
        self.stack.addWidget(self.upcoming_view)

        self.load_home()

    def open_player_view(self, anime, ep: int):
        self.player_view.open_anime(anime, ep)
        self.stack.setCurrentWidget(self.player_view)

    def on_back_to_details(self):
        self.stack.setCurrentWidget(self.details_view)

    def _connect_shelf(self, shelf: AnimeShelfWidget):
        shelf.card_clicked.connect(self.open_details_view)
        shelf.card_hovered.connect(self._show_hover_preview)

    def _show_hover_preview(self, anime: AnimeMedia, global_pos: QPoint, card_widget: QWidget):
        if self.stack.currentIndex() != 0:
            return
        self.hover_preview.set_anime(anime)

        screen = QApplication.primaryScreen().availableGeometry()
        x = global_pos.x() + 8
        y = global_pos.y()

        if x + self.hover_preview.width() > screen.right() - 20:
            x = global_pos.x() - self.hover_preview.width() - 205

        if y + self.hover_preview.height() > screen.bottom() - 20:
            y = screen.bottom() - self.hover_preview.height() - 20
        y = max(screen.top() + 20, y)

        self.hover_preview.move(x, y)
        self.hover_preview.show()
        self.hover_preview.track_card(card_widget)

    def play_anime_episode(self, anime: AnimeMedia, episode: int):
        self.hover_preview.hide()
        self.open_details_view(anime)
        self.details_view.auto_play_episode(episode)

    def closeEvent(self, event: QCloseEvent):
        # Terminate libmpv before the native video surface is destroyed.
        try:
            self.player_view._shutdown_playback()
        except Exception:
            import logging
            logging.exception("Error shutting down playback on application exit")

        _SPOTLIGHT_PIX_CACHE.clear()
        try:
            self.spotlight_timer.stop()
        except Exception:
            pass
        try:
            self.hover_preview.hide()
        except Exception:
            pass

        shutdown_workers()

        # 1. Stop background QThreadPool tasks safely
        try:
            from hayate.core.image_cache import ImageManager
            ImageManager.get().shutdown()
        except Exception:
            pass

        event.accept()

    def toggle_nav_popup(self):
        if self.nav_popup.isVisible():
            self.nav_popup.hide()
        else:
            btn_pos = self.menu_btn.mapToGlobal(QPoint(0, self.menu_btn.height() + 8))
            self.nav_popup.move(btn_pos)
            self.nav_popup.show()


    def open_settings_view(self):
        self.spotlight_timer.stop()
        self.hover_preview.hide()
        self.progress.setVisible(False)
        self.stack.setCurrentWidget(self.settings_view)

    def open_schedule_view(self):
        self.spotlight_timer.stop()
        self.hover_preview.hide()
        self.progress.setVisible(False)
        self.schedule_view.load_schedule()
        self.stack.setCurrentWidget(self.schedule_view)

    def on_back_to_home(self):
        self.progress.setVisible(False)
        self.hover_preview.hide()
        self.stack.setCurrentWidget(self.home_scroll)
        self._refresh_history_shelf()
        if self.spotlight_items:
            self.spotlight_timer.start()

    def _refresh_history_shelf(self):
        recent = history_mgr.get_recent_history()
        if not recent:
            self.history_shelf.setVisible(False)
            self.history_spacer.setVisible(False)
            return

        self.history_shelf.setVisible(True)
        self.history_spacer.setVisible(True)
        shelf_data = []
        for h in recent:
            total = max(h.get("total_episodes") or 1, h.get("last_episode", 1), 1)
            last = h.get("last_episode", 1)

            if total > 1:
                progress = min(95.0, (last / total) * 100.0)
            else:
                progress = 25.0

            # Pull cached description if available
            cached_desc = ""
            if h["id"] in _ANIME_META_CACHE:
                cached_desc = _ANIME_META_CACHE[h["id"]].description

            media = AnimeMedia(
                id=h["id"],
                title=h["title"],
                cover_url=h.get("cover_url", ""),
                banner_url=h.get("banner_url", ""),
                description=cached_desc,
                episodes=total
            )
            shelf_data.append((media, progress))

        self.history_shelf.set_items(shelf_data, base_priority=500)

    def play_from_schedule(self, anime_id: int, episode: int):
        self.spotlight_timer.stop()
        self.hover_preview.hide()
        self.progress.setVisible(True)
        self._nav_token = getattr(self, "_nav_token", 0) + 1
        token = self._nav_token
        worker = SingleAnimeWorker(anime_id)
        def on_done(media):
            if token != self._nav_token:
                return
            self.progress.setVisible(False)
            if media:
                self.open_details_view(media)
                self.details_view.auto_play_episode(episode)
        worker.data_ready.connect(on_done)
        run_worker(worker)

    @staticmethod
    def _is_unreleased(anime: AnimeMedia) -> bool:
        """True for titles that have not started airing (AniList status, with a start-date fallback)."""
        status = getattr(anime, "status", None)
        if status:
            return status == "NOT_YET_RELEASED"
        if anime.airing_episode or not anime.start_date or not anime.start_date[0]:
            return False
        from datetime import date
        today = date.today()
        y, m, d = anime.start_date
        if y != today.year:
            return y > today.year
        if not m:
            return False
        if m != today.month:
            return m > today.month
        return bool(d) and d > today.day

    def open_by_id(self, anime_id: int):
        self.spotlight_timer.stop()
        self.hover_preview.hide()
        self.progress.setVisible(True)
        self._nav_token = getattr(self, "_nav_token", 0) + 1
        token = self._nav_token
        worker = SingleAnimeWorker(anime_id)
        def on_done(media):
            if token != self._nav_token:
                return  # a newer click superseded this one
            self.progress.setVisible(False)
            if media:
                self.open_details_view(media)
        worker.data_ready.connect(on_done)
        run_worker(worker)

    def open_details_view(self, anime: AnimeMedia):
        self.spotlight_timer.stop()
        self.hover_preview.hide()
        self.progress.setVisible(False)
        # Cards built from watch history only carry basic info, so prefer the full cached record
        cached = _ANIME_META_CACHE.get(anime.id)
        incomplete = lambda a: not a.description or not a.genres or not a.year
        if cached and cached.description and incomplete(anime):
            anime = cached
        if self._is_unreleased(anime):
            self.upcoming_view.set_anime(anime)
            self.stack.setCurrentWidget(self.upcoming_view)
            if incomplete(anime):
                self._enrich_details(anime.id)
            return
        self.details_view.set_anime(anime)
        self.stack.setCurrentIndex(1)
        if incomplete(anime):
            self._enrich_details(anime.id)

    def _enrich_details(self, anime_id: int):
        """Fetch the full record (description, genres, year, relations) and refresh the open details page."""
        worker = SingleAnimeWorker(anime_id)

        def on_done(media):
            if not media:
                return
            _ANIME_META_CACHE[media.id] = media
            for view in (self.upcoming_view, self.details_view):
                current = getattr(view, "anime", None)
                if current is not None and current.id == media.id and self.stack.currentWidget() is view:
                    view.set_anime(media)

        worker.data_ready.connect(on_done)
        run_worker(worker)

    def load_home(self):
        self.search_input.clear()
        self.hero_banner.setVisible(True)
        self.trending_shelf.setVisible(True)
        self.new_releases_shelf.setVisible(True)
        self.finished_shelf.setVisible(True)
        self.upcoming_shelf.setVisible(True)

        self._refresh_history_shelf()
        self.stack.setCurrentIndex(0)
        self.fetch_home_content()

    def trigger_search(self):
        query = self.search_input.text().strip()
        if not query:
            self.load_home()
            return

        self.spotlight_timer.stop()
        self.hover_preview.hide()
        self.progress.setVisible(False)
        self.stack.setCurrentWidget(self.search_view)
        self.search_view.execute_search(query)

    def fetch_home_content(self):
        self.progress.setVisible(True)
        worker = HomeShelvesWorker()
        worker.data_ready.connect(self.on_home_loaded)
        worker.error_occurred.connect(self.on_error)
        run_worker(worker)

    def on_home_loaded(self, shelves: dict):
        self.progress.setVisible(False)
        self.spotlight_items = shelves.get("spotlight", [])
        self.spotlight_index = 0

        self.update_spotlight(forward=True)

        if len(self.spotlight_items) > 1:
            run_worker(SpotlightPreviewWorker(self.spotlight_items))
            self.spotlight_timer.start()

        # Cache metadata
        for s_list in shelves.values():
            for m in s_list:
                _ANIME_META_CACHE[m.id] = m

        self.trending_shelf.set_items(shelves.get("trending", []), base_priority=400)
        self.new_releases_shelf.set_items(shelves.get("new_releases", []), base_priority=300)
        self.finished_shelf.set_items(shelves.get("just_finished", []), base_priority=200)
        self.upcoming_shelf.set_items(shelves.get("upcoming", []), base_priority=100)

    def update_spotlight(self, forward: bool = True):
        if self.spotlight_items:
            anime = self.spotlight_items[self.spotlight_index]
            self.hero_banner.set_data(anime, rank=self.spotlight_index + 1, forward=forward)

    def prev_spotlight(self):
        if self.spotlight_items:
            self.spotlight_index = (self.spotlight_index - 1) % len(self.spotlight_items)
            self.update_spotlight(forward=False)
            self.spotlight_timer.start()

    def next_spotlight(self):
        if self.spotlight_items:
            self.spotlight_index = (self.spotlight_index + 1) % len(self.spotlight_items)
            self.update_spotlight(forward=True)
            self.spotlight_timer.start()

    def on_spotlight_clicked(self):
        if self.spotlight_items:
            self.open_details_view(self.spotlight_items[self.spotlight_index])

    def on_spotlight_watch(self):
        if not self.spotlight_items:
            return
        anime = self.spotlight_items[self.spotlight_index]
        ep = history_mgr.get_next_episode(anime.id, anime.episodes or 1)
        self.play_anime_episode(anime, ep)

    def on_error(self, msg: str):
        self.progress.setVisible(False)
        QMessageBox.warning(self, "Network Notice", f"Failed to load content:\n{msg}")
