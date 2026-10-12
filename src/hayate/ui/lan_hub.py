from hayate.core.settings import load_settings, save_settings
from pathlib import Path
from PyQt6 import QtCore, QtWidgets

from hayate.core.lan_server import get_local_ip, lan_server


class LocalHubWindow(QtWidgets.QDialog):
    """Dedicated Host Manager styled consistently with the rest of Hayate."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Hayate — LAN Host Manager")
        self._ip_visible = False

        # Match base window background
        self.setStyleSheet("""
            QDialog {
                background-color: #0f0f17;
                color: #e2e8f0;
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            }
        """)
        self._build_ui()

    def _build_ui(self):
        main_layout = QtWidgets.QVBoxLayout(self)
        main_layout.setContentsMargins(40, 32, 40, 32)
        main_layout.setSpacing(24)

        # Top Bar: Back Button & Title
        top_bar = QtWidgets.QHBoxLayout()
        top_bar.setSpacing(18)

        btn_back = QtWidgets.QPushButton("← Back to Settings")
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
        btn_back.clicked.connect(self.accept)
        top_bar.addWidget(btn_back)

        title = QtWidgets.QLabel("LAN Host Manager")
        title.setStyleSheet("font-size: 22px; font-weight: 800; color: #ffffff;")
        top_bar.addWidget(title)
        top_bar.addStretch()

        main_layout.addLayout(top_bar)

        # Card 1: Shared Directory
        card_dir = QtWidgets.QFrame()
        card_dir.setStyleSheet("""
            QFrame {
                background-color: #171724;
                border: 1px solid #26263a;
                border-radius: 12px;
            }
        """)
        dir_layout = QtWidgets.QVBoxLayout(card_dir)
        dir_layout.setContentsMargins(24, 20, 24, 20)
        dir_layout.setSpacing(12)

        dir_desc = QtWidgets.QLabel(
            "Select the folder on your NVMe/SSD containing the anime and video files you want to host across your local network."
        )
        dir_desc.setStyleSheet("color: #94a3b8; font-size: 13px; border: none; background: transparent;")
        dir_layout.addWidget(dir_desc)

        row_picker = QtWidgets.QHBoxLayout()
        row_picker.setSpacing(14)

        btn_browse = QtWidgets.QPushButton("Choose Folder")
        btn_browse.setFixedHeight(34)
        btn_browse.setStyleSheet("""
            QPushButton {
                background-color: #222233;
                color: #e2e8f0;
                border: 1px solid #363650;
                border-radius: 6px;
                padding: 0 16px;
                font-size: 13px;
                font-weight: 600;
            }
            QPushButton:hover {
                background-color: #2c2c42;
                border-color: #38bdf8;
                color: #38bdf8;
            }
        """)
        btn_browse.clicked.connect(self._choose_directory)
        row_picker.addWidget(btn_browse)

        cfg = load_settings()
        saved = cfg.get("lan_folder")
        if saved and Path(saved).is_dir():
            chosen_dir = Path(saved)
        else:
            dl = Path.home() / "Downloads"
            chosen_dir = dl if dl.is_dir() else Path.home()
        if not lan_server.serving_dir:
            lan_server.serving_dir = chosen_dir
        current_dir = lan_server.serving_dir
        self.dir_label = QtWidgets.QLabel(str(current_dir))
        self.dir_label.setStyleSheet("color: #cbd5e1; font-size: 13px; font-weight: 500; border: none; background: transparent;")
        row_picker.addWidget(self.dir_label, 1)

        dir_layout.addLayout(row_picker)
        main_layout.addWidget(card_dir)

        # Card 2: Server Controls & LAN Status
        card_ctrl = QtWidgets.QFrame()
        card_ctrl.setStyleSheet("""
            QFrame {
                background-color: #171724;
                border: 1px solid #26263a;
                border-radius: 12px;
            }
        """)
        ctrl_layout = QtWidgets.QVBoxLayout(card_ctrl)
        ctrl_layout.setContentsMargins(24, 20, 24, 20)
        ctrl_layout.setSpacing(14)

        ctrl_desc = QtWidgets.QLabel(
            "Control background HTTP streaming daemon. When active, compatible devices can stream via Range requests."
        )
        ctrl_desc.setStyleSheet("color: #94a3b8; font-size: 13px; border: none; background: transparent;")
        ctrl_layout.addWidget(ctrl_desc)

        row_status = QtWidgets.QHBoxLayout()
        row_status.setSpacing(14)

        btn_text = "Stop Server" if lan_server.is_running else "Start Server"
        self.btn_toggle_server = QtWidgets.QPushButton(btn_text)
        self.btn_toggle_server.setFixedHeight(34)
        self._update_btn_style()
        self.btn_toggle_server.clicked.connect(self._toggle_server)
        row_status.addWidget(self.btn_toggle_server)

        # Mask eye toggle button
        self.btn_toggle_ip = QtWidgets.QPushButton("👁")
        self.btn_toggle_ip.setFixedSize(34, 34)
        self.btn_toggle_ip.setToolTip("Show/Hide IP")
        self.btn_toggle_ip.setStyleSheet("""
            QPushButton {
                background-color: #222233;
                color: #e2e8f0;
                border: 1px solid #363650;
                border-radius: 6px;
                font-size: 13px;
            }
            QPushButton:hover {
                background-color: #2c2c42;
                border-color: #38bdf8;
            }
        """)
        self.btn_toggle_ip.clicked.connect(self._toggle_ip_visibility)
        row_status.addWidget(self.btn_toggle_ip)

        self.status_label = QtWidgets.QLabel()
        self.status_label.setStyleSheet("color: #38bdf8; font-weight: 600; font-size: 13px; border: none; background: transparent;")
        row_status.addWidget(self.status_label, 1)

        ctrl_layout.addLayout(row_status)
        main_layout.addWidget(card_ctrl)

        main_layout.addStretch()
        self._refresh_status_text()

    def _toggle_ip_visibility(self):
        self._ip_visible = not self._ip_visible
        self.btn_toggle_ip.setText("🙈" if self._ip_visible else "👁")
        self._refresh_status_text()

    def _refresh_status_text(self):
        ip = get_local_ip() if self._ip_visible else "•••.•••.•••.•••"
        if lan_server.is_running:
            self.status_label.setText(f"Hosting at: http://{ip}:{lan_server.port}")
        else:
            self.status_label.setText(f"LAN IP: {ip} (Inactive)")

    def _update_btn_style(self):
        if lan_server.is_running:
            self.btn_toggle_server.setStyleSheet("""
                QPushButton {
                    background-color: #ef4444;
                    color: #ffffff;
                    border: none;
                    border-radius: 6px;
                    padding: 0 20px;
                    font-size: 13px;
                    font-weight: 700;
                }
                QPushButton:hover { background-color: #dc2626; }
            """)
        else:
            self.btn_toggle_server.setStyleSheet("""
                QPushButton {
                    background-color: #10b981;
                    color: #ffffff;
                    border: none;
                    border-radius: 6px;
                    padding: 0 20px;
                    font-size: 13px;
                    font-weight: 700;
                }
                QPushButton:hover { background-color: #059669; }
            """)

    def _choose_directory(self):
        path = QtWidgets.QFileDialog.getExistingDirectory(self, "Select Folder to Host")
        if path:
            chosen = Path(path)
            lan_server.serving_dir = chosen
            cfg = load_settings()
            cfg["lan_folder"] = str(chosen)
            save_settings(cfg)
            self.dir_label.setText(str(chosen))
            if lan_server.is_running:
                lan_server.start(chosen)
                self._refresh_status_text()

    def _toggle_server(self):
        if not lan_server.serving_dir and not lan_server.is_running:
            QtWidgets.QMessageBox.warning(self, "Warning", "Select a folder to host first.")
            return

        if lan_server.is_running:
            lan_server.stop()
            self.btn_toggle_server.setText("Start Server")
        else:
            lan_server.start(lan_server.serving_dir)
            self.btn_toggle_server.setText("Stop Server")

        self._refresh_status_text()
        self._update_btn_style()
