import json
import subprocess
from pathlib import Path

from PyQt6.QtCore import QFileSystemWatcher, QPoint, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from hayate.core.history import history_mgr
from hayate.core.lan_server import get_local_ip, lan_server
from hayate.ui.lan_hub import LocalHubWindow
from hayate.ui.lan_player import LANPlayerWindow
from hayate.core.image_cache import image_mgr
from hayate.core.settings import config_dir, load_settings, save_settings, cache_dir
from PyQt6.QtWidgets import QSlider
from hayate.providers import load_providers, provider_label


def _ensure_check_icon() -> str:
    icon_path = cache_dir() / "check.png"
    if not icon_path.exists():
        pm = QPixmap(18, 18)
        pm.fill(QColor(0, 0, 0, 0))
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor("#090a0f"), 2.6)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        p.drawPolyline([QPoint(4, 9), QPoint(7, 13), QPoint(14, 5)])
        p.end()
        pm.save(str(icon_path))
    return str(icon_path).replace("\\", "/")


class SettingsView(QWidget):
    back_requested = pyqtSignal()
    settings_changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.settings = load_settings()
        self.providers = load_providers()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(40, 24, 40, 40)
        layout.setSpacing(24)

        # Header
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

        title = QLabel("Settings")
        title.setStyleSheet("font-size: 22px; font-weight: 800; color: #ffffff;")
        header.addWidget(title)
        header.addStretch()
        layout.addLayout(header)

        # Card 1: Streaming Source / Provider
        card_prov = self._create_card("Streaming Provider")
        p_layout = QVBoxLayout(card_prov)
        p_layout.setContentsMargins(24, 20, 24, 20)
        p_layout.setSpacing(14)

        p_desc = QLabel(
            "Select the default stream resolver. Hayate comes with official channel YouTube resolution by default. "
            "Additional community providers can be added as .py plugins into your config folder."
        )
        p_desc.setStyleSheet("color: #94a3b8; font-size: 12px;")
        p_desc.setWordWrap(True)
        p_layout.addWidget(p_desc)

        row_prov = QHBoxLayout()
        lbl_select = QLabel("Active Provider:")
        lbl_select.setStyleSheet("color: #ffffff; font-weight: 700; font-size: 13px;")
        row_prov.addWidget(lbl_select)

        self.prov_combo = QComboBox()
        self.prov_combo.setFixedHeight(36)
        self.prov_combo.setMinimumWidth(260)
        self.prov_combo.setStyleSheet("""
            QComboBox {
                background-color: #212130;
                color: #ffffff;
                border: 1.5px solid #3d3d52;
                border-radius: 8px;
                padding: 0 12px;
                font-size: 13px;
                font-weight: 600;
            }
            QComboBox:hover { border-color: #38bdf8; }
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
        self.prov_combo.currentIndexChanged.connect(self._on_provider_changed)
        row_prov.addWidget(self.prov_combo)

        self.reload_btn = QPushButton("↻ Refresh")
        self.reload_btn.setFixedHeight(36)
        self.reload_btn.setFixedWidth(115)
        self.reload_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.reload_btn.setStyleSheet("""
            QPushButton {
                background-color: #212130;
                color: #38bdf8;
                border: 1.5px solid #38bdf8;
                border-radius: 8px;
                font-weight: 600;
                font-size: 12px;
            }
            QPushButton:hover { background-color: #38bdf8; color: #090a0f; }
            QPushButton:pressed { background-color: #0284c7; color: #ffffff; border-color: #0284c7; }
        """)
        self.reload_btn.clicked.connect(self._on_refresh_clicked)
        row_prov.addWidget(self.reload_btn)

        open_folder_btn = QPushButton("📁 Open Plugins Folder")
        open_folder_btn.setFixedHeight(36)
        open_folder_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        open_folder_btn.setStyleSheet("""
            QPushButton {
                background-color: #212130;
                color: #94a3b8;
                border: 1.5px solid #3d3d52;
                border-radius: 8px;
                padding: 0 14px;
                font-weight: 600;
                font-size: 12px;
            }
            QPushButton:hover { background-color: #2d2d42; color: #ffffff; }
        """)
        open_folder_btn.clicked.connect(self._open_plugins_folder)
        row_prov.addWidget(open_folder_btn)
        row_prov.addStretch()

        p_layout.addLayout(row_prov)
        layout.addWidget(card_prov)

        # Card 2: Content Filtering (18+ / NSFW)
        card_content = self._create_card("Content & Safety Filters")
        c_layout = QVBoxLayout(card_content)
        c_layout.setContentsMargins(24, 20, 24, 20)
        c_layout.setSpacing(12)

        desc = QLabel("Control whether 18+, NSFW, and Hentai metadata are displayed across catalog searches and shelves.")
        desc.setStyleSheet("color: #94a3b8; font-size: 12px;")
        desc.setWordWrap(True)
        c_layout.addWidget(desc)

        check_icon_file = _ensure_check_icon()
        self.adult_checkbox = QCheckBox("Enable 18+ and NSFW Content")
        self.adult_checkbox.setCursor(Qt.CursorShape.PointingHandCursor)
        self.adult_checkbox.setChecked(bool(self.settings.get("allow_adult", False)))
        self.adult_checkbox.setStyleSheet(f"""
            QCheckBox {{
                color: #ffffff;
                font-size: 13px;
                font-weight: 700;
            }}
            QCheckBox::indicator {{
                width: 18px;
                height: 18px;
                border-radius: 4px;
                border: 1.5px solid #4a4a68;
                background: #242436;
            }}
            QCheckBox::indicator:checked {{
                background-color: #38bdf8;
                border: 1.5px solid #38bdf8;
                image: url('{check_icon_file}');
            }}
        """)
        self.adult_checkbox.clicked.connect(self._on_adult_toggled)
        c_layout.addWidget(self.adult_checkbox)
        layout.addWidget(card_content)

        # Banner transition card
        card_tr = self._create_card("Banner transition")
        tr_layout = card_tr.layout() or QVBoxLayout(card_tr)
        tr_row = QHBoxLayout()
        tr_row.addWidget(QLabel("Style:"))
        self.trans_combo = QComboBox()
        self.trans_combo.setStyleSheet(self.prov_combo.styleSheet())
        for label, key in (("Slide (original)", "slide"), ("Wipe (directional)", "wipe"),
                           ("Circle grow", "grow"), ("Circle shrink (outer)", "outer"),
                           ("Circle from center", "center"), ("Fade", "fade")):
            self.trans_combo.addItem(label, key)
        i = self.trans_combo.findData(load_settings().get("banner_transition", "slide"))
        self.trans_combo.setCurrentIndex(max(i, 0))
        self.trans_combo.currentIndexChanged.connect(self._on_transition_changed)
        tr_row.addWidget(self.trans_combo)
        tr_row.addStretch()
        tr_layout.addLayout(tr_row)

        dur_row = QHBoxLayout()
        dur_row.addWidget(QLabel("Duration:"))
        self.dur_slider = QSlider(Qt.Orientation.Horizontal)
        self.dur_slider.setRange(100, 1500)
        self.dur_slider.setPageStep(100)
        self.dur_slider.setValue(int(load_settings().get("banner_transition_ms", 550)))
        self.dur_lbl = QLabel(f"{self.dur_slider.value()} ms")
        self.dur_lbl.setMinimumWidth(60)
        self.dur_slider.valueChanged.connect(self._on_duration_changed)
        dur_row.addWidget(self.dur_slider, 1)
        dur_row.addWidget(self.dur_lbl)
        tr_layout.addLayout(dur_row)

        pg_row = QHBoxLayout()
        pg_row.addWidget(QLabel("Page transition:"))
        self.page_combo = QComboBox()
        self.page_combo.setStyleSheet(self.prov_combo.styleSheet())
        for label, key in (("Fade", "fade"), ("Slide", "slide"), ("Zoom", "zoom"), ("None", "none")):
            self.page_combo.addItem(label, key)
        self.page_combo.setCurrentIndex(max(self.page_combo.findData(load_settings().get("page_transition", "fade")), 0))
        self.page_combo.currentIndexChanged.connect(self._on_page_style_changed)
        pg_row.addWidget(self.page_combo)
        pg_row.addStretch()
        tr_layout.addLayout(pg_row)

        pd_row = QHBoxLayout()
        pd_row.addWidget(QLabel("Page duration:"))
        self.page_slider = QSlider(Qt.Orientation.Horizontal)
        self.page_slider.setRange(100, 800)
        self.page_slider.setPageStep(50)
        self.page_slider.setValue(int(load_settings().get("page_transition_ms", 260)))
        self.page_lbl = QLabel(f"{self.page_slider.value()} ms")
        self.page_slider.valueChanged.connect(self._on_page_dur_changed)
        pd_row.addWidget(self.page_slider, 1)
        pd_row.addWidget(self.page_lbl)
        tr_layout.addLayout(pd_row)

        bl_row = QHBoxLayout()
        bl_row.addWidget(QLabel("Transition blur:"))
        self.blur_slider = QSlider(Qt.Orientation.Horizontal)
        self.blur_slider.setRange(0, 100)
        self.blur_slider.setValue(int(load_settings().get("transition_blur", 60)))
        self.blur_lbl = QLabel(f"{self.blur_slider.value()}%")
        self.blur_slider.valueChanged.connect(self._on_blur_changed)
        bl_row.addWidget(self.blur_slider, 1)
        bl_row.addWidget(self.blur_lbl)
        tr_layout.addLayout(bl_row)

        # match the other cards: padding, label and slider styling
        tr_layout.setContentsMargins(25, 20, 25, 20)
        tr_layout.setSpacing(12)
        _desc = QLabel("Animations for the home banner and for switching between pages.")
        _desc.setStyleSheet("color: #94a3b8; font-size: 12px; border: none; background: transparent;")
        tr_layout.insertWidget(0, _desc)
        for _l in (card_tr.findChildren(QLabel)):
            if _l is _desc:
                continue
            _val = _l in (self.dur_lbl, self.page_lbl, self.blur_lbl)
            _l.setMinimumWidth(64 if _val else 0)
            _l.setStyleSheet("color: %s; font-weight: 700; font-size: 13px; border: none; background: transparent;"
                             % ("#38bdf8" if _val else "#ffffff"))
        for _s in (self.dur_slider, self.page_slider, self.blur_slider):
            _s.setStyleSheet("""
                QSlider::groove:horizontal { height: 4px; background: #2f2f44; border-radius: 2px; }
                QSlider::sub-page:horizontal { background: #38bdf8; border-radius: 2px; }
                QSlider::handle:horizontal { background: #38bdf8; width: 14px; height: 14px; margin: -5px 0; border-radius: 7px; }
            """)
        layout.addWidget(card_tr)

        # Card 3: Watch History & Backup
        card_hist = self._create_card("Watch History Management")
        h_layout = QVBoxLayout(card_hist)
        h_layout.setContentsMargins(24, 20, 24, 20)
        h_layout.setSpacing(14)

        hist_desc = QLabel("Export or import your on-device watch progress via a portable JSON backup file.")
        hist_desc.setStyleSheet("color: #94a3b8; font-size: 12px;")
        h_layout.addWidget(hist_desc)

        h_btns = QHBoxLayout()
        export_btn = QPushButton("💾 Export History (JSON)")
        export_btn.setFixedHeight(36)
        export_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        export_btn.setStyleSheet("""
            QPushButton {
                background-color: #212130;
                color: #38bdf8;
                border: 1.5px solid #38bdf8;
                border-radius: 8px;
                padding: 0 16px;
                font-weight: 700;
            }
            QPushButton:hover { background-color: #38bdf8; color: #090a0f; }
        """)
        export_btn.clicked.connect(self._export_history)
        h_btns.addWidget(export_btn)

        import_btn = QPushButton("📂 Import History (JSON)")
        import_btn.setFixedHeight(36)
        import_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        import_btn.setStyleSheet("""
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
        import_btn.clicked.connect(self._import_history)
        h_btns.addWidget(import_btn)
        h_btns.addStretch()
        h_layout.addLayout(h_btns)
        layout.addWidget(card_hist)

        # Card 4: Storage & Cache Maintenance
        card_cache = self._create_card("Cache Maintenance")
        cache_layout = QVBoxLayout(card_cache)
        cache_layout.setContentsMargins(24, 20, 24, 20)
        cache_layout.setSpacing(14)

        cache_desc = QLabel("Poster thumbnails are cached locally on NVMe (75 MB LRU max). Clear the cache if you want to free disk space immediately.")
        cache_desc.setStyleSheet("color: #94a3b8; font-size: 12px;")
        cache_layout.addWidget(cache_desc)

        self.clear_btn = QPushButton("🗑 Clear Image Cache")
        self.clear_btn.setFixedHeight(36)
        self.clear_btn.setFixedWidth(180)
        self.clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.clear_btn.setStyleSheet("""
            QPushButton {
                background-color: #212130;
                color: #f87171;
                border: 1.5px solid #f87171;
                border-radius: 8px;
                font-weight: 700;
            }
            QPushButton:hover { background-color: #f87171; color: #090a0f; }
        """)
        self.clear_btn.clicked.connect(self._clear_cache)
        cache_layout.addWidget(self.clear_btn)
        layout.addWidget(card_cache)

        # Card 5: Local Network Sharing (LAN)
        card_lan = self._create_card("Local Network Sharing (LAN)")
        lan_layout = QVBoxLayout(card_lan)
        lan_layout.setContentsMargins(24, 20, 24, 20)
        lan_layout.setSpacing(14)

        lan_desc = QLabel(
            "Host your local anime and video folders over your home Wi-Fi/LAN, "
            "or open the dedicated player to browse and stream files."
        )
        lan_desc.setStyleSheet("color: #94a3b8; font-size: 12px;")
        lan_desc.setWordWrap(True)
        lan_layout.addWidget(lan_desc)


        # Server Toggle & Status Row
        row_server = QHBoxLayout()
        self.btn_toggle_server = QPushButton("Stop Server" if lan_server.is_running else "Start Server")
        self.btn_toggle_server.setFixedHeight(34)
        self._update_server_btn_style()
        self.btn_toggle_server.clicked.connect(self._toggle_lan_server)

        self._ip_visible = False
        self.lan_status_lbl = QLabel()
        self.lan_status_lbl.setStyleSheet("color: #38bdf8; font-size: 12px; font-weight: 600;")

        self.btn_toggle_ip = QPushButton("👁")
        self.btn_toggle_ip.setFixedSize(30, 30)
        self.btn_toggle_ip.setToolTip("Show/Hide IP")
        self.btn_toggle_ip.setStyleSheet("""
            QPushButton {
                background-color: #212130;
                color: #e2e8f0;
                border: 1px solid #3d3d52;
                border-radius: 6px;
                font-size: 13px;
            }
            QPushButton:hover { background-color: #2d2d42; }
        """)
        self.btn_toggle_ip.clicked.connect(self._toggle_ip_visibility)

        row_server.addWidget(self.btn_toggle_server)
        row_server.addWidget(self.btn_toggle_ip)
        row_server.addWidget(self.lan_status_lbl, 1)
        self._refresh_lan_status_text()
        lan_layout.addLayout(row_server)

        # Launch UI Interface Row
        # Launch UI Interface Row
        row_ui = QHBoxLayout()
        btn_launch_hub = QPushButton("Open LAN Host Manager")
        btn_launch_hub.setFixedHeight(36)
        btn_launch_hub.setStyleSheet("""
            QPushButton {
                background-color: #212130;
                color: #e2e8f0;
                border: 1px solid #3d3d52;
                border-radius: 6px;
                padding: 0 16px;
                font-size: 13px;
                font-weight: 600;
            }
            QPushButton:hover {
                background-color: #2d2d42;
                border-color: #38bdf8;
                color: #38bdf8;
            }
        """)
        btn_launch_hub.clicked.connect(self._open_lan_hub_window)
        row_ui.addWidget(btn_launch_hub)

        btn_launch_player = QPushButton("Open LAN Media Player")
        btn_launch_player.setFixedHeight(36)
        btn_launch_player.setStyleSheet("""
            QPushButton {
                background-color: #38bdf8;
                color: #090a0f;
                border: none;
                border-radius: 6px;
                padding: 0 18px;
                font-size: 13px;
                font-weight: 700;
            }
            QPushButton:hover { background-color: #7dd3fc; }
        """)
        btn_launch_player.clicked.connect(self._open_lan_player_window)
        row_ui.addWidget(btn_launch_player)
        row_ui.addStretch()
        lan_layout.addLayout(row_ui)

        layout.addWidget(card_lan)

        layout.addStretch()

        # Filesystem watcher for live dynamic reloading
        plugin_path = config_dir() / "providers"
        plugin_path.mkdir(parents=True, exist_ok=True)
        self.watcher = QFileSystemWatcher(self)
        self.watcher.addPath(str(plugin_path))
        self.watcher.directoryChanged.connect(self.refresh_providers)

        # Populate combo box initially
        self.refresh_providers()

    def _create_card(self, title_text: str) -> QFrame:
        card = QFrame()
        card.setObjectName("settings_card")
        card.setStyleSheet("""
            QFrame#settings_card {
                background-color: #1e1e2d;
                border-radius: 16px;
                border: 1.5px solid #2f2f44;
            }
        """)
        return card

    def _on_refresh_clicked(self):
        self.refresh_providers()
        self.reload_btn.setText("✓ Refreshed")
        self.reload_btn.setStyleSheet("""
            QPushButton {
                background-color: #052e16;
                color: #4ade80;
                border: 1.5px solid #22c55e;
                border-radius: 8px;
                font-weight: 700;
                font-size: 12px;
            }
        """)

        def _reset():
            self.reload_btn.setText("↻ Refresh")
            self.reload_btn.setStyleSheet("""
                QPushButton {
                    background-color: #212130;
                    color: #38bdf8;
                    border: 1.5px solid #38bdf8;
                    border-radius: 8px;
                    font-weight: 600;
                    font-size: 12px;
                }
                QPushButton:hover { background-color: #38bdf8; color: #090a0f; }
                QPushButton:pressed { background-color: #0284c7; color: #ffffff; border-color: #0284c7; }
            """)

        QTimer.singleShot(1200, _reset)

    def refresh_providers(self):
        """Reloads providers from disk and syncs the dropdown without restarting."""
        self.providers = load_providers()
        current_key = self.settings.get("provider", "ytdlp")

        self.prov_combo.blockSignals(True)
        self.prov_combo.clear()

        # Filter out internal pseudo provider from user selection
        real_providers = {k: v for k, v in self.providers.items() if k != "pseudo"}

        if not real_providers:
            self.prov_combo.addItem("No Provider Installed (none)", "none")
            active_idx = 0
        else:
            active_idx = 0
            for idx, (key, prov) in enumerate(real_providers.items()):
                lbl = getattr(prov, "label", key)
                self.prov_combo.addItem(f"{lbl} ({key})", key)
                if key == current_key:
                    active_idx = idx
            if current_key not in real_providers:
                active_idx = 0
            self.settings["provider"] = "ytdlp"
            save_settings(self.settings)

        self.prov_combo.setCurrentIndex(active_idx)
        self.prov_combo.blockSignals(False)

    def _on_transition_changed(self, idx: int):
        d = load_settings()
        d["banner_transition"] = self.trans_combo.itemData(idx)
        save_settings(d)

    def _on_duration_changed(self, val: int):
        val = round(val / 50) * 50
        self.dur_lbl.setText(f"{val} ms")
        d = load_settings()
        d["banner_transition_ms"] = val
        save_settings(d)

    def _on_page_style_changed(self, idx: int):
        d = load_settings()
        d["page_transition"] = self.page_combo.itemData(idx)
        save_settings(d)

    def _on_page_dur_changed(self, val: int):
        val = round(val / 20) * 20
        self.page_lbl.setText(f"{val} ms")
        d = load_settings()
        d["page_transition_ms"] = val
        save_settings(d)

    def _on_blur_changed(self, val: int):
        self.blur_lbl.setText(f"{val}%")
        d = load_settings()
        d["transition_blur"] = int(val)
        save_settings(d)

    def _on_provider_changed(self, idx: int):
        prov_key = self.prov_combo.itemData(idx)
        if prov_key:
            self.settings["provider"] = prov_key
            save_settings(self.settings)
            self.settings_changed.emit()

    def _open_plugins_folder(self):
        p_dir = config_dir() / "providers"
        p_dir.mkdir(parents=True, exist_ok=True)
        try:
            subprocess.Popen(["xdg-open", str(p_dir)])
        except Exception:
            QMessageBox.information(self, "Plugin Folder", f"Plugin directory is located at:\n{p_dir}")

    def _on_adult_toggled(self):
        wants_enable = self.adult_checkbox.isChecked()
        if wants_enable:
            reply = QMessageBox.question(
                self,
                "Age Verification Required",
                "Are you 18 years of age or older?\n\n"
                "Enabling this option will show 18+ and adult content across search and catalogs.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.Yes:
                self.settings["allow_adult"] = True
                save_settings(self.settings)
                Path(cache_dir() / "schedule_cache.json").unlink(missing_ok=True)
                self.settings_changed.emit()
            else:
                self.adult_checkbox.setChecked(False)
        else:
            self.settings["allow_adult"] = False
            save_settings(self.settings)
            Path(cache_dir() / "schedule_cache.json").unlink(missing_ok=True)
            self.settings_changed.emit()

    def _export_history(self):
        hist_path = history_mgr.file_path
        if not hist_path.exists():
            QMessageBox.information(self, "Export Notice", "No watch history recorded yet.")
            return

        file_dest, _ = QFileDialog.getSaveFileName(
            self, "Export Watch History", "hayate_history_backup.json", "JSON Files (*.json)"
        )
        if file_dest:
            try:
                data = hist_path.read_text(encoding="utf-8")
                Path(file_dest).write_text(data, encoding="utf-8")
                QMessageBox.information(self, "Export Complete", f"Watch history exported successfully to:\n{file_dest}")
            except Exception as e:
                QMessageBox.warning(self, "Export Error", f"Failed to export history: {e}")

    def _import_history(self):
        file_src, _ = QFileDialog.getOpenFileName(
            self, "Import Watch History Backup", "", "JSON Files (*.json)"
        )
        if file_src:
            try:
                content = Path(file_src).read_text(encoding="utf-8")
                parsed = json.loads(content)
                if isinstance(parsed, dict):
                    history_mgr.data = parsed
                    history_mgr._save()
                    QMessageBox.information(self, "Import Complete", "Watch history restored successfully!")
                    self.settings_changed.emit()
                else:
                    QMessageBox.warning(self, "Invalid Backup", "The selected JSON file is not a valid Hayate history backup.")
            except Exception as e:
                QMessageBox.warning(self, "Import Error", f"Failed to read backup: {e}")

    def _clear_cache(self, *args):
        try:
            image_mgr.clear_cache()
            if hasattr(self, "clear_btn"):
                self.clear_btn.setText("✓ Cleared")
                self.clear_btn.setStyleSheet("""
                    QPushButton {
                        background-color: #052e16;
                        color: #4ade80;
                        border: 1.5px solid #22c55e;
                        border-radius: 8px;
                        font-weight: 700;
                    }
                """)
                def _reset():
                    self.clear_btn.setText("🗑 Clear Image Cache")
                    self.clear_btn.setStyleSheet("""
                        QPushButton {
                            background-color: #212130;
                            color: #f87171;
                            border: 1.5px solid #f87171;
                            border-radius: 8px;
                            font-weight: 700;
                        }
                        QPushButton:hover { background-color: #f87171; color: #090a0f; }
                    """)
                QTimer.singleShot(1500, _reset)
        except Exception as e:
            QMessageBox.warning(self, "Cache Error", f"Failed to clear cache: {e}")

    def _update_server_btn_style(self):
        if lan_server.is_running:
            self.btn_toggle_server.setStyleSheet("""
                QPushButton {
                    background-color: #ef4444;
                    color: #ffffff;
                    border: none;
                    border-radius: 6px;
                    padding: 0 14px;
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
                    padding: 0 14px;
                    font-weight: 700;
                }
                QPushButton:hover { background-color: #059669; }
            """)

    def _choose_lan_dir(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Video Folder to Share")
        if folder:
            self.lan_chosen_dir = Path(folder)
            self.lan_dir_lbl.setText(str(self.lan_chosen_dir))

    def _toggle_lan_server(self):
        saved = self.settings.get("lan_folder")
        if saved and Path(saved).is_dir():
            target = Path(saved)
        elif lan_server.serving_dir and Path(lan_server.serving_dir).is_dir():
            target = Path(lan_server.serving_dir)
        else:
            dl = Path.home() / "Downloads"
            target = dl if dl.is_dir() else Path.home()
        lan_server.serving_dir = target

        if lan_server.is_running:
            lan_server.stop()
            self.btn_toggle_server.setText("Start Server")
            self._refresh_lan_status_text()
        else:
            lan_server.start(target)
            self.btn_toggle_server.setText("Stop Server")
            self._refresh_lan_status_text()

        self._update_server_btn_style()

    def _open_lan_hub_window(self):
        hub = LocalHubWindow(self)
        hub.showMaximized()
        hub.exec()
        if lan_server.is_running:
            self.btn_toggle_server.setText("Stop Server")
            self._refresh_lan_status_text()
        else:
            self.btn_toggle_server.setText("Start Server")
            self._refresh_lan_status_text()
        self._update_server_btn_style()

    def _toggle_ip_visibility(self):
        self._ip_visible = not getattr(self, "_ip_visible", False)
        self.btn_toggle_ip.setText("🙈" if self._ip_visible else "👁")
        self._refresh_lan_status_text()

    def _refresh_lan_status_text(self):
        ip = get_local_ip() if getattr(self, "_ip_visible", False) else "•••.•••.•••.•••"
        if lan_server.is_running:
            self.lan_status_lbl.setText(f"Hosting at http://{ip}:{lan_server.port}")
        else:
            self.lan_status_lbl.setText(f"LAN IP: {ip} (Inactive)")

    def _open_lan_player_window(self):
        player = LANPlayerWindow(self)
        player.showMaximized()
        player.exec()
