import re, weakref
from pathlib import Path
from PyQt6.QtCore import Qt, QByteArray, QSize, QRectF, QTimer
from PyQt6.QtGui import QFontDatabase, QFont, QIcon, QPixmap, QPainter
from PyQt6.QtWidgets import QApplication, QLabel, QAbstractButton
try:
    from PyQt6.QtSvg import QSvgRenderer
except Exception:
    QSvgRenderer = None

ASSETS = Path(__file__).resolve().parent.parent / "assets"
_SVG, _reg, _state = {}, weakref.WeakKeyDictionary(), {"fonts": False, "conn": False}


def load_fonts():
    app = QApplication.instance()
    if _state["fonts"] or not app: return
    _state["fonts"] = True
    ids = [QFontDatabase.addApplicationFont(str(p)) for p in sorted((ASSETS / "fonts").glob("Inter-*")) if p.suffix in (".otf", ".ttf")]
    if any(i >= 0 for i in ids):
        sz = app.font().pointSizeF(); f = QFont("Inter")
        if sz > 0: f.setPointSizeF(sz)
        app.setFont(f)


def _pix(name, size, color):
    if QSvgRenderer is None: return None
    base = "heart" if name == "heart-fill" else name
    if base not in _SVG:
        f = ASSETS / "icons" / f"{base}.svg"; _SVG[base] = f.read_text() if f.exists() else ""
    if not _SVG[base]: return None
    svg = _SVG[base].replace("currentColor", color)
    if name in ("play", "pause", "heart-fill"): svg = svg.replace('fill="none"', f'fill="{color}"', 1)
    r = QSvgRenderer(QByteArray(svg.encode()))
    pm = QPixmap(size * 2, size * 2); pm.fill(Qt.GlobalColor.transparent); pm.setDevicePixelRatio(2.0)
    p = QPainter(pm); r.render(p, QRectF(0, 0, size, size)); p.end()
    return pm


_MAP = {"\u2190": "arrow-left", "\U0001F441": "eye", "\u25B6": "play", "\u23F8": "pause", "\u2661": "heart", "\u2665": "heart-fill",
        "\u2764": "heart-fill", "\u2715": "x", "\u2699": "settings", "\u21BB": "refresh-cw", "\u2B07": "download",
        "\u23EA": "rewind", "\u23E9": "fast-forward", "\U0001F50A": "volume-2", "\U0001F507": "volume-x", "\u26F6": "maximize"}


def _guard(w):
    if getattr(w, "_ic_guard", False) or not isinstance(w, QAbstractButton): return
    w._ic_guard = True; orig = w.setText

    def st(t, orig=orig, w=w):
        t = str(t); g = t[:1]
        if g in _MAP and w in _reg:
            _reg[w] = (_MAP[g], _reg[w][1]); t = t[1:].lstrip("\ufe0f ").strip()
            orig(t); _apply(w, *_reg[w])
        else:
            orig(t)
    w.setText = st


def _fg(w):
    from hayate.core import theme as T
    ss = T._tracked.get(w) or w.styleSheet()
    m = re.search(r'(?<![-\w])color\s*:\s*(#[0-9a-fA-F]{6})', ss)
    if m: return T.legacy(m.group(1))
    return T.theme.derived()["button_text" if isinstance(w, QAbstractButton) else "text_primary"]


def _apply(w, name, size):
    try:
        pm = _pix(name, size, _fg(w))
        if pm is None: return
        if isinstance(w, QLabel): w.setPixmap(pm)
        else: w.setIcon(QIcon(pm)); w.setIconSize(QSize(size, size))
    except RuntimeError:
        pass


def _retint():
    for w, (n, s) in list(_reg.items()): _apply(w, n, s)


def ic(w, name, size=None, text=None):
    if text is not None: w.setText(text)
    if size is None:
        size = 13 if name == "x" else (15 if (w.text() if hasattr(w, "text") else "") else 18)
    _reg[w] = (name, size); _apply(w, name, size)
    _guard(w)
    QTimer.singleShot(0, lambda: _apply(w, name, size))  # re-tint once its stylesheet is set
    if not _state["conn"]:
        from hayate.core.theme import theme
        theme.changed.connect(lambda: QTimer.singleShot(30, _retint)); _state["conn"] = True
    return w
