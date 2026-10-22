from pathlib import Path
import re
import logging
from urllib.parse import urljoin, quote, unquote
from PyQt6 import QtCore, QtWidgets, QtGui
from PyQt6.QtMultimedia import QMediaPlayer
from hayate.core.mpv_adapter import MpvPlayerAdapter
from hayate.core.playback_platform import detect_playback_platform
import httpx
from html import unescape as _html_unescape
import threading
from concurrent.futures import ThreadPoolExecutor

from hayate.core.lan_server import get_local_ip, lan_server

VIDEO_EXTS = {".mp4", ".mkv", ".webm", ".avi", ".m4v"}


class _RemoteScanSignals(QtCore.QObject):
    found = QtCore.pyqtSignal(int, list)  # token, relative file paths
    done = QtCore.pyqtSignal(int, int)    # token, failed request count


def _crawl_remote(base_url, on_found, cancelled, max_depth=10, workers=8):
    # Breadth-first crawl of the server's directory listings. One shared client,
    # all folders of a level fetched in parallel. Returns the failed request count.
    errors = 0
    seen = {base_url}
    level = [(base_url, "")]
    with httpx.Client(timeout=4.0) as client, ThreadPoolExecutor(max_workers=workers) as pool:
        def fetch(item):
            try:
                resp = client.get(item[0])
                return resp.text if resp.status_code == 200 else None
            except Exception:
                return None

        for _ in range(max_depth + 1):
            if not level or cancelled():
                break
            nxt, names = [], []
            for (url, prefix), text in zip(level, pool.map(fetch, level)):
                if text is None:
                    errors += 1
                    continue
                for link in re.findall(r'href="([^"]+)"', text):
                    link = _html_unescape(link)
                    if link.startswith(("?", "/", "..")) or "://" in link:
                        continue
                    clean = unquote(link)
                    if clean.endswith("/"):
                        child = urljoin(url, link)
                        if child not in seen:
                            seen.add(child)
                            nxt.append((child, f"{prefix}{clean}"))
                    elif any(clean.lower().endswith(ext) for ext in VIDEO_EXTS):
                        names.append(f"{prefix}{clean}")
            if names:
                on_found(names)
            level = nxt
    return errors


def format_time(ms: int) -> str:
    seconds = max(0, ms // 1000)
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


class VideoOverlayWidget(QtWidgets.QWidget):
    doubleClickedCenter = QtCore.pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_NativeWindow, True)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_OpaquePaintEvent, True)

    def mouseDoubleClickEvent(self, event: QtGui.QMouseEvent):
        if event.button() == QtCore.Qt.MouseButton.LeftButton:
            center = QtCore.QPoint(self.width() // 2, self.height() // 2)
            dist_sq = (event.pos().x() - center.x()) ** 2 + (event.pos().y() - center.y()) ** 2
            if dist_sq <= (160 ** 2):
                self.doubleClickedCenter.emit()
                event.accept()
                return
        super().mouseDoubleClickEvent(event)


class FloatingControlBar(QtWidgets.QWidget):
    """Independent top-level window guaranteed to float above Wayland video subsurfaces."""
    def __init__(self, parent=None):
        super().__init__(parent, QtCore.Qt.WindowType.ToolTip | QtCore.Qt.WindowType.FramelessWindowHint)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedHeight(64)
        self._build_ui()

    def _build_ui(self):
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.card = QtWidgets.QFrame(self)
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

        card_layout = QtWidgets.QHBoxLayout(self.card)
        card_layout.setContentsMargins(16, 8, 16, 8)
        card_layout.setSpacing(12)

        self.btn_backward = QtWidgets.QPushButton("⏪")
        self.btn_backward.setToolTip("Seek -10s")
        self.btn_backward.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        self.btn_backward.setFixedSize(34, 34)
        card_layout.addWidget(self.btn_backward)

        self.btn_play = QtWidgets.QPushButton("▶")
        self.btn_play.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        self.btn_play.setFixedSize(36, 36)
        card_layout.addWidget(self.btn_play)

        self.btn_forward = QtWidgets.QPushButton("⏩")
        self.btn_forward.setToolTip("Seek +10s")
        self.btn_forward.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        self.btn_forward.setFixedSize(34, 34)
        card_layout.addWidget(self.btn_forward)

        self.lbl_time = QtWidgets.QLabel("00:00:00 / 00:00:00")
        self.lbl_time.setMinimumWidth(140)
        self.lbl_time.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.lbl_time.setStyleSheet("color: #94a3b8; font-size: 12px; font-weight: 600; border: none; background: transparent;")
        card_layout.addWidget(self.lbl_time)

        self.seek_slider = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
        self.seek_slider.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        self.seek_slider.setRange(0, 0)
        card_layout.addWidget(self.seek_slider, 1)

        self.btn_mute = QtWidgets.QPushButton("🔊")
        self.btn_mute.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        self.btn_mute.setFixedSize(36, 36)
        card_layout.addWidget(self.btn_mute)

        self.vol_slider = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
        self.vol_slider.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        self.vol_slider.setFixedWidth(90)
        self.vol_slider.setRange(0, 100)
        self.vol_slider.setValue(100)
        card_layout.addWidget(self.vol_slider)

        self.btn_settings = QtWidgets.QPushButton("⚙")
        self.btn_settings.setToolTip("Playback speed & captions")
        self.btn_settings.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        self.btn_settings.setFixedSize(36, 36)
        card_layout.addWidget(self.btn_settings)

        self.btn_fullscreen = QtWidgets.QPushButton("🗗")
        self.btn_fullscreen.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        self.btn_fullscreen.setFixedSize(36, 36)
        card_layout.addWidget(self.btn_fullscreen)

        layout.addWidget(self.card)


class LANPlayerWindow(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Hayate — LAN Media Player")
        self.setMinimumSize(960, 540)
        self.setFocusPolicy(QtCore.Qt.FocusPolicy.StrongFocus)

        # The LAN player owns its own libmpv instance.
        # QMediaPlayer is retained only for its compatible PlaybackState enum.
        self.player = None
        self.audio_output = None
        self._player_cleanup_started = False
        self.is_slider_pressed = False

        self.floating_bar = FloatingControlBar(self)
        self.floating_bar.hide()
        self.is_bar_visible = False
        self._last_cursor_pos = QtCore.QPoint()

        self.setStyleSheet("""
            QDialog {
                background-color: #0f0f17;
                color: #e2e8f0;
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            }
        """)
        self._build_ui()

        self.poll_timer = QtCore.QTimer(self)
        self.poll_timer.setInterval(40)
        self.poll_timer.timeout.connect(self._check_mouse_threshold)

        self.hide_timer = QtCore.QTimer(self)
        self.hide_timer.setInterval(2200)
        self.hide_timer.timeout.connect(self._hide_floating_bar)

        self.cursor_idle_timer = QtCore.QTimer(self)
        self.cursor_idle_timer.setInterval(2000)
        self.cursor_idle_timer.timeout.connect(self._hide_cursor_if_idle)

        # Create the native video surface before asking libmpv to embed into it.
        try:
            platform = detect_playback_platform()
            if not platform.supported:
                raise RuntimeError(platform.reason)

            native_id = int(self.video_widget.winId())
            if not native_id:
                raise RuntimeError("Qt did not provide a native video window ID.")

            self.player = MpvPlayerAdapter(platform, native_id, self)
            self.audio_output = self.player.audio_output
        except Exception as exc:
            QtWidgets.QMessageBox.critical(
                self,
                "LAN Player Unavailable",
                f"Could not initialize libmpv playback.\n\n{exc}",
            )
            QtCore.QTimer.singleShot(0, self.close)
            return

        self._setup_shortcuts()
        self._connect_signals()

    def _build_ui(self):
        self.root_layout = QtWidgets.QVBoxLayout(self)
        self.root_layout.setContentsMargins(24, 16, 24, 16)
        self.root_layout.setSpacing(16)

        # Top Bar
        self.top_bar_widget = QtWidgets.QWidget()
        top_bar = QtWidgets.QHBoxLayout(self.top_bar_widget)
        top_bar.setContentsMargins(0, 0, 0, 0)
        top_bar.setSpacing(14)

        btn_back = QtWidgets.QPushButton("← Back to Settings")
        btn_back.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        btn_back.setFixedHeight(34)
        btn_back.setStyleSheet("""
            QPushButton {
                background-color: #1c1c2b;
                color: #e2e8f0;
                border: 1px solid #2d2d42;
                border-radius: 8px;
                padding: 0 16px;
                font-size: 13px;
                font-weight: 600;
            }
            QPushButton:hover {
                background-color: #27273c;
                border-color: #38bdf8;
                color: #38bdf8;
            }
        """)
        btn_back.clicked.connect(self.close)
        top_bar.addWidget(btn_back)

        title = QtWidgets.QLabel("LAN Media Player")
        title.setStyleSheet("font-size: 22px; font-weight: 800; color: #ffffff;")
        top_bar.addWidget(title)
        top_bar.addStretch()

        lbl_host = QtWidgets.QLabel("Host URL:")
        lbl_host.setStyleSheet("color: #94a3b8; font-weight: 600; font-size: 13px;")
        top_bar.addWidget(lbl_host)

        default_url = f"http://{get_local_ip()}:{lan_server.port}"
        self.host_input = QtWidgets.QLineEdit(default_url)
        self.host_input.setFocusPolicy(QtCore.Qt.FocusPolicy.ClickFocus)
        self.host_input.setFixedWidth(240)
        self.host_input.setFixedHeight(34)
        self.host_input.setStyleSheet("""
            QLineEdit {
                background-color: #171724;
                color: #ffffff;
                border: 1px solid #26263a;
                border-radius: 8px;
                padding: 0 12px;
                font-size: 13px;
                font-weight: 500;
            }
            QLineEdit:focus {
                border-color: #38bdf8;
            }
        """)
        top_bar.addWidget(self.host_input)

        btn_scan = QtWidgets.QPushButton("Scan Files")
        btn_scan.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        btn_scan.setFixedHeight(34)
        btn_scan.setStyleSheet("""
            QPushButton {
                background-color: #38bdf8;
                color: #090a0f;
                border: none;
                border-radius: 8px;
                padding: 0 18px;
                font-size: 13px;
                font-weight: 700;
            }
            QPushButton:hover {
                background-color: #7dd3fc;
            }
        """)
        btn_scan.clicked.connect(self._scan_remote_media)
        top_bar.addWidget(btn_scan)

        self.root_layout.addWidget(self.top_bar_widget)

        # Body Layout
        self.body_layout = QtWidgets.QHBoxLayout()
        self.body_layout.setSpacing(16)

        # Left File Browser
        self.card_sidebar = QtWidgets.QFrame()
        self.card_sidebar.setFixedWidth(340)
        self.card_sidebar.setStyleSheet("""
            QFrame {
                background-color: #171724;
                border: 1px solid #26263a;
                border-radius: 12px;
            }
        """)
        side_layout = QtWidgets.QVBoxLayout(self.card_sidebar)
        side_layout.setContentsMargins(16, 16, 16, 16)
        side_layout.setSpacing(12)

        lbl_files = QtWidgets.QLabel("Available Files")
        lbl_files.setStyleSheet("color: #ffffff; font-weight: 700; font-size: 14px; border: none; background: transparent;")
        side_layout.addWidget(lbl_files)

        self.file_list = QtWidgets.QListWidget()
        self.file_list.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        self.file_list.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.file_list.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.file_list.setStyleSheet("""
            QListWidget {
                background-color: transparent;
                border: none;
                color: #cbd5e1;
                font-size: 13px;
            }
            QListWidget::item {
                padding: 10px 12px;
                border-radius: 8px;
                margin-bottom: 4px;
                background-color: #1c1c2e;
            }
            QListWidget::item:hover {
                background-color: #25253d;
                color: #38bdf8;
            }
            QListWidget::item:selected {
                background-color: #38bdf8;
                color: #090a0f;
                font-weight: 700;
            }
        """)
        self.file_list.itemDoubleClicked.connect(self._on_item_double_clicked)
        side_layout.addWidget(self.file_list)
        self.body_layout.addWidget(self.card_sidebar)

        # Right Column
        self.right_column = QtWidgets.QVBoxLayout()
        self.right_column.setSpacing(12)

        # Viewport Frame
        self.card_viewport = QtWidgets.QFrame()
        self.card_viewport.setStyleSheet("""
            QFrame {
                background-color: #000000;
                border: 1px solid #26263a;
                border-radius: 12px;
            }
        """)
        self.view_layout = QtWidgets.QVBoxLayout(self.card_viewport)
        self.view_layout.setContentsMargins(0, 0, 0, 0)
        self.view_layout.setSpacing(0)

        self.video_widget = VideoOverlayWidget(self.card_viewport)
        self.video_widget.doubleClickedCenter.connect(self._toggle_play)
        self.view_layout.addWidget(self.video_widget)

        self.placeholder_label = QtWidgets.QLabel("Double-click a video on the left to start playback.", self.card_viewport)
        self.placeholder_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.placeholder_label.setStyleSheet("color: #64748b; font-size: 14px; font-weight: 600; border: none; background: transparent;")
        self.view_layout.addWidget(self.placeholder_label)

        self.right_column.addWidget(self.card_viewport, 1)

        # Docked Maximized Control Bar
        self.docked_card = QtWidgets.QFrame()
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

        d_layout = QtWidgets.QHBoxLayout(self.docked_card)
        d_layout.setContentsMargins(16, 8, 16, 8)
        d_layout.setSpacing(12)

        self.btn_backward_docked = QtWidgets.QPushButton("⏪")
        self.btn_backward_docked.setToolTip("Seek -10s")
        self.btn_backward_docked.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        self.btn_backward_docked.setFixedSize(34, 34)
        d_layout.addWidget(self.btn_backward_docked)

        self.btn_play_docked = QtWidgets.QPushButton("▶")
        self.btn_play_docked.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        self.btn_play_docked.setFixedSize(36, 36)
        self.btn_play_docked.clicked.connect(self._toggle_play)
        d_layout.addWidget(self.btn_play_docked)

        self.btn_forward_docked = QtWidgets.QPushButton("⏩")
        self.btn_forward_docked.setToolTip("Seek +10s")
        self.btn_forward_docked.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        self.btn_forward_docked.setFixedSize(34, 34)
        d_layout.addWidget(self.btn_forward_docked)

        self.lbl_time_docked = QtWidgets.QLabel("00:00 / 00:00")
        self.lbl_time_docked.setMinimumWidth(140)
        self.lbl_time_docked.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.lbl_time_docked.setStyleSheet("color: #94a3b8; font-size: 12px; font-weight: 600; border: none; background: transparent;")
        d_layout.addWidget(self.lbl_time_docked)

        self.seek_slider_docked = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
        self.seek_slider_docked.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        self.seek_slider_docked.setRange(0, 0)
        self.seek_slider_docked.sliderMoved.connect(self._on_seek_moved)
        self.seek_slider_docked.sliderPressed.connect(self._on_seek_pressed)
        self.seek_slider_docked.sliderReleased.connect(self._on_seek_released)
        d_layout.addWidget(self.seek_slider_docked, 1)

        self.btn_mute_docked = QtWidgets.QPushButton("🔊")
        self.btn_mute_docked.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        self.btn_mute_docked.setFixedSize(36, 36)
        d_layout.addWidget(self.btn_mute_docked)

        self.vol_slider_docked = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
        self.vol_slider_docked.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        self.vol_slider_docked.setFixedWidth(90)
        self.vol_slider_docked.setRange(0, 100)
        self.vol_slider_docked.setValue(100)
        self.vol_slider_docked.valueChanged.connect(self._on_volume_changed)
        d_layout.addWidget(self.vol_slider_docked)

        self.btn_settings_docked = QtWidgets.QPushButton("⚙")
        self.btn_settings_docked.setToolTip("Playback speed & captions")
        self.btn_settings_docked.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        self.btn_settings_docked.setFixedSize(36, 36)
        d_layout.addWidget(self.btn_settings_docked)

        self.btn_settings_docked.clicked.connect(lambda: self._open_player_menu(self.btn_settings_docked))
        self.btn_fullscreen_docked = QtWidgets.QPushButton("⛶")
        self.btn_fullscreen_docked.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        self.btn_fullscreen_docked.setFixedSize(36, 36)
        self.btn_fullscreen_docked.clicked.connect(self._toggle_fullscreen)
        d_layout.addWidget(self.btn_fullscreen_docked)

        self.right_column.addWidget(self.docked_card)
        self.body_layout.addLayout(self.right_column, 1)
        self.root_layout.addLayout(self.body_layout, 1)

    def _setup_shortcuts(self):
        ctx = QtCore.Qt.ShortcutContext.ApplicationShortcut
        QtGui.QShortcut(QtGui.QKeySequence(QtCore.Qt.Key.Key_Space), self, self._toggle_play, context=ctx)
        QtGui.QShortcut(QtGui.QKeySequence(QtCore.Qt.Key.Key_F), self, self._toggle_fullscreen, context=ctx)
        QtGui.QShortcut(QtGui.QKeySequence(QtCore.Qt.Key.Key_Left), self, lambda: self.player.setPosition(max(0, self.player.position() - 5000)), context=ctx)
        QtGui.QShortcut(QtGui.QKeySequence(QtCore.Qt.Key.Key_Right), self, lambda: self.player.setPosition(min(self.player.duration(), self.player.position() + 5000)), context=ctx)
        QtGui.QShortcut(QtGui.QKeySequence(QtCore.Qt.Key.Key_Up), self, lambda: self.vol_slider_docked.setValue(min(100, self.vol_slider_docked.value() + 5)), context=ctx)
        QtGui.QShortcut(QtGui.QKeySequence(QtCore.Qt.Key.Key_Down), self, lambda: self.vol_slider_docked.setValue(max(0, self.vol_slider_docked.value() - 5)), context=ctx)

    def _connect_signals(self):
        self.player.playbackStateChanged.connect(self._on_state_changed)
        self.player.positionChanged.connect(self._on_position_changed)
        self.player.durationChanged.connect(self._on_duration_changed)
        self.player.errorOccurred.connect(self._on_player_error)

        self.floating_bar.btn_backward.clicked.connect(self._seek_backward_10)
        self.floating_bar.btn_forward.clicked.connect(self._seek_forward_10)
        if hasattr(self, 'btn_backward_docked'):
            self.btn_backward_docked.clicked.connect(self._seek_backward_10)
            self.btn_forward_docked.clicked.connect(self._seek_forward_10)

        self.btn_mute_docked.clicked.connect(self._toggle_mute)
        self.floating_bar.btn_play.clicked.connect(self._toggle_play)
        self.floating_bar.btn_mute.clicked.connect(self._toggle_mute)
        self.floating_bar.btn_fullscreen.clicked.connect(self._toggle_fullscreen)
        self.floating_bar.btn_settings.clicked.connect(lambda: self._open_player_menu(self.floating_bar.btn_settings))
        self.floating_bar.seek_slider.sliderMoved.connect(self._on_seek_moved)
        self.floating_bar.seek_slider.sliderPressed.connect(self._on_seek_pressed)
        self.floating_bar.seek_slider.sliderReleased.connect(self._on_seek_released)
        self.floating_bar.vol_slider.valueChanged.connect(self._on_volume_changed)

    def _on_player_error(self, message):
        QtWidgets.QMessageBox.warning(
            self,
            "LAN Playback Error",
            f"{message}\n\n{getattr(self, '_last_stream_url', '')}",
        )

    def _hide_cursor_if_idle(self):
        if self.isFullScreen() and not self.is_bar_visible:
            self.setCursor(QtCore.Qt.CursorShape.BlankCursor)

    def _check_mouse_threshold(self):
        if not self.isFullScreen():
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
        if not self.isFullScreen() or self.is_bar_visible:
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
        if not self.isFullScreen() or not self.is_bar_visible:
            return
        self.is_bar_visible = False
        self.floating_bar.hide()

    def _open_player_menu(self, btn):
        from PyQt6.QtGui import QAction, QActionGroup
        mp = self.player
        menu = QtWidgets.QMenu(self)
        menu.setStyleSheet(
            "QMenu{background:#0f172a;color:#e2e8f0;border:1px solid #334155;padding:4px;}"
            "QMenu::item{padding:6px 18px;}QMenu::item:selected{background:#1e293b;}"
            "QMenu::item:disabled{color:#64748b;}")

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

        menu.exec(btn.mapToGlobal(QtCore.QPoint(0, -menu.sizeHint().height())))

    def _toggle_fullscreen(self):
        if self.isFullScreen():
            self.poll_timer.stop()
            self.hide_timer.stop()
            self.cursor_idle_timer.stop()
            self.unsetCursor()
            self._hide_floating_bar()

            self.top_bar_widget.setVisible(True)
            self.card_sidebar.setVisible(True)
            self.docked_card.setVisible(True)
            self.root_layout.setContentsMargins(24, 16, 24, 16)
            self.card_viewport.setStyleSheet("""
                QFrame {
                    background-color: #000000;
                    border: 1px solid #26263a;
                    border-radius: 12px;
                }
            """)
            self.btn_fullscreen_docked.setText("⛶")

            self.showNormal()
            self.showMaximized()
        else:
            self.top_bar_widget.setVisible(False)
            self.card_sidebar.setVisible(False)
            self.docked_card.setVisible(False)
            self.root_layout.setContentsMargins(0, 0, 0, 0)
            self.card_viewport.setStyleSheet("QFrame { background-color: #000000; border: none; }")

            self.showFullScreen()
            self.btn_fullscreen_docked.setText("🗗")

            self.poll_timer.start()
            self.cursor_idle_timer.start()

        self.setFocus()
        QtCore.QTimer.singleShot(20, self._force_surface_refresh)
        QtCore.QTimer.singleShot(100, self._force_surface_refresh)

    def _force_surface_refresh(self):
        self.video_widget.resize(self.card_viewport.size())
        self.video_widget.updateGeometry()
        self.card_viewport.updateGeometry()
        self.update()

    def keyPressEvent(self, event: QtGui.QKeyEvent):
        if event.key() == QtCore.Qt.Key.Key_Escape:
            if self.isFullScreen():
                self._toggle_fullscreen()
                event.accept()
                return
            else:
                self.close()
                event.accept()
                return
        super().keyPressEvent(event)

    def _toggle_play(self):
        if self.host_input.hasFocus():
            self.host_input.clearFocus()
            self.setFocus()
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def _on_state_changed(self, state):
        txt = "⏸" if state == QMediaPlayer.PlaybackState.PlayingState else "▶"
        self.btn_play_docked.setText(txt)
        self.floating_bar.btn_play.setText(txt)

    def _seek_backward_10(self):
        self._seek_relative(-10000)

    def _seek_forward_10(self):
        self._seek_relative(10000)

    def _seek_relative(self, delta_ms):
        dur = self.player.duration()
        pos = self.player.position()
        target = max(0, min(dur, pos + delta_ms))
        self.player.setPosition(target)

    def _update_slider_groove(self, position, duration):
        if duration <= 0:
            return
        p_ratio = max(0.0, min(1.0, position / duration))
        b_ratio = max(0.0, min(1.0, (position + 10000) / duration))
        
        # Build dynamic linear gradient: progress (#38bdf8), cache forward (#475569), remaining (#252538)
        grad = (
            f"qlineargradient(x1:0, y1:0, x2:1, y2:0, "
            f"stop:0 #38bdf8, stop:{p_ratio:.4f} #38bdf8, "
            f"stop:{p_ratio + 0.0001:.4f} #475569, stop:{b_ratio:.4f} #475569, "
            f"stop:{b_ratio + 0.0001:.4f} #252538, stop:1 #252538)"
        )
        style = f"""
            QSlider::groove:horizontal {{
                height: 5px;
                background: {grad};
                border-radius: 2px;
            }}
            QSlider::sub-page:horizontal {{
                background: transparent;
            }}
            QSlider::handle:horizontal {{
                background: #ffffff;
                width: 12px;
                height: 12px;
                margin-top: -4px;
                margin-bottom: -4px;
                border-radius: 6px;
            }}
        """
        self.seek_slider_docked.setStyleSheet(style)
        self.floating_bar.seek_slider.setStyleSheet(style)

    def _on_position_changed(self, position):
        player = getattr(self, "player", None)
        if self._player_cleanup_started or player is None:
            return

        if not self.is_slider_pressed:
            self.seek_slider_docked.setValue(position)
            self.floating_bar.seek_slider.setValue(position)

        dur = player.duration()
        formatted = f"{format_time(position)} / {format_time(dur)}"
        self.lbl_time_docked.setText(formatted)
        self.floating_bar.lbl_time.setText(formatted)
        self._update_slider_groove(position, dur)

    def _on_duration_changed(self, duration):
        player = getattr(self, "player", None)
        if self._player_cleanup_started or player is None:
            return

        self.seek_slider_docked.setRange(0, duration)
        self.floating_bar.seek_slider.setRange(0, duration)
        pos = player.position()
        formatted = f"{format_time(pos)} / {format_time(duration)}"
        self.lbl_time_docked.setText(formatted)
        self.floating_bar.lbl_time.setText(formatted)

    def _on_seek_pressed(self):
        self.is_slider_pressed = True

    def _on_seek_moved(self, pos):
        dur = self.player.duration()
        formatted = f"{format_time(pos)} / {format_time(dur)}"
        self.lbl_time_docked.setText(formatted)
        self.floating_bar.lbl_time.setText(formatted)

    def _on_seek_released(self):
        self.is_slider_pressed = False
        val = self.seek_slider_docked.value() if not self.isFullScreen() else self.floating_bar.seek_slider.value()
        self.player.setPosition(val)

    def _on_volume_changed(self, val):
        self.audio_output.setVolume(val / 100.0)
        txt = "🔇" if val == 0 else "🔊"
        self.btn_mute_docked.setText(txt)
        self.floating_bar.btn_mute.setText(txt)
        if self.vol_slider_docked.value() != val:
            self.vol_slider_docked.setValue(val)
        if self.floating_bar.vol_slider.value() != val:
            self.floating_bar.vol_slider.setValue(val)

    def _toggle_mute(self):
        is_muted = not self.audio_output.isMuted()
        self.audio_output.setMuted(is_muted)
        txt = "🔇" if is_muted else "🔊"
        self.btn_mute_docked.setText(txt)
        self.floating_bar.btn_mute.setText(txt)

    def _scan_remote_media(self):
        self.file_list.clear()
        host = self.host_input.text().strip().rstrip("/")
        if not host:
            return
        base_url = host + "/"

        sig = getattr(self, "_scan_signals", None)
        if sig is None:
            sig = self._scan_signals = _RemoteScanSignals(self)
            sig.found.connect(self._on_scan_found)
            sig.done.connect(self._on_scan_done)
            self._scan_token = 0
        self._scan_token += 1
        token = self._scan_token

        self._scan_status_item = QtWidgets.QListWidgetItem("Scanning…")
        self._scan_status_item.setFlags(QtCore.Qt.ItemFlag.NoItemFlags)
        self.file_list.addItem(self._scan_status_item)

        def work():
            try:
                errors = _crawl_remote(
                    base_url,
                    lambda names: sig.found.emit(token, names),
                    lambda: self._scan_token != token,
                )
                sig.done.emit(token, errors)
            except RuntimeError:
                pass  # window closed mid-scan

        threading.Thread(target=work, daemon=True).start()

    def _on_scan_found(self, token, names):
        if token != self._scan_token:
            return
        for name in names:
            self.file_list.addItem(name)

    def _on_scan_done(self, token, errors):
        if token != self._scan_token:
            return
        item = getattr(self, "_scan_status_item", None)
        if item is not None:
            row = self.file_list.row(item)
            if row >= 0:
                self.file_list.takeItem(row)
            self._scan_status_item = None
        if self.file_list.count():
            self.file_list.sortItems()
        elif errors:
            QtWidgets.QMessageBox.warning(self, "Scan Result", "Could not reach the host. Check the address and that hosting is turned on.")
        else:
            QtWidgets.QMessageBox.information(self, "Scan Result", "No compatible video files found at this host.")

    def _on_item_double_clicked(self, item):
        rel_path = item.text()
        base_url = self.host_input.text().strip().rstrip("/")
        quoted_path = "/".join(quote(part) for part in rel_path.split("/"))
        if "://" not in base_url:
            base_url = "http://" + base_url
        stream_url = f"{base_url}/{quoted_path}"
        self._last_stream_url = stream_url
        print("LAN stream_url:", stream_url, flush=True)

        if self.placeholder_label:
            self.placeholder_label.hide()

        self.player.stop()
        self.player.setSource(QtCore.QUrl(stream_url))
        self.player.play()
        self.setFocus()

    def _cleanup_player(self):
        # This is the existing shared teardown path for reject() and closeEvent().
        if self._player_cleanup_started:
            return
        self._player_cleanup_started = True

        self.poll_timer.stop()
        self.hide_timer.stop()
        self.cursor_idle_timer.stop()
        self.unsetCursor()
        self.floating_bar.close()

        player = getattr(self, "player", None)
        if player is None:
            return

        # Detach callbacks first: stop() and terminate() may produce late events.
        for signal, slot in (
            (player.playbackStateChanged, self._on_state_changed),
            (player.positionChanged, self._on_position_changed),
            (player.durationChanged, self._on_duration_changed),
            (player.errorOccurred, self._on_player_error),
        ):
            try:
                signal.disconnect(slot)
            except (TypeError, RuntimeError):
                # The signal may not have been connected or may already be gone.
                pass

        # Queued callbacks will now see no active player and return safely.
        self.player = None
        try:
            try:
                player.stop()
            except Exception:
                logging.getLogger(__name__).exception(
                    "Error stopping the LAN playback adapter during teardown"
                )
        finally:
            try:
                player.close()
            except Exception:
                logging.getLogger(__name__).exception(
                    "Error closing the LAN playback adapter during teardown"
                )

    def closeEvent(self, event):
        self._cleanup_player()
        super().closeEvent(event)

    def reject(self):
        if self.isFullScreen():
            self._toggle_fullscreen()
            return
        self._cleanup_player()
        super().reject()
