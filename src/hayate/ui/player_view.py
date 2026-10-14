import threading
from PyQt6 import QtCore, QtWidgets, QtGui
from PyQt6.QtCore import Qt, QUrl, pyqtSignal, QPoint, QTimer
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QLabel, QFrame, QScrollArea, QListWidget, QListWidgetItem,
    QLineEdit, QComboBox, QSlider, QMessageBox, QProgressBar
)
from PyQt6.QtMultimedia import QMediaPlayer
from hayate.core.playback_platform import detect_playback_platform
from hayate.core.mpv_adapter import MpvPlayerAdapter

from hayate.core.anilist import AnimeMedia
from hayate.core.player import PlayerController
from hayate.core.history import history_mgr


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
        self.setStyleSheet("background-color: black;")

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
        self.setStyleSheet("background-color: black;")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.native_surface = _NativeVideoSurface(self)
        self.native_surface.doubleClickedCenter.connect(
            self.doubleClickedCenter.emit
        )
        layout.addWidget(self.native_surface)


class FloatingControlBar(QtWidgets.QWidget):
    """Independent floating overlay bar for fullscreen mode."""
    def __init__(self, parent=None):
        super().__init__(parent, Qt.WindowType.ToolTip | Qt.WindowType.FramelessWindowHint)
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
                background: #252538;
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

        self.btn_backward = QPushButton("⏪")
        self.btn_backward.setToolTip("Seek -10s")
        self.btn_backward.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_backward.setFixedSize(34, 34)
        card_layout.addWidget(self.btn_backward)

        self.btn_play = QPushButton("▶")
        self.btn_play.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_play.setFixedSize(36, 36)
        card_layout.addWidget(self.btn_play)

        self.btn_forward = QPushButton("⏩")
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

        self.btn_mute = QPushButton("🔊")
        self.btn_mute.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_mute.setFixedSize(36, 36)
        card_layout.addWidget(self.btn_mute)

        self.vol_slider = QSlider(Qt.Orientation.Horizontal)
        self.vol_slider.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.vol_slider.setFixedWidth(90)
        self.vol_slider.setRange(0, 100)
        self.vol_slider.setValue(100)
        card_layout.addWidget(self.vol_slider)

        self.btn_fullscreen = QPushButton("⛶")
        self.btn_fullscreen.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_fullscreen.setFixedSize(36, 36)
        card_layout.addWidget(self.btn_fullscreen)

        layout.addWidget(self.card)


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
            surface.setAttribute(Qt.WidgetAttribute.WA_NativeWindow, True)
            native_id = int(surface.winId())

            self.media_player = MpvPlayerAdapter(
                platform=self._playback_platform,
                native_id=native_id,
                parent=self,
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

        self.btn_back = QPushButton("← Back to Details")
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
        self.jump_input.setFixedSize(70, 30)
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
        filter_bar.addWidget(self.jump_input)

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
        self.audio_combo.addItem("Sub (Original)", "sub")
        self.audio_combo.addItem("Eng Dub", "dub_eng")
        self.audio_combo.addItem("Hindi Dub", "dub_hin")
        self.audio_combo.addItem("Any Dub", "any_dub")
        self.audio_combo.currentIndexChanged.connect(self._on_audio_changed)
        filter_bar.addWidget(self.audio_combo)
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
                background: #252538;
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

        self.btn_backward_docked = QPushButton("⏪")
        self.btn_backward_docked.setToolTip("Seek -10s")
        self.btn_backward_docked.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_backward_docked.setFixedSize(34, 34)
        card_layout.addWidget(self.btn_backward_docked)

        self.btn_play_docked = QPushButton("▶")
        self.btn_play_docked.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_play_docked.setFixedSize(36, 36)
        card_layout.addWidget(self.btn_play_docked)

        self.btn_forward_docked = QPushButton("⏩")
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
        self.seek_slider_docked.sliderPressed.connect(self._on_seek_pressed)
        self.seek_slider_docked.sliderReleased.connect(self._on_seek_released)
        card_layout.addWidget(self.seek_slider_docked, 1)

        self.btn_mute_docked = QPushButton("🔊")
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

        self.btn_fullscreen_docked = QPushButton("⛶")
        self.btn_fullscreen_docked.setToolTip("Toggle Fullscreen")
        self.btn_fullscreen_docked.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_fullscreen_docked.setFixedSize(36, 36)
        card_layout.addWidget(self.btn_fullscreen_docked)

        self.right_column.addWidget(self.docked_card)
        self.body_layout.addLayout(self.right_column, stretch=1)
        self.root_layout.addLayout(self.body_layout, stretch=1)

    def _setup_shortcuts(self):
        ctx = Qt.ShortcutContext.WidgetWithChildrenShortcut
        QtGui.QShortcut(QtGui.QKeySequence(Qt.Key.Key_Space), self, self._toggle_play, context=ctx)
        QtGui.QShortcut(QtGui.QKeySequence(Qt.Key.Key_F), self, self._toggle_fullscreen, context=ctx)
        QtGui.QShortcut(QtGui.QKeySequence(Qt.Key.Key_F11), self, self._toggle_fullscreen, context=ctx)
        QtGui.QShortcut(QtGui.QKeySequence(Qt.Key.Key_Escape), self, self._exit_fullscreen_only, context=ctx)
        QtGui.QShortcut(QtGui.QKeySequence(Qt.Key.Key_Left), self, lambda: self._seek_relative(-5000), context=ctx)
        QtGui.QShortcut(QtGui.QKeySequence(Qt.Key.Key_Right), self, lambda: self._seek_relative(5000), context=ctx)
        QtGui.QShortcut(QtGui.QKeySequence(Qt.Key.Key_Up), self, lambda: self._adjust_volume(5), context=ctx)
        QtGui.QShortcut(QtGui.QKeySequence(Qt.Key.Key_Down), self, lambda: self._adjust_volume(-5), context=ctx)

    def _connect_player_signals(self):
        """Connect signals belonging to the current playback adapter."""
        player = self.media_player
        if player is None:
            return
        player.positionChanged.connect(self._on_position_changed)
        player.durationChanged.connect(self._on_duration_changed)
        player.playbackStateChanged.connect(self._on_playback_state_changed)
        player.errorOccurred.connect(self._on_stream_error)

    def _connect_signals(self):
        self._connect_player_signals()

        # Floating bar signals
        self.floating_bar.btn_backward.clicked.connect(lambda: self._seek_relative(-10000))
        self.floating_bar.btn_forward.clicked.connect(lambda: self._seek_relative(10000))
        self.floating_bar.btn_play.clicked.connect(self._toggle_play)
        self.floating_bar.btn_mute.clicked.connect(self._toggle_mute)
        self.floating_bar.btn_fullscreen.clicked.connect(self._toggle_fullscreen)

        # Docked bar buttons
        self.btn_backward_docked.clicked.connect(lambda: self._seek_relative(-10000))
        self.btn_forward_docked.clicked.connect(lambda: self._seek_relative(10000))
        self.btn_play_docked.clicked.connect(self._toggle_play)
        self.btn_mute_docked.clicked.connect(self._toggle_mute)
        self.btn_fullscreen_docked.clicked.connect(self._toggle_fullscreen)
        self.floating_bar.seek_slider.sliderPressed.connect(self._on_seek_pressed)
        self.floating_bar.seek_slider.sliderReleased.connect(self._on_seek_released)
        self.floating_bar.vol_slider.valueChanged.connect(self._on_volume_changed)

    def _adjust_volume(self, delta: int):
        val = max(0, min(100, self.vol_slider_docked.value() + delta))
        self.vol_slider_docked.setValue(val)

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

    def _show_floating_bar(self, screen_geom):
        w = self.window()
        if not w.isFullScreen() or self.is_bar_visible:
            return
        self.is_bar_visible = True
        bar_w = min(1150, screen_geom.width() - 60)
        bar_h = 64
        x = screen_geom.x() + (screen_geom.width() - bar_w) // 2
        y = screen_geom.bottom() - bar_h - 24

        self.floating_bar.setGeometry(x, y, bar_w, bar_h)
        self.floating_bar.show()
        self.floating_bar.raise_()
        self.hide_timer.start()

    def _hide_floating_bar(self):
        w = self.window()
        if not w.isFullScreen() or not self.is_bar_visible:
            return
        self.is_bar_visible = False
        self.floating_bar.hide()

    def _toggle_fullscreen(self):
        w = self.window()
        if w.isFullScreen():
            self.poll_timer.stop()
            self.hide_timer.stop()
            self.cursor_idle_timer.stop()
            self.unsetCursor()
            self._hide_floating_bar()

            self.top_bar_widget.setVisible(True)
            self.sidebar.setVisible(True)
            self.docked_card.setVisible(True)
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

        pref_audio = self.player_ctrl.settings.get("preferred_audio", "sub") if hasattr(self.player_ctrl, "settings") else "sub"
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

    def _on_audio_changed(self, idx: int):
        if not self.anime:
            return
        val = self.audio_combo.itemData(idx)
        if hasattr(self.player_ctrl, "settings"):
            from hayate.core.settings import save_settings
            overrides = self.player_ctrl.settings.setdefault("anime_audio_overrides", {})
            overrides[str(self.anime.id)] = val
            save_settings(self.player_ctrl.settings)

    def play_episode(self, ep: int):
        if not self.anime:
            return
        if self.media_player is None:
            self._on_stream_error(
                self._playback_error or "The playback engine is unavailable."
            )
            return
        self.active_ep = ep
        self.title_lbl.setText(f"{self.anime.title}  —  EP {ep}")
        self.progress_bar.setVisible(True)
        self.placeholder_label.setText(f"Loading Episode {ep}...")
        self.placeholder_label.show()

        for idx in range(self.ep_list.count()):
            item = self.ep_list.item(idx)
            if item.data(Qt.ItemDataRole.UserRole) == ep:
                self.ep_list.setCurrentItem(item)
                self.ep_list.scrollToItem(item)
                break

        anime = self.anime
        dub_pref = self.audio_combo.currentData() or "sub"

        def _resolve():
            provider = self.player_ctrl.active_provider()
            if not provider or getattr(provider, "name", "") == "pseudo":
                self._stream_failed.emit("No streaming provider installed. Please install or select a provider in Settings.")
                return
            try:
                stream = provider.get_stream(anime.title, ep, dub=dub_pref)
            except Exception as e:
                self._stream_failed.emit(f"Stream source failed: {e}")
                return
            if not stream or not stream.url:
                self._stream_failed.emit(f"Episode {ep} not available on this provider.")
                return
            self._stream_resolved.emit(anime, ep, stream.url)

        threading.Thread(target=_resolve, daemon=True).start()

    def _on_stream_ready(self, anime, ep: int, url: str):
        self.progress_bar.setVisible(False)
        if self.media_player is None:
            self._on_stream_error(
                self._playback_error or "The playback engine is unavailable."
            )
            return

        self.placeholder_label.hide()
        self.media_player.setSource(QUrl(url))
        self.media_player.play()

        history_mgr.record_watch(
            anime_id=anime.id,
            title=anime.title,
            episode=ep,
            cover_url=anime.cover_url,
            banner_url=anime.banner_url,
            total_episodes=anime.episodes,
        )

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

    def _on_seek_pressed(self):
        self.is_slider_pressed = True

    def _on_seek_released(self):
        self.is_slider_pressed = False
        if self.media_player is None:
            return
        target_pos = self.seek_slider_docked.value()
        if self.is_bar_visible:
            target_pos = self.floating_bar.seek_slider.value()
        self.media_player.setPosition(target_pos)

    def _on_position_changed(self, pos: int):
        if not self.is_slider_pressed:
            self.seek_slider_docked.setValue(pos)
            self.floating_bar.seek_slider.setValue(pos)
        dur = self.media_player.duration()
        self._update_time_label(pos, dur)

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
        self.back_requested.emit()

    def _shutdown_playback(self):
        if getattr(self, "_playback_shutdown", False):
            return

        self._playback_shutdown = True
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
        self._shutdown_playback()
        super().closeEvent(event)
