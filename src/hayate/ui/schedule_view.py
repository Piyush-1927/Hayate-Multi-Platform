import datetime
import time
from concurrent.futures import ThreadPoolExecutor
from PyQt6.QtCore import Qt, QThread, QTimer, pyqtSignal
from hayate.core.workers import run_worker
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QScrollArea, QTabWidget, QFrame
)
from hayate.core.anilist import get_weekly_schedule_sync
from hayate.core import week_cache
from hayate.core.settings import load_settings

DAYS_OF_WEEK = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

class ScheduleWorker(QThread):
    data_ready = pyqtSignal(dict)
    error_occurred = pyqtSignal(str)

    def run(self):
        try:
            allow = bool(load_settings().get("allow_adult", False))
            data = week_cache.get_global(allow)  # cached for the week, refreshed every few hours
            if data is None:
                data = get_weekly_schedule_sync(allow_adult=allow, use_cache=False)
                week_cache.set_global(data, allow)
            self.data_ready.emit(data)
        except Exception as e:
            self.error_occurred.emit(str(e))

class ScheduleView(QWidget):
    play_requested = pyqtSignal(int, int)
    anime_selected = pyqtSignal(int)
    back_requested = pyqtSignal()

    def __init__(self, player=None, parent=None):
        super().__init__(parent)
        self.player = player
        self.raw_data: dict = {}
        self.mode: str = "global"
        self.in_app_ok: dict = {}       # provider name -> anime ids confirmed on that provider
        self.in_app_checked: dict = {}  # provider name -> anime ids already checked
        self.in_app_progress = (0, 0)
        self._checking: set = set()
        self._rows: list = []
        self._workers: set = set()
        self._render_timer = QTimer(self)
        self._render_timer.setSingleShot(True)
        self._render_timer.timeout.connect(lambda: self._render_schedule(self.raw_data))
        self._tick = QTimer(self)
        self._tick.timeout.connect(self._tick_rows)
        self._tick.start(30000)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(40, 24, 40, 40)
        layout.setSpacing(20)

        header = QHBoxLayout()
        header.setSpacing(14)

        back_btn = QPushButton("← Back")
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

        title = QLabel("Weekly Airing Schedule")
        title.setStyleSheet("font-size: 22px; font-weight: 800; color: #ffffff;")
        header.addWidget(title)

        header.addStretch()

        switcher_container = QFrame()
        switcher_container.setStyleSheet("""
            QFrame {
                background-color: #212130;
                border-radius: 8px;
                border: 1.5px solid #3d3d52;
            }
        """)
        s_layout = QHBoxLayout(switcher_container)
        s_layout.setContentsMargins(3, 3, 3, 3)
        s_layout.setSpacing(4)

        self.btn_global = QPushButton("Global Schedule")
        self.btn_global.setFixedHeight(30)
        self.btn_global.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_global.clicked.connect(lambda: self.set_mode("global"))
        s_layout.addWidget(self.btn_global)

        self.btn_in_app = QPushButton("In-App Releases")
        self.btn_in_app.setFixedHeight(30)
        self.btn_in_app.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_in_app.clicked.connect(lambda: self.set_mode("in_app"))
        s_layout.addWidget(self.btn_in_app)

        header.addWidget(switcher_container)
        layout.addLayout(header)

        self.tabs = QTabWidget()
        self.tabs.setStyleSheet("""
            QTabWidget::pane {
                border: 1.5px solid #2f2f44;
                border-radius: 12px;
                background: #171722;
            }
            QTabBar::tab {
                background: #1e1e2d;
                color: #94a3b8;
                padding: 10px 18px;
                font-weight: 700;
                border-top-left-radius: 8px;
                border-top-right-radius: 8px;
                margin-right: 4px;
            }
            QTabBar::tab:selected {
                background: #38bdf8;
                color: #090a0f;
            }
        """)
        layout.addWidget(self.tabs)

        self.day_layouts = {}
        for day in DAYS_OF_WEEK:
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setStyleSheet("background: transparent; border: none;")
            w = QWidget()
            w.setStyleSheet("background: transparent;")
            vl = QVBoxLayout(w)
            vl.setContentsMargins(20, 20, 20, 20)
            vl.setSpacing(10)
            scroll.setWidget(w)

            self.day_layouts[day] = vl
            self.tabs.addTab(scroll, day)

        self._update_switcher_styles()

    def set_mode(self, mode: str):
        if self.mode != mode:
            self.mode = mode
            self._update_switcher_styles()
            if mode == "in_app":
                self._start_in_app_check()
            self._render_schedule(self.raw_data)

    def _update_switcher_styles(self):
        active_style = """
            QPushButton {
                background-color: #38bdf8;
                color: #090a0f;
                font-weight: 800;
                font-size: 11px;
                border-radius: 6px;
                border: none;
                padding: 0 12px;
            }
        """
        inactive_style = """
            QPushButton {
                background-color: transparent;
                color: #94a3b8;
                font-weight: 600;
                font-size: 11px;
                border-radius: 6px;
                border: none;
                padding: 0 12px;
            }
            QPushButton:hover { color: #ffffff; }
        """
        if self.mode == "global":
            self.btn_global.setStyleSheet(active_style)
            self.btn_in_app.setStyleSheet(inactive_style)
        else:
            self.btn_global.setStyleSheet(inactive_style)
            self.btn_in_app.setStyleSheet(active_style)

    def update_tab_labels(self):
        today_idx = datetime.datetime.now().weekday()
        for idx, day in enumerate(DAYS_OF_WEEK):
            label = day
            if idx == today_idx:
                label = f"Today ({day})"
            elif idx == today_idx - 1 and today_idx > 0:
                label = f"Yesterday ({day})"
            elif idx == today_idx + 1 and today_idx < 6:
                label = f"Tomorrow ({day})"
            self.tabs.setTabText(idx, label)
        self.tabs.setCurrentIndex(today_idx)

    def load_schedule(self):
        self.update_tab_labels()
        self._show_message("Loading schedule…")
        worker = ScheduleWorker()
        worker.data_ready.connect(self._on_data_loaded)
        worker.error_occurred.connect(self._on_load_error)
        self.worker = worker
        run_worker(worker)

    def _on_data_loaded(self, data: dict):
        self.raw_data = data
        if self.mode == "in_app":
            self._start_in_app_check()
        self._render_schedule(data)

    def _on_load_error(self, msg: str):
        self._show_message(f"Couldn't load the schedule.\n{msg}", error=True)

    def _clear_day(self, vl):
        while vl.count():
            it = vl.takeAt(0)
            if it.widget():
                it.widget().deleteLater()

    def _show_message(self, text: str, error: bool = False):
        self._rows = []
        color = "#f87171" if error else "#64748b"
        for vl in self.day_layouts.values():
            self._clear_day(vl)
            lbl = QLabel(text)
            lbl.setWordWrap(True)
            lbl.setStyleSheet(f"color: {color}; font-size: 13px; font-weight: 600; padding: 20px 0;")
            vl.addWidget(lbl)
            vl.addStretch()

    # ---- In-App schedule: only shows the active provider really carries ----
    def _provider(self):
        return self.player.active_provider() if self.player else None

    @staticmethod
    def _provider_label(prov) -> str:
        return (getattr(prov, "label", "") or prov.name) if prov else "this provider"

    def _start_in_app_check(self):
        prov = self._provider()
        if not prov or not getattr(prov, "supports_catalog", False):
            return
        name = prov.name
        if name in self._checking:
            return

        airings = {}
        for entries in self.raw_data.values():
            for a in entries:
                airings.setdefault(a.get("id"), []).append(a.get("airing_at") or 0)

        # Today's tab first, so what the user is looking at fills in first.
        today = datetime.datetime.now().weekday()
        order = DAYS_OF_WEEK[today:] + DAYS_OF_WEEK[:today]
        seen, pending = set(), []
        for day in order:
            for a in self.raw_data.get(day, []):
                aid = a.get("id")
                if aid in seen:
                    continue
                seen.add(aid)
                if week_cache.needs_check(name, aid, airings.get(aid, [])):
                    pending.append((aid, a.get("title", "")))

        self.in_app_progress = (len(seen) - len(pending), len(seen))
        if not pending:
            return

        self._checking.add(name)
        worker = InAppWorker(prov, pending)
        worker.result.connect(self._on_in_app_result)
        worker.done.connect(self._on_in_app_done)
        run_worker(worker)

    def _on_in_app_result(self, pname: str, anime_id: int, found: bool):
        done, total = self.in_app_progress
        self.in_app_progress = (done + 1, total)
        if found:
            self._schedule_render()  # show newly confirmed rows right away

    def _on_in_app_done(self, pname: str):
        self._checking.discard(pname)
        self._schedule_render(force=True)

    def _schedule_render(self, force: bool = False):
        # Short debounce: rows appear in small batches instead of one repaint per show.
        if self.mode == "in_app" and not self._render_timer.isActive():
            self._render_timer.start(250)

    def _visible_items(self, items: list) -> list:
        if self.mode != "in_app":
            return items
        prov = self._provider()
        ok = week_cache.ok_ids(prov.name) if prov else set()
        return [a for a in items if a.get("id") in ok]

    def _restore_scroll(self, positions: dict):
        for i, value in positions.items():
            try:
                self.tabs.widget(i).verticalScrollBar().setValue(value)
            except Exception:
                pass

    def _render_schedule(self, data: dict):
        in_app = self.mode == "in_app"
        prov = self._provider() if in_app else None
        if in_app and (not prov or not getattr(prov, "supports_catalog", False)):
            self._show_message(
                f"The In-App schedule isn't available for {self._provider_label(prov)} yet, "
                "because this provider can't confirm which shows it carries."
            )
            return

        # Keep each tab's scroll position while rows are added progressively.
        positions = {}
        for i in range(self.tabs.count()):
            try:
                positions[i] = self.tabs.widget(i).verticalScrollBar().value()
            except Exception:
                pass

        checking = in_app and prov.name in self._checking
        done, total = self.in_app_progress
        self._rows = []
        for day, vl in self.day_layouts.items():
            self._clear_day(vl)

            items = self._visible_items(data.get(day, []) if isinstance(data, dict) else [])

            if not items:
                if checking:
                    text = f"Checking {self._provider_label(prov)} for releases… ({done}/{total})"
                elif in_app:
                    text = f"No confirmed releases on {self._provider_label(prov)} for {day}."
                else:
                    text = f"No entries available for {day}."
                empty = QLabel(text)
                empty.setStyleSheet("color: #64748b; font-size: 13px; font-weight: 600; padding: 20px 0;")
                vl.addWidget(empty)
                vl.addStretch()
                continue

            for anime in items:
                vl.addWidget(self._make_row(anime))

            if checking:
                more = QLabel(f"Checking for more releases… ({done}/{total})")
                more.setStyleSheet("color: #64748b; font-size: 12px; font-weight: 600; padding: 8px 0;")
                vl.addWidget(more)
            vl.addStretch()

        QTimer.singleShot(0, lambda: self._restore_scroll(positions))

    def _make_row(self, anime: dict):
        row = _ScheduleRow(anime)
        row.clicked.connect(self.anime_selected.emit)
        row.play_clicked.connect(self.play_requested.emit)
        self._rows.append(row)
        return row

    def _tick_rows(self):
        alive = []
        for r in self._rows:
            try:
                r.refresh()
                alive.append(r)
            except RuntimeError:  # widget already deleted
                pass
        self._rows = alive


class InAppWorker(QThread):
    """Asks the provider which scheduled shows it carries, several at a time.
    Every answer is stored in the weekly cache, so only new shows are checked next time."""
    result = pyqtSignal(str, int, bool)
    done = pyqtSignal(str)

    def __init__(self, provider, items):
        super().__init__()
        self.provider = provider
        self.items = items

    def run(self):
        name = self.provider.name

        def check(item):
            aid, title = item
            try:
                return aid, self.provider.carries(title)
            except Exception:
                return aid, None  # network hiccup: stays unchecked, retried next time

        count = 0
        with ThreadPoolExecutor(max_workers=6) as pool:
            for aid, found in pool.map(check, self.items):  # keeps today-first order
                if found is None:
                    continue
                week_cache.record(name, aid, found)
                self.result.emit(name, aid, found)
                count += 1
                if count % 10 == 0:
                    week_cache.save()
        week_cache.save()
        self.done.emit(name)


def _eta(seconds: float) -> str:
    mins = max(1, int(seconds // 60))
    days, rem = divmod(mins, 1440)
    hours, m = divmod(rem, 60)
    if days:
        return f"in {days}d {hours}h"
    if hours:
        return f"in {hours}h {m}m"
    return f"in {m}m"


class _ScheduleRow(QFrame):
    clicked = pyqtSignal(int)
    play_clicked = pyqtSignal(int, int)

    def __init__(self, anime: dict, parent=None):
        super().__init__(parent)
        self.anime_id = anime.get("id")
        self.episode = anime.get("episode") or 1
        self.airing_at = anime.get("airing_at") or 0

        self.setFixedHeight(54)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet("""
            QFrame {
                background-color: #1e1e2d;
                border-radius: 8px;
                border: 1.5px solid #2f2f44;
            }
            QFrame:hover { border-color: #38bdf8; }
        """)
        rl = QHBoxLayout(self)
        rl.setContentsMargins(18, 0, 14, 0)
        rl.setSpacing(12)

        time_lbl = QLabel(anime.get("time", ""))
        time_lbl.setFixedWidth(52)
        time_lbl.setStyleSheet("color: #38bdf8; font-weight: 800; font-size: 13px; border: none; background: transparent;")
        rl.addWidget(time_lbl)

        t_lbl = QLabel(str(anime.get("title", "Unknown")))
        t_lbl.setStyleSheet("color: #ffffff; font-weight: 700; font-size: 13px; border: none; background: transparent;")
        rl.addWidget(t_lbl, stretch=1)

        ep_lbl = QLabel(f"EP {self.episode}")
        ep_lbl.setStyleSheet("color: #94a3b8; font-weight: 600; font-size: 12px; border: none; background: transparent;")
        rl.addWidget(ep_lbl)

        self.status_lbl = QLabel("")
        self.status_lbl.setMinimumWidth(96)
        self.status_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.status_lbl.setStyleSheet("color: #fbbf24; font-weight: 700; font-size: 12px; border: none; background: transparent;")
        rl.addWidget(self.status_lbl)

        self.play_btn = QPushButton("▶ Play")
        self.play_btn.setFixedSize(84, 30)
        self.play_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.play_btn.setStyleSheet("""
            QPushButton {
                background-color: #38bdf8; color: #090a0f;
                border: none; border-radius: 6px;
                font-weight: 800; font-size: 12px;
            }
            QPushButton:hover { background-color: #7dd3fc; }
        """)
        self.play_btn.clicked.connect(lambda: self.play_clicked.emit(int(self.anime_id), int(self.episode)))
        rl.addWidget(self.play_btn)

        self.refresh()

    def refresh(self):
        """Aired episodes get a Play button; upcoming ones show a countdown."""
        left = self.airing_at - time.time()
        aired = left <= 0
        self.play_btn.setVisible(aired)
        self.status_lbl.setVisible(not aired)
        if not aired:
            self.status_lbl.setText(f"Airs {_eta(left)}")

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.anime_id is not None:
            self.clicked.emit(int(self.anime_id))
        super().mouseReleaseEvent(event)
