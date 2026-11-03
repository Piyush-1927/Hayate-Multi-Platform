from hayate.core.assets import ic as _ic
from pathlib import Path
import base64
import json
import os
import re
import logging
from urllib.parse import urljoin, quote, unquote, urlparse
from PyQt6 import QtCore, QtWidgets, QtGui
from PyQt6.QtMultimedia import QMediaPlayer
from hayate.core.mpv_adapter import MpvPlayerAdapter
from hayate.ui.gl_video_surface import GLVideoSurface
from hayate.ui.pill_switch import PillSwitch
from hayate.ui.player_view import VideoOverlayWidget
from hayate.core.playback_platform import detect_playback_platform
import httpx
from html import unescape as _html_unescape
import threading
from concurrent.futures import ThreadPoolExecutor

from hayate.core.lan_server import ensure_tailscale, get_local_ip, lan_server
from hayate.core.settings import load_settings, save_settings

VIDEO_EXTS = {".mp4", ".mkv", ".webm", ".avi", ".m4v"}


class _RemoteScanSignals(QtCore.QObject):
    resolved = QtCore.pyqtSignal(int, str)  # token, base url with the probed port filled in
    denied = QtCore.pyqtSignal(int, int)    # token, HTTP status (401 = needs password, 429 = locked out)
    found = QtCore.pyqtSignal(int, list)  # token, relative file paths
    done = QtCore.pyqtSignal(int, int)    # token, failed request count


def _crawl_remote(base_url, on_found, cancelled, max_depth=10, workers=8, auth=None, on_denied=None):
    # Breadth-first crawl of the server's directory listings. One shared client,
    # all folders of a level fetched in parallel. Returns the failed request count.
    errors = 0
    seen = {base_url}
    level = [(base_url, "")]
    with httpx.Client(timeout=4.0, auth=auth) as client, ThreadPoolExecutor(max_workers=workers) as pool:
        def fetch(item):
            try:
                resp = client.get(item[0])
                if resp.status_code in (401, 429) and on_denied:
                    on_denied(resp.status_code, resp.headers.get("x-hayate-auth", ""))
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


_KR_SERVICE = "hayate-lan"


def _cred_file():
    from hayate.core.settings import config_dir
    return config_dir() / "lan_credentials.json"


def _cred_get(host):
    """Saved permanent password for a host: system keyring first, then the 0600 file."""
    try:
        import keyring
        pw = keyring.get_password(_KR_SERVICE, host)
        if pw:
            return pw
    except Exception:
        pass
    try:
        return str(json.loads(_cred_file().read_text(encoding="utf-8")).get(host, ""))
    except Exception:
        return ""


def _cred_set(host, pw):
    try:
        import keyring
        keyring.set_password(_KR_SERVICE, host, pw)
        return
    except Exception:
        pass
    path = _cred_file()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        data = {}
    data[host] = pw
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _normalize_host(text):
    host = (text or "").strip().rstrip("/")
    if host and "://" not in host:
        host = "http://" + host
    return host


def _origin(url):
    pr = urlparse(url)
    return f"{pr.scheme}://{pr.netloc}"


def _probe_hayate(host, cancelled=lambda: False):
    """host = scheme://name without a port. Looks for a Hayate server on 8080, then 8000-8079.
    Probes send no credentials, so a password never reaches a non-Hayate service."""
    ports = [8080] + list(range(8000, 8080))
    with httpx.Client(timeout=0.8) as client, ThreadPoolExecutor(max_workers=16) as pool:
        def check(port):
            if cancelled():
                return False
            try:
                r = client.get(f"{host}:{port}/")
            except Exception:
                return False
            if r.status_code == 401 and "hayate" in r.headers.get("www-authenticate", "").lower():
                return True
            if r.status_code == 429 and "retry-after" in r.headers:
                return True
            return r.status_code == 200 and "Directory listing for" in r.text[:2000]
        for port, ok in zip(ports, pool.map(check, ports)):
            if ok:
                return f"{host}:{port}"
    return None


def format_time(ms: int) -> str:
    seconds = max(0, ms // 1000)
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


class FloatingControlBar(QtWidgets.QWidget):
    """Independent top-level window guaranteed to float above Wayland video subsurfaces."""
    def __init__(self, parent=None):
        super().__init__(parent, QtCore.Qt.WindowType.ToolTip | QtCore.Qt.WindowType.FramelessWindowHint
                         | QtCore.Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
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

        self.btn_backward = _ic(QtWidgets.QPushButton(""), "rewind")
        self.btn_backward.setToolTip("Seek -10s")
        self.btn_backward.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        self.btn_backward.setFixedSize(34, 34)
        card_layout.addWidget(self.btn_backward)

        self.btn_play = _ic(QtWidgets.QPushButton(""), "play")
        self.btn_play.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        self.btn_play.setFixedSize(36, 36)
        card_layout.addWidget(self.btn_play)

        self.btn_forward = _ic(QtWidgets.QPushButton(""), "fast-forward")
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

        self.btn_mute = _ic(QtWidgets.QPushButton(""), "volume-2")
        self.btn_mute.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        self.btn_mute.setFixedSize(36, 36)
        card_layout.addWidget(self.btn_mute)

        self.vol_slider = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
        self.vol_slider.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        self.vol_slider.setFixedWidth(90)
        self.vol_slider.setRange(0, 100)
        self.vol_slider.setValue(100)
        card_layout.addWidget(self.vol_slider)

        self.btn_settings = _ic(QtWidgets.QPushButton(""), "settings")
        self.btn_settings.setToolTip("Playback speed & captions")
        self.btn_settings.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        self.btn_settings.setFixedSize(36, 36)
        card_layout.addWidget(self.btn_settings)

        self.btn_fullscreen = QtWidgets.QPushButton("🗗")
        self.btn_fullscreen.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        self.btn_fullscreen.setFixedSize(36, 36)
        card_layout.addWidget(self.btn_fullscreen)

        layout.addWidget(self.card)


def _lan_work_area(screen):
    """availableGeometry(), unless the platform reports a stale/tiny work area."""
    a, g = screen.availableGeometry(), screen.geometry()
    if a.width() < g.width() * 0.6 or a.height() < g.height() * 0.6:
        return g
    return a


class LANPlayerWindow(QtWidgets.QDialog):
    _MODES = (("self", "Self"), ("local", "Local LAN"), ("tailscale", "Internet LAN"))

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Hayate — LAN Media Player")
        self.setMinimumSize(960, 540)
        _scr = (parent.screen() if parent is not None else None) or QtGui.QGuiApplication.primaryScreen()
        if _scr is not None:
            self.setGeometry(_lan_work_area(_scr))
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
        QtGui.QGuiApplication.instance().applicationStateChanged.connect(
            lambda st: st != QtCore.Qt.ApplicationState.ApplicationActive and self._hide_floating_bar())
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

            is_gl = platform.embedding == "gl"
            if is_gl:
                native_id = 0
            else:
                native_id = int(self.video_widget.native_surface.winId())
                if not native_id:
                    raise RuntimeError("Qt did not provide a native video window ID.")

            self.player = MpvPlayerAdapter(
                platform, native_id, self,
                gl_widget=self.video_widget.native_surface if is_gl else None,
            )
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

        btn_back = _ic(QtWidgets.QPushButton("Back to Settings"), "arrow-left")
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
        left_row = QtWidgets.QHBoxLayout()
        left_row.setSpacing(14)
        left_row.addWidget(btn_back)

        title = QtWidgets.QLabel("LAN Media Player")
        title.setStyleSheet("font-size: 22px; font-weight: 800; color: #ffffff;")
        left_row.addWidget(title)
        self.view_switch = PillSwitch((("videos", "Videos"), ("folders", "Folders")))
        self.view_switch.changed.connect(self._on_view_changed)

        self.mode_switch = PillSwitch(self._MODES)
        self.mode_switch.changed.connect(self._on_mode_changed)
        mode_row = QtWidgets.QHBoxLayout()
        mode_row.setSpacing(0)
        mode_row.addStretch(1)
        mode_row.addWidget(self.mode_switch)
        host_row = QtWidgets.QHBoxLayout()
        host_row.setSpacing(10)

        lbl_host = QtWidgets.QLabel("Host URL:")
        lbl_host.setStyleSheet("color: #94a3b8; font-weight: 600; font-size: 13px;")
        lbl_host.setSizePolicy(QtWidgets.QSizePolicy.Policy.Fixed, QtWidgets.QSizePolicy.Policy.Preferred)
        host_row.addWidget(lbl_host)

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
        host_row.addWidget(self.host_input)

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
        btn_scan.setSizePolicy(QtWidgets.QSizePolicy.Policy.Fixed, QtWidgets.QSizePolicy.Policy.Fixed)
        host_row.addWidget(btn_scan)

        grid = QtWidgets.QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(8)
        _vc = QtCore.Qt.AlignmentFlag.AlignVCenter
        grid.addLayout(left_row, 0, 0, _vc)
        grid.addWidget(self.view_switch, 1, 0, QtCore.Qt.AlignmentFlag.AlignLeft | _vc)
        grid.addLayout(mode_row, 0, 2, _vc)
        grid.addLayout(host_row, 1, 2, _vc)
        grid.setColumnStretch(1, 1)
        top_bar.addLayout(grid)

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

        self.btn_backward_docked = _ic(QtWidgets.QPushButton(""), "rewind")
        self.btn_backward_docked.setToolTip("Seek -10s")
        self.btn_backward_docked.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        self.btn_backward_docked.setFixedSize(34, 34)
        d_layout.addWidget(self.btn_backward_docked)

        self.btn_play_docked = _ic(QtWidgets.QPushButton(""), "play")
        self.btn_play_docked.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        self.btn_play_docked.setFixedSize(36, 36)
        self.btn_play_docked.clicked.connect(self._toggle_play)
        d_layout.addWidget(self.btn_play_docked)

        self.btn_forward_docked = _ic(QtWidgets.QPushButton(""), "fast-forward")
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

        self.btn_mute_docked = _ic(QtWidgets.QPushButton(""), "volume-2")
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

        self.btn_settings_docked = _ic(QtWidgets.QPushButton(""), "settings")
        self.btn_settings_docked.setToolTip("Playback speed & captions")
        self.btn_settings_docked.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        self.btn_settings_docked.setFixedSize(36, 36)
        d_layout.addWidget(self.btn_settings_docked)

        self.btn_settings_docked.clicked.connect(lambda: self._open_player_menu(self.btn_settings_docked))
        self.btn_fullscreen_docked = _ic(QtWidgets.QPushButton(""), "maximize")
        self.btn_fullscreen_docked.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        self.btn_fullscreen_docked.setFixedSize(36, 36)
        self.btn_fullscreen_docked.clicked.connect(self._toggle_fullscreen)
        d_layout.addWidget(self.btn_fullscreen_docked)

        self.right_column.addWidget(self.docked_card)
        self.body_layout.addLayout(self.right_column, 1)
        self._gap = QtWidgets.QWidget()
        self._gap.setMinimumHeight(0)
        self._gap.setSizePolicy(QtWidgets.QSizePolicy.Policy.Preferred, QtWidgets.QSizePolicy.Policy.Expanding)
        self.root_layout.insertWidget(0, self._gap, 1)
        self._gap.hide()
        self.top_bar_widget.setContentsMargins(0, 0, 0, 0)
        self.root_layout.addLayout(self.body_layout, 0)
        self.root_layout.setSpacing(0)
        self._fs_mode = False
        self._init_client_modes()

    def _setup_shortcuts(self):
        ctx = QtCore.Qt.ShortcutContext.ApplicationShortcut
        QtGui.QShortcut(QtGui.QKeySequence(QtCore.Qt.Key.Key_Space), self, self._toggle_play, context=ctx).setAutoRepeat(False)
        QtGui.QShortcut(QtGui.QKeySequence(QtCore.Qt.Key.Key_F), self, self._toggle_fullscreen, context=ctx).setAutoRepeat(False)
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
        self._init_media_controls()

    def _init_media_controls(self):
        from PyQt6.QtMultimedia import QMediaPlayer
        from hayate.ui import media_controls
        self._mc_pos = 0
        vs = self.floating_bar.vol_slider

        def seek_abs(us):
            self._seek_relative(int(us / 1000) - self._mc_pos)

        def set_vol(v):
            vs.setValue(int(round(min(1.0, v) * vs.maximum())))

        self._mc = media_controls.create({
            "play": lambda: self.player.play(),
            "pause": lambda: self.player.pause(),
            "toggle": self._toggle_play,
            "stop": lambda: self.player.stop(),
            "next": lambda: self._step(1),
            "prev": lambda: self._step(-1),
            "raise": lambda: (self.show(), self.raise_(), self.activateWindow()),
            "seek_rel": lambda us: self._seek_relative(int(us / 1000)),
            "seek_abs": seek_abs,
            "volume": set_vol,
        }, position_fn=lambda: self._mc_pos * 1000, name="hayate.lan", parent=self)

        P = QMediaPlayer.PlaybackState

        def on_state(st):
            if not self._mc:
                return
            self._mc.update(status="Playing" if st == P.PlayingState
                            else "Paused" if st == P.PausedState else "Stopped")

        def on_pos(ms):
            self._mc_pos = int(ms)

        self.player.playbackStateChanged.connect(on_state)
        self.player.positionChanged.connect(on_pos)
        self.player.durationChanged.connect(lambda ms: self._mc and self._mc.update(length_us=int(ms) * 1000))

    def _playlist(self):
        return sorted(getattr(self, "_all_names", []), key=str.lower)

    def _step(self, delta):
        lst = self._playlist()
        cur = getattr(self, "_cur_rel", None)
        if cur not in lst:
            return
        i = lst.index(cur) + delta
        if 0 <= i < len(lst):
            it = QtWidgets.QListWidgetItem()
            it.setData(QtCore.Qt.ItemDataRole.UserRole, ("file", lst[i]))
            self._on_item_double_clicked(it)

    def _update_nav(self):
        mc = getattr(self, "_mc", None)
        if not mc:
            return
        lst = self._playlist()
        cur = getattr(self, "_cur_rel", None)
        i = lst.index(cur) if cur in lst else -1
        mc.update(can_next=0 <= i < len(lst) - 1, can_prev=i > 0)

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
        bar_w = min(1500, int(screen_geom.width() * 0.85))
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
            "QMenu{background:#1e1e2d;color:#e2e8f0;border:1px solid #3d3d52;padding:4px;}"
            "QMenu::item{padding:6px 18px;}QMenu::item:selected{background:#2d2d42;}"
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

            self._fs_mode = False
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
            _ic(self.btn_fullscreen_docked, "maximize", text="")

            self.showNormal()
            rg = getattr(self, "restored_geom", None)
            if rg is not None:
                self.setGeometry(rg)
            QtCore.QTimer.singleShot(200, self._fit_to_screen)
            QtCore.QTimer.singleShot(260, self._apply_player_layout)
        else:
            self._fs_mode = True
            self._apply_player_layout()
            self.top_bar_widget.setVisible(False)
            self.card_sidebar.setVisible(False)
            self.docked_card.setVisible(False)
            self.root_layout.setContentsMargins(0, 0, 0, 0)
            self.card_viewport.setStyleSheet("QFrame { background-color: #000000; border: none; }")

            self.restored_geom = self.geometry()
            self.showFullScreen()
            self.btn_fullscreen_docked.setText("🗗")

            self.poll_timer.start()
            self.cursor_idle_timer.start()

        self.setFocus()
        QtCore.QTimer.singleShot(20, self._force_surface_refresh)
        QtCore.QTimer.singleShot(100, self._force_surface_refresh)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._apply_player_layout()

    def _apply_player_layout(self):
        """Exact 16:9 viewport pinned to the bottom; leftover height split into three equal vertical gaps."""
        if not (hasattr(self, "_fs_mode") and hasattr(self, "docked_card")):
            return
        lay = self.root_layout
        if self._fs_mode:
            self.card_viewport.setMinimumHeight(0)
            self.card_viewport.setMaximumHeight(16777215)
            lay.setStretchFactor(self.body_layout, 1)
            lay.setSpacing(0)
            return
        lay.setStretchFactor(self.body_layout, 0)
        m = lay.contentsMargins()
        col_w = self.width() - m.left() - m.right() - self.card_sidebar.maximumWidth() - self.body_layout.spacing()
        free = (self.height() - self.top_bar_widget.sizeHint().height()
                - self.docked_card.maximumHeight() - self.right_column.spacing())
        h = max(160, min(int((col_w - 2) * 9 / 16) + 2, free - 24))
        g = max(8, (free - h) // 3)
        if self.card_viewport.minimumHeight() != h or self.card_viewport.maximumHeight() != h:
            self.card_viewport.setFixedHeight(h)
        if m.top() != g or m.bottom() != g or lay.spacing() != g:
            lay.setContentsMargins(m.left(), g, m.right(), g)
            lay.setSpacing(g)

    def _default_host(self, mode):
        if mode == "self":
            return "http://127.0.0.1"
        if mode == "tailscale":
            return ""
        return f"http://{get_local_ip()}:{lan_server.port}"

    def _init_client_modes(self):
        cfg = load_settings()
        hosts = cfg.get("lan_client_hosts")
        self._mode_hosts = dict(hosts) if isinstance(hosts, dict) else {}
        mode = cfg.get("lan_client_mode")
        self._mode = None
        self._set_mode(mode if mode in dict(self._MODES) else "local")
        self._refresh_last_used()

    def _on_mode_changed(self, mode):
        if mode and mode != self._mode:
            self._mode_hosts[self._mode] = self.host_input.text().strip()
            self._set_mode(mode)

    def _set_mode(self, mode, host=None):
        self._mode = mode
        self.mode_switch.set_current(mode)
        self.host_input.setPlaceholderText("http://100.x.y.z  or  machine-name" if mode == "tailscale" else "")
        if host is not None:
            self._mode_hosts[mode] = host
        self.host_input.setText(self._mode_hosts.get(mode) or self._default_host(mode))

    def _refresh_last_used(self):
        self._apply_player_layout()

    def _on_view_changed(self, key):
        self._folder = ""
        self._render_files()

    def _render_files(self):
        names = sorted(getattr(self, "_all_names", []), key=str.lower)
        fl = self.file_list
        fl.clear()
        role = QtCore.Qt.ItemDataRole.UserRole

        def add(text, kind, path):
            it = QtWidgets.QListWidgetItem(text)
            it.setData(role, (kind, path))
            fl.addItem(it)

        if self.view_switch.current() == "videos":
            for n in names:
                add(n, "file", n)
        else:
            cur = getattr(self, "_folder", "")
            pre = cur + "/" if cur else ""
            if cur:
                add("\u2190  ..", "dir", cur.rpartition("/")[0])
            dirs, files = set(), []
            for n in names:
                if not n.startswith(pre):
                    continue
                rest = n[len(pre):]
                if "/" in rest:
                    dirs.add(rest.split("/")[0])
                else:
                    files.append(n)
            for d in sorted(dirs, key=str.lower):
                add("\U0001F4C1  " + d, "dir", pre + d)
            for n in files:
                add(n[len(pre):], "file", n)
        if getattr(self, "_scan_status_item", None) is not None:
            st = QtWidgets.QListWidgetItem("Scanning\u2026")
            st.setFlags(QtCore.Qt.ItemFlag.NoItemFlags)
            fl.addItem(st)
            self._scan_status_item = st

    def _remember_connection(self):
        pend = getattr(self, "_cred_pending", None)
        if pend:
            self._cred_pending = None
            _cred_set(*pend)
        host = self.host_input.text().strip()
        if not host:
            return
        cfg = load_settings()
        hosts = cfg.get("lan_client_hosts")
        hosts = dict(hosts) if isinstance(hosts, dict) else {}
        hosts[self._mode] = host
        cfg["lan_client_hosts"] = hosts
        cfg["lan_client_mode"] = self._mode
        cfg["lan_client_last"] = {"mode": self._mode, "host": host}
        save_settings(cfg)
        self._mode_hosts[self._mode] = host
        self._refresh_last_used()

    def showEvent(self, e):
        super().showEvent(e)
        if not getattr(self, "_fitted", False):
            self._fitted = True
            QtCore.QTimer.singleShot(0, self._fit_to_screen)
            QtCore.QTimer.singleShot(200, self._fit_to_screen)  # WM frame is final by now

    def _fit_to_screen(self):
        """Fill the screen's usable area exactly: not maximized, not fullscreen."""
        if self.isMaximized() or self.isFullScreen():
            return
        scr = self.screen() or QtGui.QGuiApplication.primaryScreen()
        if not scr:
            return
        a = _lan_work_area(scr)
        fg, g = self.frameGeometry(), self.geometry()
        l, t = g.x() - fg.x(), g.y() - fg.y()
        r, b = fg.right() - g.right(), fg.bottom() - g.bottom()
        w, h = a.width() - l - r, a.height() - t - b
        if QtGui.QGuiApplication.platformName() == "wayland":
            self.resize(w, h)  # compositor owns position
        else:
            self.setGeometry(a.x() + l, a.y() + t, w, h)

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

    _SEEK_STYLE = """
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
    """

    def _update_slider_groove(self, position, duration):
        # Static main-player style; Qt draws the fill up to the handle itself.
        if getattr(self, "_seek_style_applied", False):
            return
        self._seek_style_applied = True
        self.seek_slider_docked.setStyleSheet(self._SEEK_STYLE)
        self.floating_bar.seek_slider.setStyleSheet(self._SEEK_STYLE)

    def _on_position_changed(self, position):
        player = getattr(self, "player", None)
        if self._player_cleanup_started or player is None:
            return
        if self._seek_pending(position):
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
        dur = self.player.duration()
        for sl in (self.seek_slider_docked, self.floating_bar.seek_slider):
            if sl.value() != pos:
                sl.blockSignals(True)
                sl.setValue(pos)
                sl.blockSignals(False)
        formatted = f"{format_time(pos)} / {format_time(dur)}"
        self.lbl_time_docked.setText(formatted)
        self.floating_bar.lbl_time.setText(formatted)
        self._update_slider_groove(pos, dur)

    def _on_seek_pressed(self):
        self._seek_target = None
        self.is_slider_pressed = True

    def _on_seek_moved(self, pos):
        self._sync_seek_ui(pos)
        # scrub-hook

    def _on_seek_released(self):
        self.is_slider_pressed = False
        val = self.seek_slider_docked.value() if not self.isFullScreen() else self.floating_bar.seek_slider.value()
        from time import monotonic
        self._seek_target, self._seek_t0 = val, monotonic()
        st = getattr(self, "_scrub_timer", None)
        if st is not None:
            st.stop()
        self._sync_seek_ui(val)
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

    def _cred_for(self, url):
        host = (urlparse(url).hostname or "").lower()
        if not host:
            return ""
        mem = getattr(self, "_cred_mem", None)
        if mem is None:
            mem = self._cred_mem = {}
        return mem.get(host) or _cred_get(host)

    def _auth_headers(self, url):
        pw = self._cred_for(url)
        if not pw:
            return None
        return {"Authorization": "Basic " + base64.b64encode(f"hayate:{pw}".encode()).decode()}

    def _scan_remote_media(self):
        self._all_names = []
        self._folder = ""
        self._scan_status_item = None
        self.file_list.clear()
        host = _normalize_host(self.host_input.text())
        if not host:
            return
        self._scan_origin = _origin(host)

        sig = getattr(self, "_scan_signals", None)
        if sig is None:
            sig = self._scan_signals = _RemoteScanSignals(self)
            sig.found.connect(self._on_scan_found)
            sig.done.connect(self._on_scan_done)
            sig.resolved.connect(self._on_scan_resolved)
            sig.denied.connect(self._on_scan_denied)
            self._scan_token = 0
        self._scan_token += 1
        token = self._scan_token

        self._scan_status_item = QtWidgets.QListWidgetItem("Scanning\u2026")
        self._scan_status_item.setFlags(QtCore.Qt.ItemFlag.NoItemFlags)
        self.file_list.addItem(self._scan_status_item)

        def work():
            try:
                stale = lambda: self._scan_token != token
                if self._mode == "tailscale":
                    ts_ip, ts_msg = ensure_tailscale()
                    if not ts_ip:
                        self._ts_msg = ts_msg
                        sig.done.emit(token, 1)
                        return
                pr = urlparse(host)
                base = host
                if pr.port is None:  # no port typed: look for the server in 8000-8080
                    base = _probe_hayate(host, stale)
                    if not base:
                        sig.done.emit(token, 1)
                        return
                    sig.resolved.emit(token, base)
                got, denied = [], []

                def on_found(names):
                    got.append(1)
                    sig.found.emit(token, names)

                def on_denied(code, kind):
                    denied.append(code)
                    self._auth_kind = kind

                def crawl(b):
                    pw = self._cred_for(b)
                    return _crawl_remote(b + "/", on_found, stale, auth=("hayate", pw) if pw else None,
                                         on_denied=on_denied)

                errors = crawl(base)
                if not got and not denied and errors and pr.port and 8000 <= pr.port <= 8080:
                    # saved port is dead - the server may have fallen back to another free port
                    alt = _probe_hayate(f"{pr.scheme}://{pr.netloc.rsplit(':', 1)[0]}", stale)
                    if alt and alt != base:
                        base = alt
                        sig.resolved.emit(token, base)
                        errors = crawl(base)
                if denied and not got:
                    sig.denied.emit(token, denied[0])
                else:
                    sig.done.emit(token, errors)
            except RuntimeError:
                pass  # window closed mid-scan

        threading.Thread(target=work, daemon=True).start()

    def _on_scan_resolved(self, token, base):
        if token != self._scan_token:
            return
        self._scan_origin = _origin(base)
        self.host_input.setText(base)

    def _on_scan_denied(self, token, code):
        if token != self._scan_token:
            return
        item = getattr(self, "_scan_status_item", None)
        if item is not None:
            row = self.file_list.row(item)
            if row >= 0:
                self.file_list.takeItem(row)
            self._scan_status_item = None
        if code == 429:
            QtWidgets.QMessageBox.warning(self, "LAN Server", "Too many wrong attempts. Wait a minute, then scan again.")
            return
        host = (urlparse(self._scan_origin).hostname or "").lower()
        kind = getattr(self, "_auth_kind", "")
        had = bool(self._cred_for(self._scan_origin))
        if had:
            msg = "Wrong password or PIN. Try again:"
        elif kind == "pin":
            msg = "This host uses a temporary PIN. Enter the current PIN:"
        else:
            msg = "This host is protected. Enter its password:"
        pw, ok = QtWidgets.QInputDialog.getText(self, "Hayate LAN", msg, QtWidgets.QLineEdit.EchoMode.Password)
        if ok and pw:
            if getattr(self, "_cred_mem", None) is None:
                self._cred_mem = {}
            self._cred_mem[host] = pw
            # a permanent password is saved only once a scan with it succeeds; PINs are never saved
            self._cred_pending = (host, pw) if kind == "password" else None
            self._scan_remote_media()
        else:
            self._cred_pending = None
            if had and getattr(self, "_cred_mem", None):
                self._cred_mem.pop(host, None)

    def _on_scan_found(self, token, names):
        if token != self._scan_token:
            return
        self._all_names.extend(names)
        self._render_files()

    def _on_scan_done(self, token, errors):
        if token != self._scan_token:
            return
        item = getattr(self, "_scan_status_item", None)
        if item is not None:
            row = self.file_list.row(item)
            if row >= 0:
                self.file_list.takeItem(row)
            self._scan_status_item = None
        self._render_files()
        if self._all_names:
            self._remember_connection()
        elif errors:
            msg = getattr(self, "_ts_msg", "") or "Could not reach the host. Check the address and that hosting is turned on."
            self._ts_msg = ""
            QtWidgets.QMessageBox.warning(self, "Scan Result", msg)
        else:
            QtWidgets.QMessageBox.information(self, "Scan Result", "No compatible video files found at this host.")

    def _sync_selection(self):
        cur = getattr(self, "_cur_rel", None)
        if not cur:
            return
        if self.view_switch.current() != "videos":
            folder = cur.rpartition("/")[0]
            if getattr(self, "_folder", "") != folder:
                self._folder = folder
                self._render_files()
        role = QtCore.Qt.ItemDataRole.UserRole
        fl = self.file_list
        for i in range(fl.count()):
            it = fl.item(i)
            d = it.data(role)
            if d and tuple(d) == ("file", cur):
                fl.setCurrentItem(it)
                fl.scrollToItem(it, QtWidgets.QAbstractItemView.ScrollHint.PositionAtCenter)
                break

    def _on_item_double_clicked(self, item):
        data = item.data(QtCore.Qt.ItemDataRole.UserRole)
        if not data:
            return
        kind, rel_path = data
        if kind == "dir":
            self._folder = rel_path
            self._render_files()
            return
        base_url = self.host_input.text().strip().rstrip("/")
        quoted_path = "/".join(quote(part) for part in rel_path.split("/"))
        if "://" not in base_url:
            base_url = "http://" + base_url
        stream_url = f"{base_url}/{quoted_path}"
        self._last_stream_url = stream_url
        if getattr(self, '_mc', None):
            self._mc.update(title=rel_path.rsplit('/', 1)[-1])
        self._cur_rel = rel_path
        self._update_nav()
        print("LAN stream_url:", stream_url, flush=True)

        if self.placeholder_label:
            self.placeholder_label.hide()

        self.player.stop()
        self.player.setSource(QtCore.QUrl(stream_url), headers=self._auth_headers(stream_url))
        self.player.play()
        self._sync_selection()
        self.setFocus()

    def _cleanup_player(self):
        mc = getattr(self, '_mc', None)
        if mc:
            self._mc = None
            mc.shutdown()
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
