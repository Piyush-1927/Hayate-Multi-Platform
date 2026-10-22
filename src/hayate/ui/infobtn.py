from PyQt6.QtCore import Qt, QPoint, QObject, QEvent
from PyQt6.QtWidgets import (QWidget, QFrame, QLabel, QPushButton, QHBoxLayout, QVBoxLayout,
                             QComboBox, QLineEdit, QAbstractSpinBox, QBoxLayout, QSlider)
from hayate.core.theme import theme, _raw, HEIGHTS

WARN = ("warning", "adult", "18+", "permanent", "irreversible", "cannot be undone")
AMBER = "#fbbf24"


class InfoPop(QWidget):
    def __init__(self, items):
        super().__init__(None, Qt.WindowType.ToolTip | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        outer = QVBoxLayout(self); outer.setContentsMargins(0, 0, 0, 0)
        self.box = QFrame(); outer.addWidget(self.box)
        v = QVBoxLayout(self.box); v.setContentsMargins(14, 12, 14, 12); v.setSpacing(10)
        self.rows = []
        from PyQt6.QtWidgets import QApplication
        QApplication.instance().applicationStateChanged.connect(self._on_app_state)
        for kind, text in items:
            r = QHBoxLayout(); r.setSpacing(10)
            ic = QLabel("⚠" if kind == "warn" else "ⓘ"); ic.setAlignment(Qt.AlignmentFlag.AlignTop)
            tx = QLabel(text); tx.setWordWrap(True); tx.setFixedWidth(300)
            r.addWidget(ic); r.addWidget(tx); v.addLayout(r)
            self.rows.append((kind, ic, tx))

    def _on_app_state(self, state):
        if state != Qt.ApplicationState.ApplicationActive:
            self.hide()

    def restyle(self):
        t = theme.tokens()
        warn = any(k == "warn" for k, _, _ in self.rows)
        _raw(self.box, f"QFrame{{background:{t['card_background']};border:1px solid "
                       f"{AMBER if warn else t['accent']};border-radius:12px;}}")
        for k, ic, tx in self.rows:
            _raw(ic, f"color:{AMBER if k == 'warn' else t['accent']};font-size:15px;border:none;background:transparent;")
            _raw(tx, f"color:{t['text_primary']};font-size:12px;border:none;background:transparent;")

    def show_at(self, btn):
        self.restyle(); self.layout().activate(); self.adjustSize()
        g = btn.mapToGlobal(QPoint(btn.width(), btn.height() + 8))
        scr = btn.screen().availableGeometry()
        x = max(scr.left() + 8, min(g.x() - self.width(), scr.right() - self.width() - 8))
        y = g.y()
        if y + self.height() > scr.bottom():
            y = btn.mapToGlobal(QPoint(0, 0)).y() - self.height() - 8
        self.move(x, y); self.show()


class InfoButton(QPushButton):
    def __init__(self, items, parent=None):
        self.warn = any(k == "warn" for k, _ in items)
        super().__init__("!" if self.warn else "i", parent)
        self.setFixedSize(HEIGHTS["sm"], HEIGHTS["sm"])
        self.setCursor(Qt.CursorShape.WhatsThisCursor)
        self.pop = InfoPop(items)
        theme.changed.connect(self._style); self._style()

    def _style(self):
        t = theme.tokens(); c = AMBER if self.warn else t["accent"]
        _raw(self, f"QPushButton{{background:{t['button_background']};color:{c};border:1px solid "
                   f"{t['ui_border']};border-radius:8px;font-weight:800;font-size:13px;padding:0;min-width:{HEIGHTS['sm']}px;max-width:{HEIGHTS['sm']}px;"
                   f"min-height:{HEIGHTS['sm']}px;max-height:{HEIGHTS['sm']}px;}}"
                   f"QPushButton:hover{{border-color:{c};background:{t['hover']};}}")

    def enterEvent(self, e): self.pop.show_at(self); super().enterEvent(e)
    def leaveEvent(self, e): self.pop.hide(); super().leaveEvent(e)
    def hideEvent(self, e): self.pop.hide(); super().hideEvent(e)


def _title_for(view, card):
    if type(card).__name__ == "ThemeSettingsCard":
        return None
    for name, title in (("prov_combo", "Providers"), ("adult_checkbox", "Content"),
                        ("trans_combo", "Transitions"), ("clear_btn", "Image cache"),
                        ("btn_toggle_server", "LAN streaming"),
                        ("dns_check", "Security")):
        w = getattr(view, name, None)
        if w is not None and card.isAncestorOf(w):
            return title
    for b in card.findChildren(QPushButton):
        if "history" in b.text().lower():
            return "Watch history"
    return None


class _Pin(QObject):
    """Keeps the info button in the card's top-right corner without using layout space."""
    def __init__(self, card, btn):
        super().__init__(card)
        self.card, self.btn = card, btn
        card.installEventFilter(self)
        self.place(); btn.show()

    def place(self):
        self.btn.move(self.card.width() - self.btn.width() - 14, 12)
        self.btn.raise_()

    def eventFilter(self, o, e):
        if e.type() in (QEvent.Type.Resize, QEvent.Type.Show):
            self.place()
        return False


def polish_settings(view):
    from hayate.core import theme as _th
    content = view.findChild(QWidget, "settings_content")
    if content is not None and content.layout():
        content.layout().setSpacing(20)

    def nested(w):
        p = w.parentWidget()
        while p is not None:
            if p.property("card") is True: return True
            p = p.parentWidget()
        return False

    for card in [f for f in view.findChildren(QFrame) if f.property("card") is True]:
        lay = card.layout()
        if not isinstance(lay, QBoxLayout): continue
        lay.setSpacing(12)
        if not nested(card): lay.setContentsMargins(22, 18, 22, 20)
        items = []
        for lb in card.findChildren(QLabel):
            q = (_th._tracked.get(lb) or lb.styleSheet()).lower()
            txt = lb.text().strip()
            if txt and "94a3b8" in q and "font-size: 12px" in q:
                kind = "warn" if any(w in txt.lower() for w in WARN) else "info"
                items.append((kind, txt)); lb.hide()
        title = _title_for(view, card)
        if title and isinstance(lay, QVBoxLayout):
            lay.insertWidget(0, QLabel(f"<b>{title}</b>"))
        if items:
            _Pin(card, InfoButton(items, card))

    ip = getattr(view, "btn_toggle_ip", None)
    if ip is not None and not ip.toolTip():
        ip.setToolTip("Show / hide IP")

    for w in view.findChildren(QWidget):
        if isinstance(w, (InfoButton, QSlider)): continue
        if isinstance(w, QPushButton):
            if w.minimumWidth() == w.maximumWidth(): continue          # icons / swatches / ×
            if w.minimumHeight() == w.maximumHeight() >= 60: continue  # preset tiles
            if hasattr(w.parentWidget(), "_nb"): continue              # saved-theme capsule
            w.setFixedHeight(HEIGHTS["md"])
        elif isinstance(w, (QComboBox, QLineEdit, QAbstractSpinBox)):
            w.setFixedHeight(HEIGHTS["md"])
