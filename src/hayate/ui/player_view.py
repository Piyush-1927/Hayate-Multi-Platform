from hayate.core.assets import ic as _ic
import threading
from PyQt6 import QtCore, QtWidgets, QtGui
from PyQt6.QtCore import Qt, QUrl, pyqtSignal, QPoint, QTimer
from PyQt6.QtWidgets import (
    QApplication,
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QLabel, QFrame, QScrollArea, QListWidget, QListWidgetItem,
    QLineEdit, QComboBox, QSlider, QMessageBox, QProgressBar
)
from PyQt6.QtMultimedia import QMediaPlayer
from hayate.core.playback_platform import detect_playback_platform
from hayate.core.mpv_adapter import MpvPlayerAdapter
from hayate.ui.gl_video_surface import GLVideoSurface

from hayate.core.anilist import AnimeMedia
from hayate.core.player import PlayerController
from hayate.core.history import history_mgr


QUALITY_OPTIONS = (
    ("Best Available (Auto)", "best"),
    ("1080p (FHD)", "1080"),
    ("720p (HD Balanced)", "720"),
    ("480p (SD)", "480"),
    ("Data Saver (Lowest)", "worst"),
)


def format_time(ms: int) -> str:
    seconds = max(0, ms // 1000)
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


class _NativeVideoSurface(QWidget):
    """Dedicated native child window used by libmpv for video output."""

    doubleClickedCenter = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(
            Qt.WidgetAttribute.WA_DontCreateNativeAncestors, True
        )
        self.setAttribute(Qt.WidgetAttribute.WA_NativeWindow, True)
        self.setMouseTracking(True)
        self.setStyleSheet("background-color: #000000;")

    def mouseDoubleClickEvent(self, event: QtGui.QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            center = QPoint(self.width() // 2, self.height() // 2)
            dist_sq = (
                (event.position().x() - center.x()) ** 2
                + (event.position().y() - center.y()) ** 2
            )
            if dist_sq <= 160 ** 2:
                self.doubleClickedCenter.emit()
                event.accept()
                return
        super().mouseDoubleClickEvent(event)


class VideoOverlayWidget(QWidget):
    """Video container with a stable native child surface for embedding."""

    doubleClickedCenter = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setStyleSheet("background-color: #000000;")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.native_surface = (
            GLVideoSurface(self)
            if detect_playback_platform().embedding == "gl"
            else _NativeVideoSurface(self)
        )
        self.native_surface.doubleClickedCenter.connect(
            self.doubleClickedCenter.emit
        )
        layout.addWidget(self.native_surface)


class FloatingControlBar(QtWidgets.QWidget):
    """Independent floating overlay bar for fullscreen mode."""
    def __init__(self, parent=None):
        super().__init__(parent, Qt.WindowType.ToolTip | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedHeight(64)
        self._build_ui()

    def _build_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.card = QFrame(self)
        self.card.setStyleSheet("""
            QFrame {
                background-color: rgba(23, 23, 36, 0.96);
                border: 1px solid #2d2d44;
                border-radius: 12px;
            }
            QPushButton {
                background-color: #1c1c2e;
                color: #e2e8f0;
                border: 1px solid #2d2d44;
                border-radius: 8px;
                font-size: 14px;
                font-weight: 700;
            }
            QPushButton:hover {
                background-color: #27273c;
                border-color: #38bdf8;
                color: #38bdf8;
            }
            QSlider::groove:horizontal {
                height: 5px;
                background: #3d3d5c;
                border-radius: 2px;
            }
            QSlider::sub-page:horizontal {
                background: #38bdf8;
                border-radius: 2px;
            }
            QSlider::handle:horizontal {
                background: #ffffff;
                width: 12px;
                height: 12px;
                margin-top: -4px;
                margin-bottom: -4px;
                border-radius: 6px;
            }
        """)

        card_layout = QHBoxLayout(self.card)
        card_layout.setContentsMargins(16, 8, 16, 8)
        card_layout.setSpacing(12)

        self.btn_backward = _ic(QPushButton(""), "rewind")
        self.btn_backward.setToolTip("Seek -10s")
        self.btn_backward.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_backward.setFixedSize(34, 34)
        card_layout.addWidget(self.btn_backward)

        self.btn_play = _ic(QPushButton(""), "play")
        self.btn_play.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_play.setFixedSize(36, 36)
        card_layout.addWidget(self.btn_play)

        self.btn_forward = _ic(QPushButton(""), "fast-forward")
        self.btn_forward.setToolTip("Seek +10s")
        self.btn_forward.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_forward.setFixedSize(34, 34)
        card_layout.addWidget(self.btn_forward)

        self.lbl_time = QLabel("00:00 / 00:00")
        self.lbl_time.setMinimumWidth(110)
        self.lbl_time.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_time.setStyleSheet("color: #94a3b8; font-size: 12px; font-weight: 600; border: none; background: transparent;")
        card_layout.addWidget(self.lbl_time)

        self.seek_slider = QSlider(Qt.Orientation.Horizontal)
        self.seek_slider.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.seek_slider.setRange(0, 0)
        card_layout.addWidget(self.seek_slider, 1)

        self.btn_mute = _ic(QPushButton(""), "volume-2")
        self.btn_mute.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_mute.setFixedSize(36, 36)
        card_layout.addWidget(self.btn_mute)

        self.vol_slider = QSlider(Qt.Orientation.Horizontal)
        self.vol_slider.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.vol_slider.setFixedWidth(90)
        self.vol_slider.setRange(0, 100)
        self.vol_slider.setValue(100)
        card_layout.addWidget(self.vol_slider)

        self.btn_settings = _ic(QPushButton(""), "settings")
        self.btn_settings.setToolTip("Playback speed & captions")
        self.btn_settings.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_settings.setFixedSize(36, 36)
        card_layout.addWidget(self.btn_settings)

        self.btn_fullscreen = _ic(QPushButton(""), "maximize")
        self.btn_fullscreen.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_fullscreen.setFixedSize(36, 36)
        card_layout.addWidget(self.btn_fullscreen)

        layout.addWidget(self.card)


def _skip_safe(fn):
    import functools, traceback

    @functools.wraps(fn)
    def w(*a, **k):
        try:
            return fn(*a, **k)
        except Exception:
            traceback.print_exc()
    return w


class PlayerView(QWidget):
    back_requested = pyqtSignal()
    _stream_resolved = pyqtSignal(object, int, str)
    _stream_failed = pyqtSignal(str)

    def __init__(self, player_ctrl: PlayerController, parent=None):
        super().__init__(parent)
        self.player_ctrl = player_ctrl
        self.anime: AnimeMedia | None = None
        self.active_ep: int = 1
        self.current_chunk: int = 0
        self.chunk_buttons: list[QPushButton] = []
        self.is_slider_pressed = False

        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        # Playback engine is initialized after the native video surface exists.
        self.audio_output = None
        self.media_player = None
        self._playback_platform = None
        self._playback_error = None
        self._playback_shutdown = False

        # Floating bar for Fullscreen Mode
        self.floating_bar = FloatingControlBar(self)
        self.floating_bar.hide()
        from PyQt6.QtGui import QGuiApplication
        QGuiApplication.instance().applicationStateChanged.connect(
            lambda st: st != Qt.ApplicationState.ApplicationActive and self._hide_floating_bar())
        self.is_bar_visible = False
        self._last_cursor_pos = QPoint()

        # Timers
        self.poll_timer = QTimer(self)
        self.poll_timer.setInterval(40)
        self.poll_timer.timeout.connect(self._check_mouse_threshold)

        self.hide_timer = QTimer(self)
        self.hide_timer.setInterval(2200)
        self.hide_timer.timeout.connect(self._hide_floating_bar)

        self.cursor_idle_timer = QTimer(self)
        self.cursor_idle_timer.setInterval(2000)
        self.cursor_idle_timer.timeout.connect(self._hide_cursor_if_idle)

        self._stream_resolved.connect(self._on_stream_ready)
        self._stream_failed.connect(self._on_stream_error)

        self._build_ui()
        self._initialize_playback()
        self._setup_shortcuts()
        self._connect_signals()
        self._build_skip_btn()

    def _initialize_playback(self):
        """Detect the desktop backend once and initialize embedded libmpv."""
        try:
            self._playback_platform = detect_playback_platform()

            if not self._playback_platform.supported:
                raise RuntimeError(
                    self._playback_platform.reason
                    or "Native video embedding is not supported on this session."
                )

            # Force creation of the dedicated native child window before mpv starts.
            surface = self.video_widget.native_surface
            is_gl = self._playback_platform.embedding == "gl"
            if is_gl:
                native_id = 0
            else:
                surface.setAttribute(Qt.WidgetAttribute.WA_NativeWindow, True)
                native_id = int(surface.winId())

            self.media_player = MpvPlayerAdapter(
                platform=self._playback_platform,
                native_id=native_id,
                parent=self,
                gl_widget=surface if is_gl else None,
            )
            self.audio_output = self.media_player.audio_output

        except Exception as exc:
            self.media_player = None
            self.audio_output = None
            self._playback_error = str(exc)
            self.placeholder_label.setText(
                "Embedded playback could not initialize.\n"
                + self._playback_error
            )
            self.placeholder_label.show()

    def _build_ui(self):
        self.root_layout = QVBoxLayout(self)
        self.root_layout.setContentsMargins(18, 14, 18, 14)
        self.root_layout.setSpacing(12)

        # Top Bar
        self.top_bar_widget = QWidget()
        top_bar = QHBoxLayout(self.top_bar_widget)
        top_bar.setContentsMargins(0, 0, 0, 0)
        top_bar.setSpacing(14)

        self.btn_back = _ic(QPushButton("Back to Details"), "arrow-left")
        self.btn_back.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_back.setFixedHeight(34)
        self.btn_back.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_back.setStyleSheet("""
            QPushButton {
                background-color: #212130;
                color: #e2e8f0;
                border: 1px solid #3d3d52;
                border-radius: 7px;
                padding: 0 14px;
                font-weight: 700;
                font-size: 12px;
            }
            QPushButton:hover { background-color: #2d2d44; color: #38bdf8; }
        """)
        self.btn_back.clicked.connect(self._handle_back)
        top_bar.addWidget(self.btn_back)

        self.title_lbl = QLabel("")
        self.title_lbl.setStyleSheet("color: #ffffff; font-size: 15px; font-weight: 700; padding-left: 8px;")
        top_bar.addWidget(self.title_lbl)
        top_bar.addStretch()

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setFixedSize(140, 4)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setVisible(False)
        top_bar.addWidget(self.progress_bar)

        self.root_layout.addWidget(self.top_bar_widget)

        # Main Body Layout
        self.body_layout = QHBoxLayout()
        self.body_layout.setSpacing(14)

        # Left Sidebar (Episodes)
        self.sidebar = QFrame()
        self.sidebar.setFixedWidth(320)
        self.sidebar.setStyleSheet("""
            QFrame {
                background-color: #171724;
                border: 1px solid #26263a;
                border-radius: 12px;
            }
        """)
        s_layout = QVBoxLayout(self.sidebar)
        s_layout.setContentsMargins(12, 12, 12, 12)
        s_layout.setSpacing(8)

        self.side_title = QLabel("Episodes")
        self.side_title.setStyleSheet("color: #ffffff; font-weight: 700; font-size: 14px; border: none; background: transparent;")
        s_layout.addWidget(self.side_title)

        filter_bar = QHBoxLayout()
        self.jump_input = QLineEdit()
        self.jump_input.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.jump_input.setPlaceholderText("Jump #...")
        self.jump_input.setFixedHeight(30); self.jump_input.setMinimumWidth(70)
        self.jump_input.setStyleSheet("""
            QLineEdit {
                background-color: #242436;
                color: #ffffff;
                border: 1px solid #3d3d52;
                border-radius: 6px;
                padding: 0 6px;
                font-size: 12px;
            }
        """)
        self.jump_input.returnPressed.connect(self._on_jump)
        filter_bar.addWidget(self.jump_input, 1)

        self.audio_combo = QComboBox()
        self.audio_combo.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.audio_combo.setFixedHeight(30)
        self.audio_combo.setStyleSheet("""
            QComboBox {
                background-color: #212130;
                color: #e2e8f0;
                border: 1px solid #3d3d52;
                border-radius: 6px;
                padding: 0 8px;
                font-size: 11px;
                font-weight: 700;
            }
        """)
        from hayate.core.settings import audio_options
        for _l, _d in audio_options():
            self.audio_combo.addItem(_l, _d)
        self.audio_combo.currentIndexChanged.connect(self._on_audio_changed)
        filter_bar.addWidget(self.audio_combo)

        self.quality_combo = QComboBox()
        self.quality_combo.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.quality_combo.setFixedHeight(30)
        from hayate.core.theme import _tracked as _tr
        self.quality_combo.setStyleSheet(_tr.get(self.audio_combo) or self.audio_combo.styleSheet())
        for _label, _val in QUALITY_OPTIONS:
            self.quality_combo.addItem(_label, _val)
        _q = self._settings().get("playback_quality", "best")
        self.quality_combo.setCurrentIndex(max(self.quality_combo.findData(_q), 0))
        self.quality_combo.currentIndexChanged.connect(self._on_quality_changed)
        self.quality_combo.setParent(self); self.quality_combo.hide()
        s_layout.addLayout(filter_bar)

        self.chunk_scroll = QScrollArea()
        self.chunk_scroll.setFixedHeight(34)
        self.chunk_scroll.setWidgetResizable(True)
        self.chunk_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.chunk_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.chunk_scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")

        self.chunk_widget = QWidget()
        self.chunk_layout = QHBoxLayout(self.chunk_widget)
        self.chunk_layout.setContentsMargins(0, 0, 0, 0)
        self.chunk_layout.setSpacing(6)
        self.chunk_scroll.setWidget(self.chunk_widget)
        s_layout.addWidget(self.chunk_scroll)

        self.ep_list = QListWidget()
        self.ep_list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.ep_list.setStyleSheet("""
            QListWidget {
                background-color: transparent;
                border: none;
                color: #cbd5e1;
                font-size: 13px;
            }
            QListWidget::item {
                padding: 9px 12px;
                border-radius: 6px;
                margin-bottom: 4px;
                background-color: #1e1e2d;
            }
            QListWidget::item:hover { background-color: #25253b; color: #38bdf8; }
            QListWidget::item:selected { background-color: #38bdf8; color: #090a0f; font-weight: 700; }
        """)
        self.ep_list.itemClicked.connect(self._on_ep_clicked)
        s_layout.addWidget(self.ep_list)

        self.body_layout.addWidget(self.sidebar)

        # Right Column (Video + Docked Controls)
        self.right_column = QVBoxLayout()
        self.right_column.setSpacing(10)

        self.card_viewport = QFrame()
        self.card_viewport.setStyleSheet("""
            QFrame {
                background-color: #000000;
                border: 1px solid #26263a;
                border-radius: 12px;
            }
        """)
        v_layout = QVBoxLayout(self.card_viewport)
        v_layout.setContentsMargins(0, 0, 0, 0)
        v_layout.setSpacing(0)

        self.video_widget = VideoOverlayWidget(self.card_viewport)
        self.video_widget.doubleClickedCenter.connect(self._toggle_fullscreen)
        v_layout.addWidget(self.video_widget)

        self.placeholder_label = QLabel("Select an episode to begin playback.", self.card_viewport)
        self.placeholder_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.placeholder_label.setStyleSheet("color: #64748b; font-size: 14px; font-weight: 600; border: none; background: transparent;")
        v_layout.addWidget(self.placeholder_label)

        self.right_column.addWidget(self.card_viewport, 1)

        # Docked Control Capsule
        self.docked_card = QFrame()
        self.docked_card.setFixedHeight(56)
        self.docked_card.setStyleSheet("""
            QFrame {
                background-color: rgba(23, 23, 36, 0.96);
                border: 1px solid #2d2d44;
                border-radius: 12px;
            }
            QPushButton {
                background-color: #1c1c2e;
                color: #e2e8f0;
                border: 1px solid #2d2d44;
                border-radius: 8px;
                font-size: 14px;
                font-weight: 700;
            }
            QPushButton:hover {
                background-color: #27273c;
                border-color: #38bdf8;
                color: #38bdf8;
            }
            QSlider::groove:horizontal {
                height: 5px;
                background: #3d3d5c;
                border-radius: 2px;
            }
            QSlider::sub-page:horizontal {
                background: #38bdf8;
                border-radius: 2px;
            }
            QSlider::handle:horizontal {
                background: #ffffff;
                width: 12px;
                height: 12px;
                margin-top: -4px;
                margin-bottom: -4px;
                border-radius: 6px;
            }
        """)

        card_layout = QHBoxLayout(self.docked_card)
        card_layout.setContentsMargins(14, 6, 14, 6)
        card_layout.setSpacing(10)

        self.btn_backward_docked = _ic(QPushButton(""), "rewind")
        self.btn_backward_docked.setToolTip("Seek -10s")
        self.btn_backward_docked.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_backward_docked.setFixedSize(34, 34)
        card_layout.addWidget(self.btn_backward_docked)

        self.btn_play_docked = _ic(QPushButton(""), "play")
        self.btn_play_docked.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_play_docked.setFixedSize(36, 36)
        card_layout.addWidget(self.btn_play_docked)

        self.btn_forward_docked = _ic(QPushButton(""), "fast-forward")
        self.btn_forward_docked.setToolTip("Seek +10s")
        self.btn_forward_docked.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_forward_docked.setFixedSize(34, 34)
        card_layout.addWidget(self.btn_forward_docked)

        self.lbl_time_docked = QLabel("00:00 / 00:00")
        self.lbl_time_docked.setMinimumWidth(110)
        self.lbl_time_docked.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_time_docked.setStyleSheet("color: #94a3b8; font-size: 12px; font-weight: 600; border: none; background: transparent;")
        card_layout.addWidget(self.lbl_time_docked)

        self.seek_slider_docked = QSlider(Qt.Orientation.Horizontal)
        self.seek_slider_docked.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.seek_slider_docked.setRange(0, 0)
        self.seek_slider_docked.sliderMoved.connect(self._on_seek_moved)
        self.seek_slider_docked.sliderPressed.connect(self._on_seek_pressed)
        self.seek_slider_docked.sliderReleased.connect(self._on_seek_released)
        card_layout.addWidget(self.seek_slider_docked, 1)

        self.btn_mute_docked = _ic(QPushButton(""), "volume-2")
        self.btn_mute_docked.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_mute_docked.setFixedSize(36, 36)
        card_layout.addWidget(self.btn_mute_docked)

        self.vol_slider_docked = QSlider(Qt.Orientation.Horizontal)
        self.vol_slider_docked.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.vol_slider_docked.setFixedWidth(80)
        self.vol_slider_docked.setRange(0, 100)
        self.vol_slider_docked.setValue(100)
        self.vol_slider_docked.valueChanged.connect(self._on_volume_changed)
        card_layout.addWidget(self.vol_slider_docked)

        self.btn_settings_docked = _ic(QPushButton(""), "settings")
        self.btn_settings_docked.setToolTip("Playback speed & captions")
        self.btn_settings_docked.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_settings_docked.setFixedSize(36, 36)
        card_layout.addWidget(self.btn_settings_docked)

        self.btn_comments_docked = QPushButton("💬")
        self.btn_comments_docked.setToolTip("Comments (C)")
        self.btn_comments_docked.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_comments_docked.setFixedSize(36, 36)
        self.btn_comments_docked.clicked.connect(self._toggle_comments)
        card_layout.addWidget(self.btn_comments_docked)

        self.btn_fullscreen_docked = _ic(QPushButton(""), "maximize")
        self.btn_fullscreen_docked.setToolTip("Toggle Fullscreen")
        self.btn_fullscreen_docked.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_fullscreen_docked.setFixedSize(36, 36)
        card_layout.addWidget(self.btn_fullscreen_docked)

        self.right_column.addWidget(self.docked_card)
        self.body_layout.addLayout(self.right_column, stretch=1)
        self.root_layout.addLayout(self.body_layout, stretch=1)

    def _setup_shortcuts(self):
        ctx = Qt.ShortcutContext.WidgetWithChildrenShortcut
        QtGui.QShortcut(QtGui.QKeySequence(Qt.Key.Key_Space), self, self._toggle_play, context=ctx).setAutoRepeat(False)
        QtGui.QShortcut(QtGui.QKeySequence(Qt.Key.Key_F), self, self._on_f_key, context=ctx).setAutoRepeat(False)
        QtGui.QShortcut(QtGui.QKeySequence(Qt.Key.Key_F11), self, self._toggle_fullscreen, context=ctx).setAutoRepeat(False)
        _cs = QtGui.QShortcut(QtGui.QKeySequence(Qt.Key.Key_C), self,
                             lambda: self.isVisible() and self._toggle_comments(),
                             context=Qt.ShortcutContext.ApplicationShortcut)
        _cs.setAutoRepeat(False)
        QtGui.QShortcut(QtGui.QKeySequence(Qt.Key.Key_Escape), self, self._on_esc_key, context=ctx)
        QtGui.QShortcut(QtGui.QKeySequence(Qt.Key.Key_Left), self, lambda: self._seek_relative(-5000), context=ctx)
        QtGui.QShortcut(QtGui.QKeySequence(Qt.Key.Key_Right), self, lambda: self._seek_relative(5000), context=ctx)
        QtGui.QShortcut(QtGui.QKeySequence(Qt.Key.Key_Up), self, lambda: self._adjust_volume(5), context=ctx)
        QtGui.QShortcut(QtGui.QKeySequence(Qt.Key.Key_Down), self, lambda: self._adjust_volume(-5), context=ctx)

    _mc_owner = None  # the one view that currently holds the process-wide MPRIS object

    def _release_other_media_controls(self):
        prev = PlayerView._mc_owner
        if prev is not None and prev is not self:
            try:
                prev._shutdown_media_controls()
            except Exception:
                pass
        PlayerView._mc_owner = self

    def _ensure_media_controls(self):
        mc = getattr(self, "_mc", None)
        if self.media_player is not None and (mc is None or not getattr(mc, "ok", True)):
            self._init_media_controls(self.media_player)

    def _init_media_controls(self, player):
        from hayate.ui import media_controls
        mc0 = getattr(self, "_mc", None)
        if mc0 is not None and not getattr(mc0, "ok", True):
            self._mc = mc0 = None
        if mc0 is None:
            self._release_other_media_controls()
            def seek_abs(us):
                if self.media_player is not None:
                    self.media_player.setPosition(max(0, int(us / 1000)))

            def set_vol(v):
                self.vol_slider_docked.setValue(int(round(min(1.0, v) * 100)))

            def step(d):
                if not self.anime:
                    return
                ep = self.active_ep + d
                if 1 <= ep <= max(1, self.anime.episodes):
                    self.play_episode(ep)

            self._mc = media_controls.create({
                "play": lambda: self.media_player and self.media_player.play(),
                "pause": lambda: self.media_player and self.media_player.pause(),
                "toggle": self._toggle_play,
                "stop": lambda: self.media_player and self.media_player.pause(),
                "next": lambda: step(1),
                "prev": lambda: step(-1),
                "seek_rel": lambda us: self._seek_relative(int(us / 1000)),
                "seek_abs": seek_abs,
                "volume": set_vol,
            }, position_fn=lambda: (self.media_player.position() * 1000) if self.media_player else 0,
                name="hayate", parent=self)
            QApplication.instance().aboutToQuit.connect(self._shutdown_media_controls)

        P = QMediaPlayer.PlaybackState

        def sync_meta(length_ms=None):
            mc = getattr(self, "_mc", None)
            if not mc or not self.anime:
                return
            total = max(1, self.anime.episodes)
            mc.update(title=f"{self.anime.title} \u2014 EP {self.active_ep}",
                      length_us=None if length_ms is None else int(length_ms) * 1000,
                      can_next=self.active_ep < total, can_prev=self.active_ep > 1)

        def on_state(st):
            mc = getattr(self, "_mc", None)
            if mc:
                mc.update(status="Playing" if st == P.PlayingState
                          else "Paused" if st == P.PausedState else "Stopped")

        player.playbackStateChanged.connect(on_state)
        player.durationChanged.connect(sync_meta)

    def _shutdown_media_controls(self):
        mc = getattr(self, "_mc", None)
        if mc:
            self._mc = None
            mc.shutdown()

    def _connect_player_signals(self):
        """Connect signals belonging to the current playback adapter."""
        player = self.media_player
        if player is None:
            return
        player.positionChanged.connect(self._on_position_changed)
        player.durationChanged.connect(self._on_duration_changed)
        player.playbackStateChanged.connect(self._on_playback_state_changed)
        player.endReached.connect(self._on_episode_end)
        player.errorOccurred.connect(self._on_stream_error)
        self._init_media_controls(player)

    def _connect_signals(self):
        self._connect_player_signals()

        # Floating bar signals
        self.floating_bar.btn_backward.clicked.connect(lambda: self._seek_relative(-10000))
        self.floating_bar.btn_forward.clicked.connect(lambda: self._seek_relative(10000))
        self.floating_bar.btn_play.clicked.connect(self._toggle_play)
        self.floating_bar.btn_mute.clicked.connect(self._toggle_mute)
        self.floating_bar.btn_fullscreen.clicked.connect(self._toggle_fullscreen)
        self.floating_bar.btn_settings.clicked.connect(lambda: self._open_player_menu(self.floating_bar.btn_settings))

        # Docked bar buttons
        self.btn_backward_docked.clicked.connect(lambda: self._seek_relative(-10000))
        self.btn_forward_docked.clicked.connect(lambda: self._seek_relative(10000))
        self.btn_play_docked.clicked.connect(self._toggle_play)
        self.btn_mute_docked.clicked.connect(self._toggle_mute)
        self.btn_fullscreen_docked.clicked.connect(self._toggle_fullscreen)
        self.btn_settings_docked.clicked.connect(lambda: self._open_player_menu(self.btn_settings_docked))
        self.floating_bar.seek_slider.sliderMoved.connect(self._on_seek_moved)
        self.floating_bar.seek_slider.sliderPressed.connect(self._on_seek_pressed)
        self.floating_bar.seek_slider.sliderReleased.connect(self._on_seek_released)
        self.floating_bar.vol_slider.valueChanged.connect(self._on_volume_changed)

    def _adjust_volume(self, delta: int):
        val = max(0, min(100, self.vol_slider_docked.value() + delta))
        self.vol_slider_docked.setValue(val)

    def _on_f_key(self):
        if self._web_active() and self.window().isFullScreen():
            self._web.press_f()
        else:
            self._toggle_fullscreen()

    def _on_esc_key(self):
        if self._web_active() and self._web.dom_fs:
            self._web.exit_page_fullscreen()
        else:
            self._exit_fullscreen_only()

    def _exit_fullscreen_only(self):
        w = self.window()
        if w.isFullScreen():
            self._toggle_fullscreen()

    def _hide_cursor_if_idle(self):
        w = self.window()
        if w.isFullScreen() and not self.is_bar_visible:
            self.setCursor(Qt.CursorShape.BlankCursor)

    def _check_mouse_threshold(self):
        w = self.window()
        if not w.isFullScreen():
            return

        c_pos = QtGui.QCursor.pos()
        if c_pos != self._last_cursor_pos:
            self._last_cursor_pos = c_pos
            self.unsetCursor()
            self.cursor_idle_timer.start()

        screen = QtGui.QGuiApplication.screenAt(c_pos) or self.screen()
        if not screen:
            return

        geom = screen.geometry()
        threshold_y = geom.bottom() - 180

        if c_pos.y() >= threshold_y:
            self._show_floating_bar(geom)
        else:
            if self.is_bar_visible:
                if not self.floating_bar.geometry().contains(c_pos):
                    if not self.hide_timer.isActive():
                        self.hide_timer.start()

    def _toggle_comments(self):
        from hayate.ui.comments_window import CommentsLauncher as CommentsCard
        c = getattr(self, "_comments_card", None)
        if c is None:
            c = self._comments_card = CommentsCard(
                self.video_widget, detect_playback_platform().embedding == "gl")
        c.set_media(self.anime.id if self.anime else None, getattr(self, "active_ep", None))
        c.toggle()

    def _cmt_open(self):
        c = getattr(self, "_comments_card", None)
        return bool(c and c._alive())

    def _show_floating_bar(self, screen_geom):
        if self._web_active():
            return
        w = self.window()
        from PyQt6.QtGui import QGuiApplication
        if (not w.isFullScreen() or self.is_bar_visible
                or (QGuiApplication.applicationState() != Qt.ApplicationState.ApplicationActive and not self._cmt_open())):
            return
        self.is_bar_visible = True
        bar_w = min(1500, int(screen_geom.width() * 0.85))
        bar_h = 64
        x = screen_geom.x() + (screen_geom.width() - bar_w) // 2
        y = screen_geom.bottom() - bar_h - 24

        self.floating_bar.setGeometry(x, y, bar_w, bar_h)
        self.floating_bar.show()
        self.floating_bar.raise_()
        self.hide_timer.start()

    def _hide_floating_bar(self):
        from PyQt6 import sip
        if sip.isdeleted(self):
            return
        from PyQt6.QtGui import QCursor, QGuiApplication
        if (self.is_bar_visible
                and (QGuiApplication.applicationState() == Qt.ApplicationState.ApplicationActive or self._cmt_open())
                and self.floating_bar.geometry().contains(QCursor.pos())):
            self.hide_timer.start()  # cursor is on the pill: keep it
            return
        w = self.window()
        if not w.isFullScreen() or not self.is_bar_visible:
            return
        self.is_bar_visible = False
        self.floating_bar.hide()

    def _open_player_menu(self, btn):
        from PyQt6.QtWidgets import QMenu
        from PyQt6.QtGui import QAction, QActionGroup
        from PyQt6.QtCore import QPoint
        mp = self.media_player
        menu = QMenu(self)
        menu.setStyleSheet(
            "QMenu{background:#1e1e2d;color:#e2e8f0;border:1px solid #3d3d52;padding:4px;}"
            "QMenu::item{padding:6px 18px;}QMenu::item:selected{background:#2d2d42;}"
            "QMenu::item:disabled{color:#64748b;}")
        qm = menu.addMenu("Quality")
        for _i in range(self.quality_combo.count()):
            _a = qm.addAction(self.quality_combo.itemText(_i)); _a.setCheckable(True)
            _a.setChecked(_i == self.quality_combo.currentIndex())
            _a.triggered.connect(lambda _c, i=_i: self.quality_combo.setCurrentIndex(i))
        menu.addSeparator()

        cur = round(float(getattr(mp, "speed", lambda: 1.0)()), 2)
        sp = menu.addMenu("Playback speed")
        grp = QActionGroup(sp)
        for r in (0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0):
            a = QAction("Normal" if r == 1.0 else f"{r}x", sp, checkable=True)
            a.setChecked(abs(r - cur) < 0.01)
            a.triggered.connect(lambda _=False, r=r: mp.setSpeed(r))
            grp.addAction(a)
            sp.addAction(a)

        cap = menu.addMenu("Captions")
        tracks = getattr(mp, "subtitleTracks", lambda: [])()
        if not tracks:
            cap.addAction("None available").setEnabled(False)
        else:
            cg = QActionGroup(cap)
            off = QAction("Off", cap, checkable=True)
            off.setChecked(not any(t.get("selected") for t in tracks))
            off.triggered.connect(lambda _=False: mp.setSubtitleTrack(None))
            cg.addAction(off)
            cap.addAction(off)
            for t in tracks:
                name = t.get("title") or t.get("lang") or f"Track {t.get('id')}"
                a = QAction(str(name), cap, checkable=True)
                a.setChecked(bool(t.get("selected")))
                a.triggered.connect(lambda _=False, i=t.get("id"): mp.setSubtitleTrack(i))
                cg.addAction(a)
                cap.addAction(a)

        menu.exec(btn.mapToGlobal(QPoint(0, -menu.sizeHint().height())))

    def _toggle_fullscreen(self):
        w = self.window()
        if w.isFullScreen():
            self.poll_timer.stop()
            self.hide_timer.stop()
            self.cursor_idle_timer.stop()
            self.unsetCursor()
            self.is_bar_visible = False
            self.floating_bar.hide()

            self.top_bar_widget.setVisible(True)
            self.sidebar.setVisible(True)
            self.docked_card.setVisible(not self._web_active())
            self.root_layout.setContentsMargins(18, 14, 18, 14)
            self.card_viewport.setStyleSheet("""
                QFrame {
                    background-color: #000000;
                    border: 1px solid #26263a;
                    border-radius: 12px;
                }
            """)
            w.showNormal()
            if hasattr(w, "restored_geom"):
                w.setGeometry(w.restored_geom)
        else:
            w.restored_geom = w.geometry()
            self.top_bar_widget.setVisible(False)
            self.sidebar.setVisible(False)
            self.docked_card.setVisible(False)
            self.root_layout.setContentsMargins(0, 0, 0, 0)
            self.card_viewport.setStyleSheet("QFrame { background-color: #000000; border: none; border-radius: 0px; }")
            w.showFullScreen()

            self.poll_timer.start()
            self.cursor_idle_timer.start()

    def open_anime(self, anime: AnimeMedia, ep: int):
        if __import__("os").environ.get("HAYATE_MPV_TIMING") == "1":
            print(
                f"[Hayate timing] open_anime "
                f"view={id(self)} ep={ep} title={anime.title!r}",
                flush=True,
            )
        # Recreate libmpv after a previous session was shut down.
        if self._playback_shutdown:
            self._playback_shutdown = False
            self._playback_error = None
            self._initialize_playback()
            if self.media_player is not None:
                self._connect_player_signals()
            else:
                # Permit another attempt if initialization failed.
                self._playback_shutdown = True

        self.anime = anime
        self.active_ep = ep
        self.current_chunk = (ep - 1) // 100
        self.title_lbl.setText(f"{anime.title}  —  EP {ep}")
        self.side_title.setText(f"Episodes ({anime.episodes})")

        from hayate.core.settings import load_settings as _ls

        _st = _ls()

        self._reload_audio_items()
        pref_audio = _st.get("anime_audio_overrides", {}).get(str(anime.id)) or _st.get("preferred_audio", "sub")
        f_idx = self.audio_combo.findData(pref_audio)
        if f_idx >= 0:
            self.audio_combo.setCurrentIndex(f_idx)

        total_ep = max(1, anime.episodes)
        self._build_chunks(total_ep)
        self._load_chunk(self.current_chunk)
        self.play_episode(ep)
        self.setFocus()

    def _build_chunks(self, total_ep: int):
        while self.chunk_layout.count():
            item = self.chunk_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.chunk_buttons.clear()

        if total_ep <= 100:
            self.chunk_scroll.setVisible(False)
            return

        self.chunk_scroll.setVisible(True)
        chunks = (total_ep - 1) // 100 + 1
        for c in range(chunks):
            start = c * 100 + 1
            end = min((c + 1) * 100, total_ep)
            btn = QPushButton(f"{start}-{end}")
            btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            btn.setFixedHeight(24)
            btn.setStyleSheet("background-color: #242436; color: #94a3b8; border-radius: 4px; font-size: 11px; font-weight: 700;")
            btn.clicked.connect(lambda _, chunk=c: self._load_chunk(chunk))
            self.chunk_layout.addWidget(btn)
            self.chunk_buttons.append(btn)
        self.chunk_layout.addStretch()

    def _load_chunk(self, chunk_index: int):
        self.current_chunk = chunk_index
        total_ep = max(1, self.anime.episodes if self.anime else 1)
        self.ep_list.clear()

        start = chunk_index * 100 + 1
        end = min((chunk_index + 1) * 100, total_ep)

        for i in range(start, end + 1):
            item = QListWidgetItem(f"Episode {i}")
            item.setData(Qt.ItemDataRole.UserRole, i)
            self.ep_list.addItem(item)
            if i == self.active_ep:
                self.ep_list.setCurrentItem(item)
                self.ep_list.scrollToItem(item)

    def _on_ep_clicked(self, item: QListWidgetItem):
        ep = item.data(Qt.ItemDataRole.UserRole)
        if ep:
            self.play_episode(ep)

    def _on_jump(self):
        text = self.jump_input.text().strip()
        if not text.isdigit() or not self.anime:
            return
        ep = int(text)
        if 1 <= ep <= self.anime.episodes:
            c = (ep - 1) // 100
            if c != self.current_chunk:
                self._load_chunk(c)
            self.play_episode(ep)
        self.setFocus()

    def _reload_audio_items(self):
        from hayate.core.settings import audio_options
        cur = self.audio_combo.currentData()
        self.audio_combo.blockSignals(True)
        self.audio_combo.clear()
        for label, data in audio_options():
            self.audio_combo.addItem(label, data)
        self.audio_combo.setCurrentIndex(max(self.audio_combo.findData(cur), 0))
        self.audio_combo.blockSignals(False)

    def _on_audio_changed(self, idx: int):
        if not self.anime:
            return
        val = self.audio_combo.itemData(idx)
        from hayate.core.settings import load_settings, save_settings
        st = load_settings()
        st.setdefault("anime_audio_overrides", {})[str(self.anime.id)] = val
        save_settings(st)
        if getattr(self, "active_ep", None):
            self.play_episode(self.active_ep)  # apply the new audio now

    def _settings(self):
        from hayate.core.settings import load_settings
        return load_settings()

    def _sync_audio_from_settings(self):
        st = self._settings()
        val = st.get("anime_audio_overrides", {}).get(str(self.anime.id)) or st.get("preferred_audio", "sub")
        self.audio_combo.blockSignals(True)
        self.audio_combo.setCurrentIndex(max(self.audio_combo.findData(val), 0))
        self.audio_combo.blockSignals(False)

    def _on_quality_changed(self, idx: int):
        from hayate.core.settings import load_settings, save_settings
        st = load_settings()
        st["playback_quality"] = self.quality_combo.itemData(idx)
        save_settings(st)
        if self.anime and getattr(self, "active_ep", None):
            self.play_episode(self.active_ep)  # restarts the episode at the new quality

    def play_episode(self, ep: int):
        if not self.anime:
            return
        if __import__("os").environ.get("HAYATE_MPV_TIMING") == "1":
            print(
                f"[Hayate timing] play_episode "
                f"view={id(self)} ep={ep} title={self.anime.title!r}",
                flush=True,
            )
        if self.media_player is None:
            self._on_stream_error(
                self._playback_error or "The playback engine is unavailable."
            )
            return
        import time as _t
        _key, _now = (self.anime.id, ep), _t.monotonic()
        _last = getattr(self, "_last_play", None)
        if _last and _last[0] == _key and _now - _last[1] < 1.5:
            return  # duplicate request for the same episode (double signal)
        self._last_play = (_key, _now)
        self.active_ep = ep
        self.title_lbl.setText(f"{self.anime.title}  —  EP {ep}")
        self.progress_bar.setVisible(True)
        self.placeholder_label.setText(f"Loading Episode {ep}...")
        self._skip_segments, self._skipped, self._skip_kind = {}, set(), None
        self.skip_btn.hide()
        self.placeholder_label.show()

        for idx in range(self.ep_list.count()):
            item = self.ep_list.item(idx)
            if item.data(Qt.ItemDataRole.UserRole) == ep:
                self.ep_list.setCurrentItem(item)
                self.ep_list.scrollToItem(item)
                break

        anime = self.anime
        self._sync_audio_from_settings()
        dub_pref = self.audio_combo.currentData() or "sub"
        quality = self.quality_combo.currentData() or "best"

        def _resolve():
            provider = self.player_ctrl.active_provider()
            if not provider or getattr(provider, "name", "") == "pseudo":
                self._stream_failed.emit("No streaming provider installed. Please install or select a provider in Settings.")
                return
            try:
                stream = provider.get_stream(anime.title, ep, dub=dub_pref, quality=quality)
            except Exception as e:
                self._stream_failed.emit(f"Stream source failed: {e}")
                return
            if not stream or not stream.url:
                self._stream_failed.emit(getattr(provider, "last_notice", "") or f"Episode {ep} not available on this provider.")
                return
            if not hasattr(self, "_stream_by_url"):
                self._stream_by_url = {}
            self._stream_by_url[stream.url] = stream
            self._stream_resolved.emit(anime, ep, stream.url)

        threading.Thread(target=_resolve, daemon=True).start()

    def _on_stream_ready(self, anime, ep: int, url: str):
        self.progress_bar.setVisible(False)
        if url.startswith("web://"):
            self._show_web(url[6:])
            return
        self._hide_web()
        if self.media_player is None:
            self._on_stream_error(
                self._playback_error or "The playback engine is unavailable."
            )
            return

        self.placeholder_label.hide()
        st = getattr(self, "_stream_by_url", {}).get(url)
        self.media_player.setSource(
            QUrl(url),
            getattr(st, "referer", ""),
            getattr(st, "headers", None),
            getattr(st, "subtitles", ()),
        )
        pr = history_mgr.get_progress(anime.id, ep)
        if pr and hasattr(self.media_player, "setStartPosition"):
            self.media_player.setStartPosition(max(0.0, pr[0] - 3.0))
        self._prog_ctx = (anime.id, ep)
        self._aniskip.request(anime.id, ep)
        self._hook_progress()
        self.media_player.play()

        history_mgr.record_watch(
            anime_id=anime.id,
            title=anime.title,
            episode=ep,
            cover_url=anime.cover_url,
            banner_url=anime.banner_url,
            total_episodes=anime.episodes,
        )
        self._cloud_sync()

    def _show_web(self, target):
        from hayate.core import web_embed as W
        if not W.WEB_OK:
            self._on_stream_error("Web player unavailable (QtWebEngine missing).")
            return
        if not W.cdm_path():
            if not getattr(self, "_cdm_busy", False):
                self._cdm_busy, self._cdm_target = True, target
                self.placeholder_label.setText("Installing Widevine (one-time download)...")
                self.placeholder_label.show()
                W.install_cdm_async(self._cdm_installed)
            return
        try:
            pm = self.media_player
            if pm is not None and hasattr(pm, "pause"):
                pm.pause()
            if getattr(self, "_web", None) is None:
                from hayate.core.web_embed import WebPane
                self._web = WebPane(self)
                self.video_widget.layout().addWidget(self._web)
            self.video_widget.native_surface.hide()
            self.placeholder_label.hide()
            self._web.show()
            self._web.open(target, self.anime.title if self.anime else "", self.active_ep)
            self._web.setFocus()
            self._set_web_chrome(True)
        except Exception as exc:
            self._on_stream_error(f"Web player failed: {exc}")

    def _cdm_installed(self, path):
        self._cdm_busy = False
        if not path:
            self._on_stream_error("Widevine download failed. See widevine.log in the Hayate cache folder, then try again.")
            return
        from hayate.core import web_embed as W
        W.apply_cdm_flag(path)
        if W.engine_started():
            self._on_stream_error("Widevine installed. Restart Hayate once to enable it.")
            return
        self._show_web(self._cdm_target)

    def _web_active(self):
        w = getattr(self, "_web", None)
        return w is not None and w.isVisible()

    def _set_web_chrome(self, on):
        self.docked_card.setVisible(not on and not self.window().isFullScreen())
        if on:
            self.floating_bar.hide()
            self.is_bar_visible = False
        keep = {"F", "F11", "Esc"}
        for sc in self.findChildren(QtGui.QShortcut):
            if sc.key().toString() not in keep:
                sc.setEnabled(not on)

    def _hide_web(self):
        w = getattr(self, "_web", None)
        if w is not None and w.isVisible():
            w.open("about:blank")
            w.hide()
            self._set_web_chrome(False)
            self.video_widget.native_surface.show()

    def _on_episode_end(self):
        history_mgr.flush()
        self._cloud_sync()

    def _cloud_sync(self):
        try:
            from hayate.core import cloud
            cloud.autosync()
        except Exception:
            import logging
            logging.exception("cloud sync")

    def _hook_progress(self):
        if getattr(self, "_progress_hooked", False) or self.media_player is None:
            return
        self._progress_hooked = True
        self.media_player.positionChanged.connect(self._save_progress)
        self.media_player.playbackStateChanged.connect(lambda *_: history_mgr.flush())
        from PyQt6.QtCore import QCoreApplication
        app = QCoreApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(history_mgr.flush)

    def _save_progress(self, ms: int):
        ctx = getattr(self, "_prog_ctx", None)
        if not ctx or ms <= 0 or self.media_player is None:
            return
        history_mgr.set_progress(ctx[0], ctx[1], ms / 1000.0, self.media_player.duration() / 1000.0)

    # ---- AniSkip ---------------------------------------------------------
    def _build_skip_btn(self):
        from hayate.core.aniskip import AniSkip
        from hayate.core.theme import theme
        self._skip_segments, self._skipped, self._skip_kind = {}, set(), None
        self._aniskip = AniSkip(self)
        self._aniskip.ready.connect(self._on_skip_ready)
        self.skip_btn = QPushButton("Skip Intro", self)
        self.skip_btn.setFixedHeight(40)
        self.skip_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.skip_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._skip_in_video = detect_playback_platform().embedding == "gl"
        if self._skip_in_video:
            # GL video is a normal widget: overlay as a child, not a top-level window.
            self.skip_btn.setParent(self.video_widget)
        else:
            self.skip_btn.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
                                         | Qt.WindowType.WindowStaysOnTopHint
                                         | Qt.WindowType.WindowDoesNotAcceptFocus)
            self.skip_btn.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
            self.skip_btn.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.skip_btn.clicked.connect(self._do_skip)
        self.skip_btn.hide()
        self._style_skip_btn()
        theme.changed.connect(self._style_skip_btn)

    def _style_skip_btn(self, *_):
        from hayate.core.theme import theme
        try:
            fg, edge = theme.rgba("accent"), theme.rgba("accent", 0.7)
        except Exception:
            fg, edge = "#38bdf8", "rgba(56,189,248,180)"
        self.skip_btn.setStyleSheet(
            "QPushButton { background: rgba(12,12,18,150); color: %s; border: 1.5px solid %s;"
            " border-radius: 10px; padding: 0 20px; font-size: 13px; font-weight: 800; }"
            "QPushButton:hover { background: rgba(12,12,18,210); }" % (fg, edge))

    @_skip_safe
    def _on_skip_ready(self, anilist_id: int, ep: int, seg: dict):
        if self.anime and anilist_id == self.anime.id and ep == self.active_ep:
            self._skip_segments = seg

    @_skip_safe
    def _update_skip_btn(self, pos: int):
        kind = None
        for k in ("op", "ed"):
            seg = self._skip_segments.get(k)
            if seg and k not in self._skipped and seg[0] <= pos < seg[1] - 1000:
                kind = k
                break
        if kind is None:
            if self._skip_kind is not None:
                self._skip_kind = None
                self.skip_btn.hide()
            return
        if kind != self._skip_kind:
            self._skip_kind = kind
            self.skip_btn.setText("Skip Intro" if kind == "op" else "Skip Outro")
            self.skip_btn.adjustSize()
            self.skip_btn.setFixedHeight(40)
        vc = self.findChild(VideoOverlayWidget)
        if vc is not None:
            lift = 110 if self.window().isFullScreen() else 28
            if getattr(self, "_skip_in_video", False):
                self.skip_btn.move(vc.width() - self.skip_btn.width() - 32,
                                   vc.height() - self.skip_btn.height() - lift)
            else:
                br = vc.mapToGlobal(QPoint(vc.width(), vc.height()))
                self.skip_btn.move(br.x() - self.skip_btn.width() - 32,
                                   br.y() - self.skip_btn.height() - lift)
        if not self.skip_btn.isVisible():
            self.skip_btn.show()
            self.skip_btn.raise_()

    def hideEvent(self, event):
        c = getattr(self, "_comments_card", None)
        if c is not None:
            c.close_card()
        if hasattr(self, "skip_btn"):
            self._skip_kind = None
            self.skip_btn.hide()
        super().hideEvent(event)

    def _do_skip(self):
        seg = self._skip_segments.get(self._skip_kind)
        if seg and self.media_player is not None:
            self._skipped.add(self._skip_kind)
            self.media_player.setPosition(seg[1])
        self._skip_kind = None
        self.skip_btn.hide()

    def _on_stream_error(self, err_msg: str):
        self.progress_bar.setVisible(False)
        self.placeholder_label.setText(err_msg)
        self.placeholder_label.show()
        QMessageBox.warning(self, "Playback Notice", err_msg)

    def _toggle_play(self):
        if self.media_player is None:
            return
        if self.media_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.media_player.pause()
        else:
            self.media_player.play()

    def _seek_relative(self, delta_ms: int):
        if self.media_player is None:
            return
        seek_relative = getattr(self.media_player, "seekRelative", None)
        if callable(seek_relative):
            seek_relative(delta_ms)
            return
        new_pos = max(
            0,
            min(
                self.media_player.duration(),
                self.media_player.position() + delta_ms,
            ),
        )
        self.media_player.setPosition(new_pos)

    def _seek_pending(self, pos):
        """True while dragging or while a released seek hasn't landed yet."""
        if self.is_slider_pressed:
            return True
        tgt = getattr(self, "_seek_target", None)
        if tgt is None:
            return False
        from time import monotonic
        if abs(pos - tgt) <= 2500 or monotonic() - self._seek_t0 > 4.0:
            self._seek_target = None
            return False
        return True

    def _sync_seek_ui(self, pos):
        for sl in (self.seek_slider_docked, self.floating_bar.seek_slider):
            if sl.value() != pos:
                sl.blockSignals(True)
                sl.setValue(pos)
                sl.blockSignals(False)
        if self.media_player is not None:
            self._update_time_label(pos, self.media_player.duration())

    def _on_seek_moved(self, pos):
        self._sync_seek_ui(pos)
        # scrub-hook

    def _on_seek_pressed(self):
        self._seek_target = None
        self.is_slider_pressed = True

    def _on_seek_released(self):
        self.is_slider_pressed = False
        if self.media_player is None:
            return
        target_pos = self.seek_slider_docked.value()
        if self.is_bar_visible:
            target_pos = self.floating_bar.seek_slider.value()
        from time import monotonic
        self._seek_target, self._seek_t0 = target_pos, monotonic()
        st = getattr(self, "_scrub_timer", None)
        if st is not None:
            st.stop()
        self._sync_seek_ui(target_pos)
        self.media_player.setPosition(target_pos)

    def _on_position_changed(self, pos: int):
        if self._seek_pending(pos):
            return
        if not self.is_slider_pressed:
            self.seek_slider_docked.setValue(pos)
            self.floating_bar.seek_slider.setValue(pos)
        dur = self.media_player.duration()
        self._update_time_label(pos, dur)
        self._update_skip_btn(pos)

    def _on_duration_changed(self, dur: int):
        self.seek_slider_docked.setRange(0, dur)
        self.floating_bar.seek_slider.setRange(0, dur)
        self._update_time_label(self.media_player.position(), dur)

    def _update_time_label(self, pos: int, dur: int):
        txt = f"{format_time(pos)} / {format_time(dur)}"
        self.lbl_time_docked.setText(txt)
        self.floating_bar.lbl_time.setText(txt)

    def _on_playback_state_changed(self, state: QMediaPlayer.PlaybackState):
        sym = "⏸" if state == QMediaPlayer.PlaybackState.PlayingState else "▶"
        self.btn_play_docked.setText(sym)
        self.floating_bar.btn_play.setText(sym)

    def _toggle_mute(self):
        if self.audio_output is None:
            return
        is_muted = not self.audio_output.isMuted()
        self.audio_output.setMuted(is_muted)
        sym = "🔇" if is_muted else "🔊"
        self.btn_mute_docked.setText(sym)
        self.floating_bar.btn_mute.setText(sym)

    def _on_volume_changed(self, val: int):
        if self.audio_output is None:
            return
        self.audio_output.setVolume(val / 100.0)
        self.vol_slider_docked.blockSignals(True)
        self.floating_bar.vol_slider.blockSignals(True)
        self.vol_slider_docked.setValue(val)
        self.floating_bar.vol_slider.setValue(val)
        self.vol_slider_docked.blockSignals(False)
        self.floating_bar.vol_slider.blockSignals(False)

        sym = "🔇" if (val == 0 or self.audio_output.isMuted()) else "🔊"
        self.btn_mute_docked.setText(sym)
        self.floating_bar.btn_mute.setText(sym)

    def _handle_back(self):
        w = self.window()
        if w.isFullScreen():
            self._toggle_fullscreen()
        self._shutdown_playback()
        history_mgr.flush()
        self._cloud_sync()
        self.back_requested.emit()

    def _shutdown_playback(self):
        if getattr(self, "_playback_shutdown", False):
            return

        self._playback_shutdown = True
        self._hide_web()
        player = self.media_player
        self.media_player = None
        self.audio_output = None

        if player is not None:
            try:
                player.close()
            except Exception:
                import logging
                logging.exception("Error while shutting down playback")

    def closeEvent(self, event):
        self._shutdown_media_controls()
        self._shutdown_playback()
        super().closeEvent(event)
