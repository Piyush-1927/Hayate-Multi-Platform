import json, re
from hayate.core.dialogs import dlg_open, dlg_save
from pathlib import Path
from string import Template
from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (QFrame, QVBoxLayout, QHBoxLayout, QLabel, QComboBox,
    QPushButton, QSpinBox, QDoubleSpinBox, QWidget, QApplication, QColorDialog, QInputDialog, QFileDialog, QMessageBox)

FMT = 1
# control height pattern (px): change each size independently
HEIGHTS = dict(sm=28, md=36, lg=44)
PATH = Path.home() / ".config/hayate/theme.json"
HEX = re.compile(r"^#[0-9a-fA-F]{6}$")

MIDNIGHT = dict(
    app_background="#171722", card_background="#1e1e2d", card_border="#2f2f44",
    card_border_width=1.5, ui_border="#3d3d52", text_primary="#ffffff",
    text_secondary="#94a3b8", accent="#38bdf8", button_background="#212130",
    button_text="#e2e8f0", hover="#2d2d42", selected="#26263b")
PRESETS = {
    "Midnight": MIDNIGHT,
    "Forest": {**MIDNIGHT, "app_background": "#101412", "card_background": "#1a201c",
        "card_border": "#2c3a31", "ui_border": "#33443a", "accent": "#34d399",
        "button_background": "#223027", "hover": "#2a3d31", "selected": "#14532d"},
    "Daylight": {**MIDNIGHT, "app_background": "#f4f6fa", "card_background": "#ffffff",
        "card_border": "#d5dbe6", "ui_border": "#c3cbd9", "text_primary": "#1b2433",
        "text_secondary": "#5b6779", "accent": "#2563eb", "button_background": "#e8edf5",
        "button_text": "#1b2433", "hover": "#dde5f1", "selected": "#dbeafe"},
    "Ember": {**MIDNIGHT, "app_background": "#17110e", "card_background": "#231a15",
        "card_border": "#4a3326", "ui_border": "#573b2b", "accent": "#fb923c",
        "button_background": "#33231a", "hover": "#43301f", "selected": "#7c2d12"},
}

QSS = Template("""
QMainWindow, QDialog { background:$app_background; }
QLabel { color:$text_primary; }
QFrame[card="true"] { background:$card_background; border:${card_border_width}px solid $card_border; border-radius:12px; }
QPushButton { background:$button_background; color:$button_text; border:1px solid $ui_border; border-radius:8px; padding:6px 12px; }
QPushButton:hover { background:$hover; }
QPushButton:checked { background:$selected; border-color:$accent; }
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox { background:$card_background; color:$text_primary; border:1px solid $ui_border; border-radius:6px; padding:4px 8px; }
QComboBox QAbstractItemView { background:$card_background; color:$text_primary; selection-background-color:$selected; }
QSlider::groove:horizontal { height:4px; background:$ui_border; border-radius:2px; }
QSlider::sub-page:horizontal { background:$accent; border-radius:2px; }
QSlider::handle:horizontal { background:$accent; width:14px; margin:-5px 0; border-radius:7px; }
""")


def clean(d, strict=False):
    out = {}
    for k, v in d.items():
        if k not in MIDNIGHT:
            continue
        ok = (isinstance(v, (int, float)) and not isinstance(v, bool) and 0 <= v <= 6) if k == "card_border_width" \
            else (isinstance(v, str) and HEX.match(v))
        if ok:
            out[k] = v.lower() if isinstance(v, str) else v
        elif strict:
            raise ValueError(f"invalid value for {k}")
    return {**MIDNIGHT, **out}


def _lum(h):
    c = [int(h[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    c = [x / 12.92 if x <= .03928 else ((x + .055) / 1.055) ** 2.4 for x in c]
    return .2126 * c[0] + .7152 * c[1] + .0722 * c[2]


def contrast(a, b):
    la, lb = sorted((_lum(a), _lum(b)), reverse=True)
    return (la + .05) / (lb + .05)


class ThemeManager(QObject):
    changed = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.active, self.library, self.draft = "Midnight", {}, None
        try:
            s = json.loads(PATH.read_text())
            self.library = {n: clean(t) for n, t in s.get("library", {}).items()}
            if s.get("active") in {*PRESETS, *self.library}:
                self.active = s["active"]
        except Exception:
            pass  # missing/invalid -> Midnight

    def names(self): return [*PRESETS, *self.library]

    def tokens(self):
        return self.draft or PRESETS.get(self.active) or self.library.get(self.active) or MIDNIGHT

    def qss(self): return QSS.substitute(self.tokens())

    def _save(self):
        PATH.parent.mkdir(parents=True, exist_ok=True)
        PATH.write_text(json.dumps({"active": self.active, "library": self.library}, indent=2))

    def apply(self, name):
        if name in self.names():
            self.active, self.draft = name, None
            self._save(); self.changed.emit()

    def preview(self, tokens): self.draft = clean(tokens); self.changed.emit()

    def delete(self, name):
        if name in self.library:
            del self.library[name]
            if self.active == name: self.active = "Midnight"
            self.draft = None
            self._save(); self.changed.emit()

    def save_custom(self, name, tokens):
        self.library[name] = clean(tokens); self.apply(name)

    def export_theme(self, path):
        Path(path).write_text(json.dumps(
            {"hayate_theme": FMT, "name": self.active, "tokens": self.tokens()}, indent=2))

    def import_theme(self, path):
        d = json.loads(Path(path).read_text())
        if d.get("hayate_theme") != FMT:
            raise ValueError("unsupported theme file")
        base = name = str(d["name"])[:40]
        tokens, i = clean(d["tokens"], strict=True), 2
        while name in self.names():
            name = f"{base} {i}"; i += 1
        self.save_custom(name, tokens)
        return name


theme = ThemeManager()


from PyQt6.QtWidgets import QGridLayout, QSlider
from PyQt6.QtCore import Qt

SUBS = {"Midnight": "Navy · Cyan", "Forest": "Charcoal · Emerald",
        "Daylight": "Light · Blue", "Ember": "Warm charcoal · Orange"}
SW = {"card_background": ["#1e1e2d", "#262638", "#141b2d", "#1f2a24", "#ffffff"],
      "card_border": ["#2f2f44", "#454563", "#38bdf8", "#34d399", "#fb923c"],
      "accent": ["#38bdf8", "#a78bfa", "#34d399", "#fb923c", "#f472b6"]}
TITLES = {"card_background": "Card background", "card_border": "Card border colour",
          "accent": "Accent colour"}


def _raw(w, q):  # bypass the legacy-colour rewriter
    _orig_set(w, q)


class ThemeSettingsCard(QFrame):
    def __init__(self):
        super().__init__()
        self.setProperty("card", True)
        self._tiles, self._sws = {}, []
        lay = QVBoxLayout(self); lay.setSpacing(10)
        lay.addWidget(QLabel("<b>Colour scheme</b>"))
        pv = QFrame(); pv.setProperty("card", True)
        pl = QVBoxLayout(pv)
        pl.addWidget(QLabel("Settings card: background and outline follow your scheme"))
        self.ex = QPushButton("Example button"); pl.addWidget(self.ex)
        lay.addWidget(pv)
        g = QGridLayout()
        for i, (n, tk) in enumerate(PRESETS.items()):
            self._tiles[n] = self._tile(n, tk); g.addWidget(self._tiles[n], i // 2, i % 2)
        lay.addLayout(g)
        self.saved_box = QWidget(); self.saved_grid = QGridLayout(self.saved_box)
        self.saved_grid.setContentsMargins(0, 0, 0, 0)
        self._caps = []
        lay.addWidget(self.saved_box)
        lay.addWidget(QLabel("<b>Custom colour scheme</b>"))
        self._row(lay, "card_background"); self._row(lay, "card_border")
        h = QHBoxLayout(); h.addWidget(QLabel("Border thickness")); h.addStretch()
        self.wl = QLabel(); h.addWidget(self.wl); lay.addLayout(h)
        self.sl = QSlider(Qt.Orientation.Horizontal); self.sl.setRange(0, 4)
        self.sl.valueChanged.connect(lambda v: self._set("card_border_width", float(v)))
        lay.addWidget(self.sl)
        e = QHBoxLayout(); e.addWidget(QLabel("None")); e.addStretch(); e.addWidget(QLabel("Thick"))
        lay.addLayout(e)
        self._row(lay, "accent")
        r = QHBoxLayout()
        for text, fn in (("Save as…", self._save), ("Import", self._import), ("Export", self._export)):
            b = QPushButton(text); b.clicked.connect(fn); r.addWidget(b)
        lay.addLayout(r)
        theme.changed.connect(self._sync); self._fill(); self._sync()

    def _tile(self, n, tk):
        b = QPushButton(); b.setCheckable(True); b.setFixedHeight(92)
        b.clicked.connect(lambda _, n=n: theme.apply(n))
        v = QVBoxLayout(b); v.setContentsMargins(8, 8, 8, 8); v.setSpacing(2)
        pv = QFrame(); pv.setFixedHeight(38)
        _raw(pv, f"QFrame{{background:{tk['app_background']};border-radius:6px;}}")
        pr = QVBoxLayout(pv); pr.setContentsMargins(6, 6, 6, 6); pr.setSpacing(4)
        bar = QFrame(); bar.setFixedSize(46, 6)
        _raw(bar, f"background:{tk['accent']};border-radius:3px;")
        cd = QFrame(); cd.setFixedHeight(12)
        _raw(cd, f"background:{tk['card_background']};border:1px solid {tk['card_border']};border-radius:4px;")
        pr.addWidget(bar); pr.addWidget(cd)
        v.addWidget(pv); v.addWidget(QLabel(f"<b>{n}</b>")); v.addWidget(QLabel(SUBS[n]))
        for w in b.findChildren(QWidget):
            w.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        return b

    def _row(self, lay, k):
        lay.addWidget(QLabel(TITLES[k]))
        r = QHBoxLayout()
        for c in SW[k]:
            b = QPushButton(); b.setFixedSize(46, HEIGHTS["sm"])
            b.clicked.connect(lambda _, k=k, c=c: self._set(k, c))
            self._sws.append((b, k, c)); r.addWidget(b)
        p = QPushButton("Custom…"); p.clicked.connect(lambda _, k=k: self._pick(k))
        r.addWidget(p); r.addStretch(); lay.addLayout(r)

    def _sync(self):
        t = theme.tokens(); cur = None if theme.draft else theme.active
        for n, b in self._tiles.items():
            b.setChecked(n == cur)
            _raw(b, f"QPushButton{{background:{t['card_background']};border:2px solid "
                    f"{t['accent'] if n == cur else t['ui_border']};border-radius:10px;text-align:left;}}")
        for b, k, c in self._sws:
            _raw(b, f"QPushButton{{background:{c};border:2px solid "
                    f"{t['text_primary'] if t[k] == c else 'transparent'};border-radius:6px;}}")
        _raw(self.ex, f"QPushButton{{background:{t['accent']};color:{legacy('#090a0f')};"
                      "border:none;border-radius:8px;padding:6px;font-weight:700;}")
        for cap, n in getattr(self, "_caps", []):
            on = n == cur
            _raw(cap, f"QFrame{{background:{t['card_background']};border:2px solid "
                      f"{t['accent'] if on else t['ui_border']};border-radius:14px;}}")
            _raw(cap._nb, f"QPushButton{{background:transparent;border:none;color:{t['text_primary']};text-align:left;padding:4px 0;min-width:0;}}")
            _raw(cap._xb, f"QPushButton{{background:transparent;border:none;border-radius:11px;color:{t['text_secondary']};font-size:16px;font-weight:700;padding:0;min-width:22px;max-width:22px;min-height:22px;max-height:22px;}}QPushButton:hover{{background:#f87171;color:#ffffff;}}")
        self.sl.blockSignals(True); self.sl.setValue(round(t["card_border_width"])); self.sl.blockSignals(False)
        self.wl.setText(f"{t['card_border_width']:g}px")

    def _fill(self):
        for cap, _n in self._caps:
            cap.setParent(None); cap.deleteLater()
        self._caps = []
        for i, n in enumerate(theme.library):
            cap = QFrame(); h = QHBoxLayout(cap); h.setContentsMargins(10, 2, 4, 2); h.setSpacing(4); cap.setFixedHeight(HEIGHTS["md"])
            nb = QPushButton(n); nb.setFlat(True); nb.setFixedHeight(HEIGHTS["md"] - 4); nb.setCursor(Qt.CursorShape.PointingHandCursor)
            nb.clicked.connect(lambda _, n=n: theme.apply(n))
            xb = QPushButton("×"); xb.setFixedSize(22, 22); xb.setToolTip("Remove saved theme")
            xb.setCursor(Qt.CursorShape.PointingHandCursor)
            xb.clicked.connect(lambda _, n=n: self._remove(n))
            h.addWidget(nb, 1); h.addWidget(xb)
            cap._nb, cap._xb = nb, xb
            self.saved_grid.addWidget(cap, i // 2, i % 2)
            self._caps.append((cap, n))
        self.saved_box.setVisible(bool(theme.library))
        self._sync()

    def _remove(self, n):
        from PyQt6.QtWidgets import QMessageBox
        if QMessageBox.question(self, "Remove theme", f"Remove saved theme “{n}”?") \
                == QMessageBox.StandardButton.Yes:
            theme.delete(n); self._fill()

    def _set(self, k, v): theme.preview({**theme.tokens(), k: v})

    def _pick(self, k):
        c = QColorDialog.getColor(QColor(theme.tokens()[k]), self)
        if c.isValid(): self._set(k, c.name())

    def _save(self):
        n, ok = QInputDialog.getText(self, "Save theme", "Name:")
        if ok and n.strip(): theme.save_custom(n.strip()[:40], theme.tokens()); self._fill()

    def _import(self):
        f, _ = dlg_open(self, "Import", "", "Hayate theme (*.json)")
        if f:
            try: theme.import_theme(f); self._fill()
            except Exception as e: QMessageBox.warning(self, "Import failed", str(e))

    def _export(self):
        f, _ = dlg_save(self, "Export", f"{theme.active}.json", "Hayate theme (*.json)")
        if f: theme.export_theme(f)


# ---- runtime translation of legacy hardcoded colours -> theme ----
import weakref
_BASE = dict(MIDNIGHT)
_LEGACY = {h: r for r, hs in {
    "app_background": "#171722 #14141e #0f0f17 #12121a",
    "card_background": "#1e1e2d #171724 #1a1a27",
    "card_border": "#2f2f44 #26263a #323247 #363650 #383852",
    "ui_border": "#3d3d52 #4a4a68 #4f4f6b #252538",
    "button_background": "#212130 #1c1c2e #1c1c2b #222233 #242436",
    "hover": "#2d2d42 #2d2d44 #2c2c42 #27273a #27273c #26263b #25253b #25253d #2f2f48",
    "text_primary": "#ffffff #f1f5f9 #e2e8f0 #cbd5e1",
    "text_secondary": "#94a3b8 #64748b",
    "accent": "#38bdf8 #7dd3fc #0284c7",
}.items() for h in hs.split()}
_HEX = re.compile(r"#[0-9a-fA-F]{6}\b")
_W = re.compile(r"1\.5px(\s+solid\s+#2f2f44)", re.I)
_tracked = weakref.WeakKeyDictionary()
_orig_set = QWidget.setStyleSheet


def legacy(h):
    h = h.lower(); t = theme.tokens()
    if h == "#090a0f":  # text on accent
        return h if contrast(h, t["accent"]) >= contrast("#ffffff", t["accent"]) else "#ffffff"
    role = _LEGACY.get(h)
    if not role:
        return h
    sg = -1 if _lum(t["app_background"]) > .5 else 1
    a, b, c = ([int(x[i:i + 2], 16) for i in (1, 3, 5)] for x in (h, _BASE[role], t[role]))
    return "#%02x%02x%02x" % tuple(max(0, min(255, n + sg * (x - y))) for x, y, n in zip(a, b, c))


def rewrite(q):
    if "#" not in q:
        return q
    q = _W.sub(lambda m: f"{theme.tokens()['card_border_width']}px{m.group(1)}", q)
    return _HEX.sub(lambda m: legacy(m.group(0)), q)


def _set(self, q):
    _tracked[self] = q
    _orig_set(self, rewrite(q))


QWidget.setStyleSheet = _set


def _refresh():
    for w, q in list(_tracked.items()):
        try: _orig_set(w, rewrite(q))
        except RuntimeError: pass
    for w in QApplication.allWidgets():
        w.update()


theme.changed.connect(_refresh)
