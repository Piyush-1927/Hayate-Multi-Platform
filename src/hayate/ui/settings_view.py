from hayate.core.assets import ic as _ic
import json
import subprocess
import os
import sys
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
    QScrollArea,
)

from hayate.core.history import history_mgr
from hayate.core.lan_server import get_local_ip, lan_server
from hayate.ui.lan_hub import LocalHubWindow
from hayate.ui.lan_player import LANPlayerWindow
from hayate.core.image_cache import image_mgr
from hayate.core.settings import config_dir, load_settings, save_settings, cache_dir
from PyQt6.QtWidgets import QSlider
from hayate.providers import load_providers, provider_label
from hayate.core.dialogs import dlg_open, dlg_save


from hayate.core.theme import theme

def _orig_ss(w):
    from hayate.core.theme import _tracked
    return _tracked.get(w) or w.styleSheet()



def _ensure_check_icon() -> str:
    icon_path = cache_dir() / "check.png"
    if not icon_path.exists():
        pm = QPixmap(18, 18)
        pm.fill(QColor(0, 0, 0, 0))
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(theme.c("text_on_accent"), 2.6)
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
    cloud_synced = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.settings = load_settings()
        self.providers = load_providers()

        # Keep the full Settings page accessible on short windows and
        # at increased display scaling.
        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(0)

        scroll_area = QScrollArea(self)
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        scroll_area.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        scroll_area.setStyleSheet("""
            QScrollArea {
                border: none;
                background-color: #171722;
            }
            QScrollArea QWidget#qt_scrollarea_viewport {
                background-color: #171722;
                border: none;
            }
        """)

        content = QWidget()
        content.setObjectName("settings_content")
        content.setStyleSheet(
            "QWidget#settings_content { background-color: #171722; border: none; }"
        )
        layout = QVBoxLayout(content)
        layout.setContentsMargins(40, 24, 40, 24)
        layout.setSpacing(20)

        scroll_area.setWidget(content)
        outer_layout.addWidget(scroll_area)

        # Header
        header = QHBoxLayout()
        back_btn = _ic(QPushButton("Back to Home"), "arrow-left")
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

        self.reload_btn = _ic(QPushButton("Refresh"), "refresh-cw")
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

        open_folder_btn = _ic(QPushButton("Open Plugins Folder"), "folder-open")
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
        imp_btn = _ic(QPushButton("Import Provider…"), "download")
        imp_btn.setFixedHeight(36)
        imp_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        from hayate.core import theme as _th
        imp_btn.setStyleSheet(_th._tracked.get(open_folder_btn) or open_folder_btn.styleSheet())
        imp_btn.clicked.connect(self._import_provider)
        row_prov.addWidget(imp_btn)
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

        # Catalogue card
        card_cat = self._create_card("Catalogue")
        cat_layout = QVBoxLayout(card_cat)
        cat_layout.setContentsMargins(24, 20, 24, 20); cat_layout.setSpacing(12)
        cat_desc = QLabel("When on, Catalogue search and genre filters combine (results must match both). "
                          "When off, using one clears the other.")
        cat_desc.setStyleSheet("color: #94a3b8; font-size: 12px;"); cat_desc.setWordWrap(True)
        cat_layout.addWidget(cat_desc)
        self.cat_link_check = QCheckBox("Combine search and filters")
        self.cat_link_check.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cat_link_check.setChecked(bool(load_settings().get("catalogue_link", True)))
        self.cat_link_check.setStyleSheet(_orig_ss(self.adult_checkbox))
        self.cat_link_check.toggled.connect(self._on_cat_link_toggled)
        cat_layout.addWidget(self.cat_link_check)
        layout.addWidget(card_cat)

        # Banner transition card
        card_tr = self._create_card("Banner transition")
        tr_layout = card_tr.layout() or QVBoxLayout(card_tr)
        tr_row = QHBoxLayout()
        tr_row.addWidget(QLabel("Style:"))
        self.trans_combo = QComboBox()
        self.trans_combo.setStyleSheet(_orig_ss(self.prov_combo))
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
        self.page_combo.setStyleSheet(_orig_ss(self.prov_combo))
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

        from hayate.core.theme import AppearanceLauncherCard
        layout.addWidget(AppearanceLauncherCard())
        # Card: Cache
        from hayate.ui.cache_slider import ClickOnlySlider
        card_cache = self._create_card("Cache")
        cc = QVBoxLayout(card_cache)
        cc.setContentsMargins(25, 20, 25, 20); cc.setSpacing(12)
        _cd = QLabel("Poster cache on disk. Bigger means fewer re-downloads; the oldest posters are removed when the limit is reached.")
        _cd.setWordWrap(True)
        _cd.setStyleSheet("color: #94a3b8; font-size: 12px; border: none; background: transparent;")
        cc.addWidget(_cd)
        _cset = load_settings()
        self.cache_check = QCheckBox("Enable cache")
        self.cache_check.setChecked(bool(_cset.get("cache_enabled", True)))
        self.cache_check.setStyleSheet(_orig_ss(self.adult_checkbox))
        cc.addWidget(self.cache_check)
        cs_row = QHBoxLayout()
        _cl = QLabel("Cache size:")
        _cl.setStyleSheet("color: #ffffff; font-weight: 700; font-size: 13px; border: none; background: transparent;")
        cs_row.addWidget(_cl)
        self.cache_slider = ClickOnlySlider(25)
        self.cache_slider.setRange(25, 2000)
        self.cache_slider.setValue(int(_cset.get("cache_mb", 300)))
        self.cache_slider.setStyleSheet(_orig_ss(self.blur_slider))
        self.cache_lbl = QLabel(f"{self.cache_slider.value()} MB")
        self.cache_lbl.setMinimumWidth(72)
        self.cache_lbl.setStyleSheet("color: #38bdf8; font-weight: 700; font-size: 13px; border: none; background: transparent;")
        self.cache_slider.valueChanged.connect(lambda v: self.cache_lbl.setText(f"{v} MB"))
        self.cache_slider.committed.connect(self._on_cache_commit)
        self.cache_check.toggled.connect(self._on_cache_toggled)
        self.cache_slider.setEnabled(self.cache_check.isChecked())
        cs_row.addWidget(self.cache_slider, 1)
        cs_row.addWidget(self.cache_lbl)
        cc.addLayout(cs_row)
        layout.addWidget(card_cache)

        # Card 3: Watch History & Backup
        card_hist = self._create_card("Watch History Management")
        h_layout = QVBoxLayout(card_hist)
        h_layout.setContentsMargins(24, 20, 24, 20)
        h_layout.setSpacing(14)

        hist_desc = QLabel("Export or import your on-device watch progress via a portable JSON backup file.")
        hist_desc.setStyleSheet("color: #94a3b8; font-size: 12px;")
        h_layout.addWidget(hist_desc)

        h_btns = QHBoxLayout()
        export_btn = _ic(QPushButton("Export History (JSON)"), "save")
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

        import_btn = _ic(QPushButton("Import History (JSON)"), "folder-open")
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

        _acc = ("QPushButton { background-color: #212130; color: #38bdf8; border: 1.5px solid #38bdf8;"
                " border-radius: 8px; padding: 0 16px; font-weight: 700; }"
                "QPushButton:hover { background-color: #38bdf8; color: #090a0f; }")
        _plain = ("QPushButton { background-color: #212130; color: #e2e8f0; border: 1.5px solid #3d3d52;"
                  " border-radius: 8px; padding: 0 16px; font-weight: 600; }"
                  "QPushButton:hover { background-color: #2d2d42; }")
        h_btns2 = QHBoxLayout()
        for _txt, _slot, _qss in (("📤 Export Library (JSON)", self._export_library, _acc),
                                  ("📂 Import Library (JSON)", self._import_library, _plain)):
            _b = QPushButton(_txt)
            _b.setFixedHeight(36)
            _b.setCursor(Qt.CursorShape.PointingHandCursor)
            _b.setStyleSheet(_qss)
            _b.clicked.connect(_slot)
            h_btns2.addWidget(_b)
        h_btns2.addStretch()
        h_layout.addLayout(h_btns2)
        layout.addWidget(card_hist)

        # Card: Default Audio
        from PyQt6.QtWidgets import QLineEdit
        self.card_audio = self._create_card("Default Audio")
        a_layout = QVBoxLayout(self.card_audio)
        a_layout.setContentsMargins(24, 20, 24, 20)
        a_layout.setSpacing(10)
        a_desc = QLabel("Audio preselected for every anime you haven't picked one for. Add your own dub "
                        "language (e.g. Tamil, Spanish) to get an extra entry in the audio dropdowns.")
        a_desc.setWordWrap(True)
        a_desc.setStyleSheet("color: #94a3b8; font-size: 12px;")
        a_layout.addWidget(a_desc)
        self.def_audio_combo = QComboBox()
        self.def_audio_combo.setFixedHeight(34)
        self.def_audio_combo.setStyleSheet(_orig_ss(self.prov_combo))
        a_layout.addWidget(self.def_audio_combo)
        self.custom_dub_edit = QLineEdit(self.settings.get("custom_dub", ""))
        self.custom_dub_edit.setPlaceholderText("Custom dub language, e.g. Tamil")
        a_layout.addWidget(self.custom_dub_edit)
        self._fill_def_audio()
        self.def_audio_combo.currentIndexChanged.connect(self._on_def_audio_changed)
        self.custom_dub_edit.editingFinished.connect(self._on_custom_dub_edited)
        layout.addWidget(self.card_audio)

        # Card: Cloud Sync (bring your own cloud)
        from PyQt6.QtWidgets import QLineEdit
        from hayate.core import cloud as _cloud
        self._cloud = _cloud.get_runner()
        self._cloud.done.connect(self._on_cloud_done)
        self._gsign = _cloud.GoogleSignIn(self)
        self._gsign.finished.connect(self._on_google_signin)
        _ccfg = _cloud.load_cfg()
        self.card_cloud = self._create_card("Cloud Sync")
        c_layout = QVBoxLayout(self.card_cloud)
        c_layout.setContentsMargins(24, 20, 24, 20)
        c_layout.setSpacing(10)
        c_desc = QLabel("Sync watch history and library to your own cloud: Google Drive (Hayate only gets its "
                        "own hidden app folder, never your other files) or any WebDAV server such as Nextcloud. "
                        "Every sync merges both sides, newest wins. Removing an entry on one device does not "
                        "remove it on others.")
        c_desc.setWordWrap(True)
        c_desc.setStyleSheet("color: #94a3b8; font-size: 12px;")
        c_layout.addWidget(c_desc)
        self.cloud_combo = QComboBox()
        self.cloud_combo.setFixedHeight(34)
        self.cloud_combo.setStyleSheet(_orig_ss(self.prov_combo))
        self.cloud_combo.addItem("Off", "off")
        self.cloud_combo.addItem("Google Drive", "google")
        self.cloud_combo.addItem("WebDAV (Nextcloud, ownCloud, ...)", "webdav")
        self.cloud_combo.setCurrentIndex(max(self.cloud_combo.findData(_ccfg.get("backend", "off")), 0))
        c_layout.addWidget(self.cloud_combo)
        self.g_status = QLabel("")
        self.g_status.setWordWrap(True)
        self.g_status.setStyleSheet("color: #cbd5e1; font-size: 13px;")
        c_layout.addWidget(self.g_status)
        self.g_btn = QPushButton("Sign in with Google")
        self.g_btn.setFixedHeight(36)
        self.g_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        c_layout.addWidget(self.g_btn)
        self.dav_url = QLineEdit(_ccfg.get("webdav_url", ""))
        self.dav_url.setPlaceholderText("https://cloud.example.com/remote.php/dav/files/you/Hayate")
        self.dav_user = QLineEdit(_ccfg.get("webdav_user", ""))
        self.dav_user.setPlaceholderText("Username")
        self.dav_pass = QLineEdit(_ccfg.get("webdav_pass", ""))
        self.dav_pass.setPlaceholderText("Password / app password")
        self.dav_pass.setEchoMode(QLineEdit.EchoMode.Password)
        for _w in (self.dav_url, self.dav_user, self.dav_pass):
            c_layout.addWidget(_w)
            _w.editingFinished.connect(self._on_cloud_changed)
        self.cloud_sync_btn = QPushButton("Sync now")
        self.cloud_sync_btn.setFixedHeight(36)
        self.cloud_sync_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        try:
            self.cloud_sync_btn.setStyleSheet(_orig_ss(export_btn))
            self.g_btn.setStyleSheet(_orig_ss(export_btn))
        except NameError:
            pass
        c_layout.addWidget(self.cloud_sync_btn)
        self.cloud_auto = QCheckBox("Sync automatically (startup, episode start and close)")
        self.cloud_auto.setChecked(bool(_ccfg.get("auto", True)))
        self.cloud_auto.setStyleSheet(_orig_ss(self.adult_checkbox))
        self.cloud_auto.toggled.connect(self._on_cloud_changed)
        c_layout.addWidget(self.cloud_auto)
        self.cloud_status = QLabel("")
        self.cloud_status.setWordWrap(True)
        self.cloud_status.setStyleSheet("color: #94a3b8; font-size: 12px;")
        c_layout.addWidget(self.cloud_status)
        self.cloud_status.hide()
        self.cloud_combo.currentIndexChanged.connect(self._on_cloud_changed)
        self.g_btn.clicked.connect(self._on_google_btn)
        self.cloud_sync_btn.clicked.connect(self._cloud_sync_now)
        self._on_cloud_changed()
        layout.addWidget(self.card_cloud)

        # Card: Network & Privacy (always shown; the Tor toggle only appears if Tor is available)
        from PyQt6.QtWidgets import QLineEdit
        self.card_net = self._create_card("Network & Privacy")
        n_layout = QVBoxLayout(self.card_net)
        n_layout.setContentsMargins(24, 20, 24, 20)
        n_layout.setSpacing(10)
        self.net_desc = QLabel("Route metadata, search, posters, stream lookups and playback through a proxy. "
                               "Devices on your LAN always connect directly. Restart Hayate after changing this. "
                               "With a socks5h:// proxy or Tor, the proxy resolves DNS itself.")
        self.net_desc.setWordWrap(True)
        self.net_desc.setStyleSheet("color: #94a3b8; font-size: 12px;")
        n_layout.addWidget(self.net_desc)
        self.dns_check = QCheckBox("Private DNS (DNS-over-HTTPS)")
        self.dns_check.setStyleSheet(_orig_ss(self.adult_checkbox))
        self.dns_check.toggled.connect(self._on_dns_toggled)
        n_layout.addWidget(self.dns_check)
        self.proxy_check = QCheckBox("Use custom proxy")
        self.proxy_check.setStyleSheet(_orig_ss(self.adult_checkbox))
        self.proxy_check.toggled.connect(self._on_proxy_toggled)
        n_layout.addWidget(self.proxy_check)
        self.proxy_edit = QLineEdit(self.settings.get("proxy_url", ""))
        self.proxy_edit.setPlaceholderText("socks5h://127.0.0.1:1080   or   http://host:8080")
        self.proxy_edit.editingFinished.connect(self._on_proxy_edited)
        n_layout.addWidget(self.proxy_edit)
        self.tor_check = QCheckBox("Route traffic through Tor")
        self.tor_check.setStyleSheet(_orig_ss(self.adult_checkbox))
        self.tor_check.toggled.connect(self._on_tor_toggled)
        n_layout.addWidget(self.tor_check)
        self.net_status = QLabel("")
        self.net_status.setWordWrap(True)
        n_layout.addWidget(self.net_status)
        layout.addWidget(self.card_net)
        self._refresh_network()

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

        self.btn_toggle_ip = _ic(QPushButton(""), "eye")
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


        # Filesystem watcher for live dynamic reloading
        plugin_path = config_dir() / "providers"
        plugin_path.mkdir(parents=True, exist_ok=True)
        self.watcher = QFileSystemWatcher(self)
        self.watcher.addPath(str(plugin_path))
        self.watcher.directoryChanged.connect(self.refresh_providers)

        # Populate combo box initially
        self.refresh_providers()
        self._lock_wheel()
        from hayate.ui.infobtn import polish_settings
        polish_settings(self)

    def _lock_wheel(self):
        from PyQt6.QtWidgets import QSlider, QComboBox, QAbstractSpinBox
        for w in self.findChildren((QSlider, QComboBox, QAbstractSpinBox)):
            w.installEventFilter(self)
            w.setFocusPolicy(Qt.FocusPolicy.ClickFocus)

    def eventFilter(self, obj, ev):
        from PyQt6.QtCore import QEvent
        from PyQt6.QtWidgets import QScrollArea
        if ev.type() == QEvent.Type.Wheel:
            sa = self.findChild(QScrollArea)
            if sa is not None:
                dy = ev.pixelDelta().y() or ev.angleDelta().y() // 2
                if abs(dy) >= abs(ev.pixelDelta().x() or ev.angleDelta().x()):
                    sb = sa.verticalScrollBar()
                    sb.setValue(sb.value() - dy)
            return True
        return super().eventFilter(obj, ev)

    def _on_cache_toggled(self, on):
        from hayate.core.image_cache import apply_cache_settings
        s = load_settings(); s["cache_enabled"] = bool(on); save_settings(s)
        self.cache_slider.setEnabled(bool(on))
        apply_cache_settings()

    def _on_cache_commit(self, mb):
        from hayate.core.image_cache import apply_cache_settings
        s = load_settings(); s["cache_mb"] = int(mb); save_settings(s)
        apply_cache_settings()

    def _fill_def_audio(self):
        from hayate.core.settings import audio_options
        self.def_audio_combo.blockSignals(True)
        self.def_audio_combo.clear()
        for label, data in audio_options(self.settings):
            self.def_audio_combo.addItem(label, data)
        i = self.def_audio_combo.findData(self.settings.get("preferred_audio", "sub"))
        self.def_audio_combo.setCurrentIndex(max(i, 0))
        self.def_audio_combo.blockSignals(False)

    def _on_def_audio_changed(self, idx):
        from hayate.core.settings import load_settings, save_settings
        val = self.def_audio_combo.itemData(idx)
        if val is None:
            return
        self.settings["preferred_audio"] = val
        st = load_settings()
        st["preferred_audio"] = val
        save_settings(st)

    def _on_custom_dub_edited(self):
        from hayate.core.settings import load_settings, save_settings
        name = self.custom_dub_edit.text().strip()
        if name == (self.settings.get("custom_dub") or ""):
            return
        st = load_settings()
        st["custom_dub"] = name
        if str(st.get("preferred_audio", "")).startswith("custom:"):
            st["preferred_audio"] = f"custom:{name}" if name else "sub"
        save_settings(st)
        self.settings["custom_dub"] = name
        self.settings["preferred_audio"] = st["preferred_audio"]
        self._fill_def_audio()

    def _on_cloud_changed(self, *_):
        from hayate.core import cloud
        c = cloud.load_cfg()
        b = self.cloud_combo.currentData() or "off"
        c["backend"] = b
        c["webdav_url"] = self.dav_url.text().strip()
        c["webdav_user"] = self.dav_user.text().strip()
        c["webdav_pass"] = self.dav_pass.text()
        c["auto"] = self.cloud_auto.isChecked()
        cloud.save_cfg(c)
        signed = bool(c.get("google_refresh"))
        self.g_status.setVisible(b == "google")
        self.g_btn.setVisible(b == "google" and cloud.google_configured())
        if not cloud.google_configured():
            self.g_status.setText("Google sign-in isn't configured in this build. Use WebDAV, or add your own credentials (see google_secrets.py.example).")
        elif signed:
            self.g_status.setText(f"Signed in as {c.get('google_email') or 'your Google account'}")
        else:
            self.g_status.setText("Not signed in.")
        self.g_btn.setText("Sign out" if signed else "Sign in with Google")
        for w in (self.dav_url, self.dav_user, self.dav_pass):
            w.setVisible(b == "webdav")
        self.cloud_auto.setVisible(b != "off")
        ready = b == "webdav" or (b == "google" and signed)
        self.cloud_sync_btn.setVisible(ready)
        self.cloud_sync_btn.setEnabled(ready)

    def _on_google_btn(self):
        from hayate.core import cloud
        if cloud.load_cfg().get("google_refresh"):
            cloud.google_sign_out()
            cloud.unlink()
            self._on_cloud_changed()
            self._cloud_msg("Signed out of Google.")
            return
        if not cloud.google_configured():
            return
        self.g_btn.setEnabled(False)
        self._cloud_msg("Finish signing in in your browser...")
        self._gsign.start()

    def _on_google_signin(self, ok, msg):
        self.g_btn.setEnabled(True)
        self._cloud_msg("Signed in." if ok else msg)
        self._on_cloud_changed()
        if ok:
            self._cloud_link_flow()

    def _cloud_autosync(self):
        from hayate.core import cloud
        if cloud.needs_link():
            return
        c = cloud.load_cfg()
        if c.get("backend", "off") != "off" and c.get("auto", True):
            self._cloud_msg("Syncing...")
            self._cloud.start()

    def _cloud_link_flow(self):
        from hayate.core import cloud
        if getattr(self, "_probe", None) is None:
            self._probe = cloud.LinkProbe(self)
            self._probe.ready.connect(self._cloud_link_ready)
            self._probe.pushed.connect(lambda ok, msg: self._cloud_msg(msg))
        self._cloud_msg("Checking your cloud...")
        self.cloud_sync_btn.setEnabled(False)
        self._probe.start()

    def _cloud_link_ready(self, remote, err):
        from hayate.core import cloud
        self._on_cloud_changed()
        if err:
            self._cloud_msg(err)
            return
        if not (cloud.remote_has_data(remote) and cloud.local_has_data()):
            cloud.mark_linked()
            self._cloud_msg("Syncing...")
            self._cloud.start()
            return
        self._cloud_link_choose(remote)

    def _cloud_confirm(self, text) -> bool:
        from PyQt6.QtWidgets import QMessageBox
        R = QMessageBox.ButtonRole
        box = QMessageBox(self)
        box.setWindowTitle("Are you sure?")
        box.setText(text + "\n\nAre you sure?")
        yes = box.addButton("I'm sure", R.DestructiveRole)
        back = box.addButton("Go back", R.RejectRole)
        box.setDefaultButton(back)
        box.exec()
        return box.clickedButton() == yes

    def _cloud_link_choose(self, remote):
        from PyQt6.QtWidgets import QMessageBox
        from hayate.core import cloud
        R = QMessageBox.ButtonRole
        while True:
            box = QMessageBox(self)
            box.setWindowTitle("Connect cloud sync")
            box.setText("This device and your cloud both already have Hayate data.\n\nWhat should Hayate do?")
            b_merge = box.addButton("Merge both (recommended)", R.AcceptRole)
            b_up = box.addButton("Replace the cloud data with this device's", R.DestructiveRole)
            b_down = box.addButton("Replace this device's data with the cloud's", R.DestructiveRole)
            box.addButton("Not now", R.RejectRole)
            box.exec()
            c = box.clickedButton()
            if c == b_merge:
                cloud.mark_linked()
                self._cloud_msg("Merging...")
                self._cloud.start()
                return
            if c == b_up:
                if self._cloud_confirm("This replaces the history and library stored in your cloud with this device's data. "
                                       "Anything that exists only in the cloud will be lost. Other devices that are already "
                                       "connected may add their data back the next time they sync."):
                    cloud.mark_linked()
                    self._cloud_msg("Uploading...")
                    self._probe.push_local()
                    return
                continue
            if c == b_down:
                if self._cloud_confirm("This deletes the history and library on this device and replaces them with what is "
                                       "in your cloud. Anything that exists only on this device will be lost."):
                    try:
                        cloud.replace_local(remote)
                        cloud.mark_linked()
                        self._cloud_msg("This device now matches your cloud.")
                    except Exception as e:
                        self._cloud_msg(f"Could not apply the cloud data: {e}")
                    return
                continue
            self._cloud_msg("Cloud sync is paused until you choose. Press Sync now to continue.")
            return

    def _cloud_msg(self, text):
        self.cloud_status.setText(text)
        self.cloud_status.setVisible(bool(text))

    def _cloud_sync_now(self):
        self._on_cloud_changed()
        from hayate.core import cloud as _cl
        if _cl.needs_link():
            self._cloud_link_flow()
            return
        self._cloud_msg("Syncing...")
        self.cloud_sync_btn.setEnabled(False)
        self._cloud.start()

    def _on_cloud_done(self, ok, msg, changed):
        self._cloud_msg(msg)
        self.cloud_sync_btn.setEnabled(self.cloud_combo.currentData() != "off")
        if ok and changed:
            self.cloud_synced.emit()

    def _create_card(self, title_text: str) -> QFrame:
        card = QFrame()
        card.setObjectName("settings_card")
        card.setProperty("card", True)
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
            _ic(self.reload_btn, "refresh-cw", text="Refresh")
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
        fell_back = False

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
                fb = "ytdlp" if "ytdlp" in real_providers else next(iter(real_providers))
                active_idx = list(real_providers).index(fb)
                self.settings["provider"] = fb
                save_settings(self.settings)
                fell_back = True

        self.prov_combo.setCurrentIndex(active_idx)
        self.prov_combo.blockSignals(False)
        if fell_back:
            self.settings_changed.emit()

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
    def _import_provider(self):
        import shutil
        from pathlib import Path
        from PyQt6.QtWidgets import QMessageBox
        path, _ = dlg_open(self, "Import provider", "", "Python provider (*.py)")
        if not path:
            return
        src = Path(path)
        dest_dir = config_dir() / "providers"
        dest = dest_dir / src.name
        if src.suffix != ".py":
            QMessageBox.warning(self, "Import provider", "Providers must be .py files.")
            return
        if dest.exists() and src.resolve() == dest.resolve():
            QMessageBox.information(self, "Import provider", "That provider is already installed.")
            return
        box = QMessageBox.question(
            self, "Import provider",
            f"Providers are Python code that runs inside Hayate.\nOnly import ones you trust.\n\n"
            f"Copy {src.name} to your providers folder?\nThe original file is left untouched."
            + ("\n\nA provider with this name exists and will be replaced." if dest.exists() else ""))
        if box != QMessageBox.StandardButton.Yes:
            return
        try:
            dest_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
        except OSError as e:
            QMessageBox.warning(self, "Import provider", f"Copy failed: {e}")
            return
        self.refresh_providers()
        QMessageBox.information(self, "Import provider", f"Installed {src.name}. Original kept at:\n{src}")


    def _open_plugins_folder(self):
        p_dir = config_dir() / "providers"
        p_dir.mkdir(parents=True, exist_ok=True)
        try:
            (os.startfile(str(p_dir)) if sys.platform == "win32" else subprocess.Popen(["xdg-open", str(p_dir)]))
        except Exception:
            QMessageBox.information(self, "Plugin Folder", f"Plugin directory is located at:\n{p_dir}")

    def _on_cat_link_toggled(self, on):
        from hayate.core.settings import load_settings as _ld, save_settings as _sv
        s = _ld(); s["catalogue_link"] = bool(on); _sv(s)
        if isinstance(getattr(self, "settings", None), dict): self.settings["catalogue_link"] = bool(on)

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

    def showEvent(self, e):
        super().showEvent(e)
        self._refresh_network()

    @staticmethod
    def _valid_proxy(url):
        from urllib.parse import urlsplit
        try:
            u = urlsplit(url)
            u.port
        except ValueError:
            return False
        return u.scheme in ("http", "https", "socks5", "socks5h") and bool(u.hostname)

    def _set_net_status(self, text, role="text_secondary"):
        from hayate.core.theme import theme
        self.net_status.setText(text)
        self.net_status.setStyleSheet(
            f"color: {theme.rgba(role)}; font-size: 12px; font-weight: 600; background: transparent;")

    def _refresh_network(self):
        from urllib.parse import urlsplit
        from hayate.core import net
        from hayate.core.theme import theme
        mode = self.settings.get("proxy_mode", "off")
        port = net.detect_tor()
        self.tor_check.setVisible(port is not None or mode == "tor")  # hidden unless Tor is available
        self.dns_check.blockSignals(True)
        self.dns_check.setChecked(self.settings.get("dns_mode", "doh") == "doh")
        self.dns_check.blockSignals(False)
        for w, on in ((self.proxy_check, mode == "custom"), (self.tor_check, mode == "tor")):
            w.blockSignals(True)
            w.setChecked(on)
            w.blockSignals(False)
        if mode == "tor" and port is None:
            self._set_net_status("Tor isn't running, so Hayate's network requests are blocked. "
                                 "Start Tor or turn this off.", "danger")
        elif mode == "tor":
            self._set_net_status(f"Routing through Tor (127.0.0.1:{port}).", "accent")
        elif mode == "custom":
            url = self.settings.get("proxy_url", "")
            if not self._valid_proxy(url):
                self._set_net_status("The proxy URL is invalid, so network requests are blocked.", "danger")
            else:
                u = urlsplit(url)
                note = " Tip: use socks5h:// so DNS isn't resolved locally." if u.scheme == "socks5" else ""
                self._set_net_status(f"Routing through {u.scheme}://{u.hostname}:{u.port}.{note}", "accent")
        else:
            self._set_net_status("Direct connection with private DNS." if self.settings.get("dns_mode", "doh") == "doh"
                                 else "Direct connection with system DNS.")

    def _save_net(self):
        from hayate.core import net
        save_settings(self.settings)
        net.invalidate()
        self._refresh_network()
        self.settings_changed.emit()

    def _on_dns_toggled(self, on):
        self.settings["dns_mode"] = "doh" if on else "system"
        self._save_net()

    def _on_proxy_toggled(self, on):
        if on:
            url = self.proxy_edit.text().strip()
            if not self._valid_proxy(url):
                self.proxy_check.blockSignals(True)
                self.proxy_check.setChecked(False)
                self.proxy_check.blockSignals(False)
                QMessageBox.warning(self, "Invalid proxy",
                                    "Enter a proxy URL like socks5h://127.0.0.1:1080 or http://host:8080.")
                return
            self.settings["proxy_url"] = url
            self.settings["proxy_mode"] = "custom"
        elif self.settings.get("proxy_mode") == "custom":
            self.settings["proxy_mode"] = "off"
        self._save_net()

    def _on_proxy_edited(self):
        url = self.proxy_edit.text().strip()
        if url and not self._valid_proxy(url):
            self._set_net_status("Invalid proxy URL (use http://, socks5:// or socks5h://host:port).", "danger")
            return
        self.settings["proxy_url"] = url
        if not url and self.settings.get("proxy_mode") == "custom":
            self.settings["proxy_mode"] = "off"
        self._save_net()

    def _on_tor_toggled(self, on):
        from hayate.core import net
        if on:
            port = net.detect_tor()
            if port is None:
                self.tor_check.blockSignals(True)
                self.tor_check.setChecked(False)
                self.tor_check.blockSignals(False)
                QMessageBox.warning(self, "Tor not found",
                                    "No Tor service found on 127.0.0.1 (ports 9050 / 9150). Start Tor and try again.")
                return
            self.settings["tor_port"] = port
            self.settings["proxy_mode"] = "tor"
        elif self.settings.get("proxy_mode") == "tor":
            self.settings["proxy_mode"] = "off"
        self._save_net()

    def _export_history(self):
        if not history_mgr.data:
            QMessageBox.information(self, "Export Notice", "No watch history recorded yet.")
            return
        file_dest, _ = dlg_save(
            self, "Export Watch History", "hayate_history_backup.json", "JSON Files (*.json)"
        )
        if not file_dest:
            return
        try:
            Path(file_dest).write_text(
                json.dumps(history_mgr.export_payload(), indent=2, ensure_ascii=False), encoding="utf-8")
            QMessageBox.information(self, "Export Complete",
                f"Exported {len(history_mgr.data)} anime to:\n{file_dest}")
        except Exception as e:
            QMessageBox.warning(self, "Export Error", f"Failed to export history: {e}")

    def _import_history(self):
        file_src, _ = dlg_open(
            self, "Import Watch History Backup", "", "JSON Files (*.json)"
        )
        if not file_src:
            return
        try:
            incoming = history_mgr.parse_backup(json.loads(Path(file_src).read_text(encoding="utf-8")))
        except Exception as e:
            QMessageBox.warning(self, "Invalid Backup", f"This is not a valid Hayate history backup.\n\n{e}")
            return
        box = QMessageBox(self)
        box.setWindowTitle("Import Watch History")
        box.setText(f"The backup contains {len(incoming)} anime.")
        box.setInformativeText(
            "Merge keeps your current history and adds the backup to it.\n"
            "Replace overwrites it (a .bak copy of the old file is kept).")
        merge_btn = box.addButton("Merge", QMessageBox.ButtonRole.AcceptRole)
        replace_btn = box.addButton("Replace", QMessageBox.ButtonRole.DestructiveRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(merge_btn)
        box.exec()
        clicked = box.clickedButton()
        try:
            if clicked is merge_btn:
                added, updated = history_mgr.merge(incoming)
                msg = f"Merged: {added} added, {updated} updated."
            elif clicked is replace_btn:
                history_mgr.replace(incoming)
                msg = f"History replaced with {len(incoming)} anime."
            else:
                return
        except Exception as e:
            QMessageBox.warning(self, "Import Error", f"Failed to import: {e}")
            return
        QMessageBox.information(self, "Import Complete", msg)
        self.settings_changed.emit()

    def _export_library(self):
        from hayate import library_db as ldb
        con = ldb.db()
        payload = ldb.export_payload(con)
        if not payload["library"]:
            QMessageBox.information(self, "Export Notice", "Your library is empty.")
            return
        file_dest, _ = dlg_save(
            self, "Export Library", "hayate_library_backup.json", "JSON Files (*.json)")
        if not file_dest:
            return
        try:
            Path(file_dest).write_text(
                json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
            QMessageBox.information(self, "Export Complete",
                f"Exported {len(payload['library'])} anime to:\n{file_dest}")
        except Exception as e:
            QMessageBox.warning(self, "Export Error", f"Failed to export library: {e}")

    def _import_library(self):
        from hayate import library_db as ldb
        file_src, _ = dlg_open(self, "Import Library Backup", "", "JSON Files (*.json)")
        if not file_src:
            return
        try:
            incoming = ldb.parse_backup(json.loads(Path(file_src).read_text(encoding="utf-8")))
        except Exception as e:
            QMessageBox.warning(self, "Invalid Backup", f"This is not a valid Hayate library backup.\n\n{e}")
            return
        box = QMessageBox(self)
        box.setWindowTitle("Import Library")
        box.setText(f"The backup contains {len(incoming)} anime.")
        box.setInformativeText(
            "Merge keeps your current library and adds newer entries from the backup.\n"
            "Replace overwrites it (a library.db.bak copy of the old data is kept).")
        merge_btn = box.addButton("Merge", QMessageBox.ButtonRole.AcceptRole)
        replace_btn = box.addButton("Replace", QMessageBox.ButtonRole.DestructiveRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(merge_btn)
        box.exec()
        clicked = box.clickedButton()
        con = ldb.db()
        try:
            if clicked is merge_btn:
                added, updated = ldb.merge(con, incoming)
                msg = f"Merged: {added} added, {updated} updated."
            elif clicked is replace_btn:
                ldb.replace(con, incoming)
                msg = f"Library replaced with {len(incoming)} anime."
            else:
                return
        except Exception as e:
            QMessageBox.warning(self, "Import Error", f"Failed to import: {e}")
            return
        QMessageBox.information(self, "Import Complete", msg)

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
            ok = lan_server.start(target)
            self.btn_toggle_server.setText("Stop Server" if ok else "Start Server")
            self._refresh_lan_status_text()
            if not ok:
                QMessageBox.warning(self, "LAN Server", lan_server.notice or "Could not start the server.")

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
        lan_server.ip_visible = not lan_server.ip_visible
        self._refresh_lan_status_text()

    def _refresh_lan_status_text(self):
        want = bool(lan_server.ip_visible)
        if getattr(self, "_eye_state", None) != want:
            self._eye_state = want
            _ic(self.btn_toggle_ip, "eye-off" if want else "eye", text="")
        self.lan_status_lbl.setText(lan_server.status_text())

    def _open_lan_player_window(self):
        player = LANPlayerWindow(self)
        player.exec()
