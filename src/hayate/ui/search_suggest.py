import html
import re

from PyQt6.QtCore import Qt, QEvent, QPoint, QSize, QThread, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPixmap
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkRequest
from PyQt6.QtWidgets import (QFrame, QGraphicsDropShadowEffect, QHBoxLayout, QLabel,
                             QListWidget, QListWidgetItem, QVBoxLayout, QWidget)

from hayate.core.anilist import smart_search_sync, AnimeMedia
from hayate.core.workers import run_worker

ACCENT = "#38bdf8"
TW, TH = 46, 66
FORMATS = {"TV": "TV", "TV_SHORT": "TV Short", "MOVIE": "Movie", "OVA": "OVA",
           "ONA": "ONA", "SPECIAL": "Special", "MUSIC": "Music"}
STATUS = {"RELEASING": ("Airing", "#4ade80"), "NOT_YET_RELEASED": ("Upcoming", "#fbbf24")}


def _rounded(pm, w, h, r=6):
    s = 2
    pm = pm.scaled(w * s, h * s, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                   Qt.TransformationMode.SmoothTransformation)
    out = QPixmap(w * s, h * s)
    out.fill(Qt.GlobalColor.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    path = QPainterPath()
    path.addRoundedRect(0, 0, w * s, h * s, r * s, r * s)
    p.setClipPath(path)
    p.drawPixmap(-((pm.width() - w * s) // 2), -((pm.height() - h * s) // 2), pm)
    p.end()
    out.setDevicePixelRatio(s)
    return out


def _hl(text, query):
    esc = html.escape(text)
    words = sorted({w for w in query.split() if len(w) > 1}, key=len, reverse=True)
    if not words:
        return esc
    pat = re.compile("(" + "|".join(re.escape(html.escape(w)) for w in words) + ")", re.I)
    return pat.sub(r'<span style="color:%s;">\1</span>' % ACCENT, esc)


def _meta(m):
    parts = []
    fmt = FORMATS.get((m.format or "").upper(), (m.format or "").replace("_", " ").title())
    if fmt:
        parts.append(fmt)
    if m.year:
        parts.append(str(m.year))
    if m.episodes and m.episodes > 1:
        parts.append(f"{m.episodes} eps")
    if m.average_score:
        parts.append(f'<span style="color:#fbbf24;">\u2605 {m.average_score / 10:.1f}</span>')
    st = STATUS.get((m.status or "").upper())
    if st:
        parts.append(f'<span style="color:{st[1]};">{st[0]}</span>')
    return "  \u00b7  ".join(parts)


class _Row(QWidget):
    def __init__(self, m, query, text_w):
        super().__init__()
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 6, 12, 6)
        lay.setSpacing(14)
        self.thumb = QLabel()
        self.thumb.setFixedSize(TW, TH)
        self.thumb.setStyleSheet("background:#252536; border-radius:6px;")
        lay.addWidget(self.thumb)

        tf = QFont(self.font())
        tf.setPixelSize(15)
        tf.setWeight(QFont.Weight.DemiBold)
        title = QLabel()
        title.setFont(tf)
        title.setTextFormat(Qt.TextFormat.RichText)
        title.setStyleSheet("color:#f1f5f9;")
        elided = QFontMetrics(tf).elidedText(m.display_title, Qt.TextElideMode.ElideRight, max(120, text_w))
        title.setText(_hl(elided, query))

        mf = QFont(self.font())
        mf.setPixelSize(12)
        meta = QLabel()
        meta.setFont(mf)
        meta.setTextFormat(Qt.TextFormat.RichText)
        meta.setStyleSheet("color:#8b93a7;")
        meta.setText(_meta(m))

        col = QVBoxLayout()
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(4)
        col.addStretch()
        col.addWidget(title)
        col.addWidget(meta)
        col.addStretch()
        lay.addLayout(col, 1)


class _SuggestWorker(QThread):
    done = pyqtSignal(int, str, list)

    def __init__(self, token, query):
        super().__init__()
        self.token = token
        self.query = query

    def run(self):
        try:
            items, _ = smart_search_sync(self.query, per_page=10, max_tokens=2)
        except Exception:
            items = []
        self.done.emit(self.token, self.query, items)


class SuggestDropdown(QFrame):
    anime_chosen = pyqtSignal(AnimeMedia)
    see_all = pyqtSignal()
    ROW_H, FOOT_H, MAX_ROWS, MAX_W = 80, 48, 6, 760

    def __init__(self, line_edit):
        super().__init__()
        self.edit = line_edit
        self._token = 0
        self._query = ""
        self._cache, self._pix, self._workers = {}, {}, []
        self._navigable = False
        self._nam = QNetworkAccessManager(self)

        self.setObjectName("suggestBox")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setStyleSheet("""
            #suggestBox { background:#171724; border:1px solid #34344a; border-radius:14px; }
            QListWidget { background:transparent; border:none; outline:0; }
            QListWidget::item { border-radius:10px; }
            QListWidget::item:selected { background:#262642; }
            QListWidget::item:hover { background:#1f1f35; }
        """)
        fx = QGraphicsDropShadowEffect(self)
        fx.setBlurRadius(36)
        fx.setOffset(0, 10)
        fx.setColor(QColor(0, 0, 0, 170))
        self.setGraphicsEffect(fx)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        self.list = QListWidget()
        self.list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.list.setMouseTracking(True)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.itemClicked.connect(self._pick)
        lay.addWidget(self.list)
        self.hide()

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(280)
        self._timer.timeout.connect(self._fire)
        line_edit.textChanged.connect(lambda _t: self._timer.start())
        line_edit.installEventFilter(self)

    # ---- search ----
    def dismiss(self):
        self._timer.stop()
        self._token += 1
        self.hide()

    def _fire(self):
        q = self.edit.text().strip()
        if len(q) < 2:
            return self.dismiss()
        key = q.lower()
        if key in self._cache:
            self._token += 1
            return self._show(self._cache[key], q)
        self._token += 1
        w = _SuggestWorker(self._token, q)
        w.done.connect(self._on_done)
        self._workers.append(w)
        w.finished.connect(lambda w=w: w in self._workers and self._workers.remove(w))
        run_worker(w)

    def _on_done(self, token, query, items):
        if len(self._cache) > 60:
            self._cache.clear()
        self._cache[query.lower()] = items
        if token == self._token and query.lower() == self.edit.text().strip().lower():
            self._show(items, query)

    # ---- rendering ----
    def _show(self, items, query):
        self._query = query
        self.list.clear()
        items = items[:self.MAX_ROWS]
        text_w = min(self.edit.width(), self.MAX_W) - 16 - 10 - 12 - TW - 14 - 12 - 12
        total = 0
        if not items:
            it = QListWidgetItem()
            it.setFlags(Qt.ItemFlag.NoItemFlags)
            it.setSizeHint(QSize(0, 64))
            self.list.addItem(it)
            lbl = QLabel(f"No matches for \u201c{html.escape(query)}\u201d")
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setStyleSheet("color:#8b93a7; font-size:13px;")
            self.list.setItemWidget(it, lbl)
            total, self._navigable = 64, False
        else:
            for m in items:
                it = QListWidgetItem()
                it.setData(Qt.ItemDataRole.UserRole, m)
                it.setSizeHint(QSize(0, self.ROW_H))
                self.list.addItem(it)
                row = _Row(m, query, text_w)
                self.list.setItemWidget(it, row)
                self._load_thumb(row.thumb, m.cover_url)
                total += self.ROW_H
            it = QListWidgetItem()
            it.setData(Qt.ItemDataRole.UserRole, "footer")
            it.setSizeHint(QSize(0, self.FOOT_H))
            self.list.addItem(it)
            foot = QWidget()
            foot.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            fl = QHBoxLayout(foot)
            fl.setContentsMargins(14, 0, 14, 0)
            left = QLabel(f'See all results for <span style="color:{ACCENT};font-weight:600;">'
                          f'\u201c{html.escape(query)}\u201d</span>')
            left.setTextFormat(Qt.TextFormat.RichText)
            left.setStyleSheet("color:#cbd5e1; font-size:13px;")
            hint = QLabel("Enter \u21b5")
            hint.setStyleSheet("color:#64748b; font-size:12px;")
            fl.addWidget(left)
            fl.addStretch()
            fl.addWidget(hint)
            self.list.setItemWidget(it, foot)
            total += self.FOOT_H
            self._navigable = True
        self.list.setFixedHeight(total + 6)
        self._place()

    def _place(self):
        win = self.edit.window()
        if self.parentWidget() is not win:
            self.setParent(win)
        w = min(self.edit.width(), self.MAX_W)
        p = self.edit.mapTo(win, QPoint(0, self.edit.height() + 6))
        self.setGeometry(p.x(), p.y(), w, self.list.height() + 16)
        self.show()
        self.raise_()

    # ---- thumbnails ----
    def _load_thumb(self, label, url):
        if not url:
            return
        pm = self._pix.get(url)
        if pm is not None:
            label.setPixmap(pm)
            return
        req = QNetworkRequest(QUrl(url))
        req.setAttribute(QNetworkRequest.Attribute.RedirectPolicyAttribute,
                         QNetworkRequest.RedirectPolicy.NoLessSafeRedirectPolicy)
        rep = self._nam.get(req)
        rep.finished.connect(lambda r=rep, lb=label, u=url: self._thumb_done(r, lb, u))

    def _thumb_done(self, rep, label, url):
        data = rep.readAll()
        rep.deleteLater()
        pm = QPixmap()
        if not pm.loadFromData(data):
            return
        pm = _rounded(pm, TW, TH)
        if len(self._pix) > 300:
            self._pix.clear()
        self._pix[url] = pm
        try:
            label.setPixmap(pm)
        except RuntimeError:
            pass

    # ---- interaction ----
    def _pick(self, it):
        data = it.data(Qt.ItemDataRole.UserRole)
        self.dismiss()
        if data == "footer":
            self.see_all.emit()
        elif isinstance(data, AnimeMedia):
            self.anime_chosen.emit(data)

    def eventFilter(self, obj, ev):
        if obj is self.edit and self.isVisible():
            t = ev.type()
            if t == QEvent.Type.KeyPress:
                k = ev.key()
                if k == Qt.Key.Key_Escape:
                    self.dismiss()
                    return True
                if k in (Qt.Key.Key_Down, Qt.Key.Key_Up) and self._navigable:
                    r, n = self.list.currentRow(), self.list.count()
                    if k == Qt.Key.Key_Down:
                        r = 0 if r < 0 else (r + 1) % n
                    else:
                        r = n - 1 if r < 0 else (r - 1) % n
                    self.list.setCurrentRow(r)
                    return True
                if k in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                    it = self.list.currentItem()
                    if it is not None and self._navigable:
                        self._pick(it)
                        return True
                    self.dismiss()
            elif t == QEvent.Type.FocusOut:
                QTimer.singleShot(150, self.dismiss)
        return False
