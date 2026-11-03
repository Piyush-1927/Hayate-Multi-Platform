import pickle, re, time, unicodedata
from queue import PriorityQueue, Empty
from PyQt6.QtCore import QEasingCurve, QEvent, QPoint, QRectF, QThread, QTimer, QVariantAnimation, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtWidgets import (QApplication, QFrame, QHBoxLayout, QLabel, QPushButton,
                             QGridLayout, QLineEdit, QComboBox, QScrollArea, QVBoxLayout, QWidget)

CARD_W, CARD_H, GAP = 185, 305, 20
PITCH = CARD_H + GAP
WINDOW = 10  # rows kept alive, viewport row in the middle
KITSU_MAX = 22515  # Kitsu total; replaced by the real count after the first chunk
ROWS_PER_PAGE = 8  # rows per page
GENRE_SLUG = {"Action": "action", "Adventure": "adventure", "Comedy": "comedy", "Drama": "drama",
              "Ecchi": "ecchi", "Fantasy": "fantasy", "Horror": "horror", "Mahou Shoujo": "magical-girl",
              "Mecha": "mecha", "Music": "music", "Mystery": "mystery", "Psychological": "psychological",
              "Romance": "romance", "Sci-Fi": "science-fiction", "Slice of Life": "slice-of-life",
              "Sports": "sports", "Supernatural": "supernatural", "Thriller": "thriller"}  # AniList name -> Kitsu slug
GENRES = list(GENRE_SLUG)
SORTS = ["Popularity", "Score", "Released", "Title"]
KSORT = {"Popularity": "userCount", "Score": "averageRating", "Released": "startDate", "Title": "canonicalTitle"}
SEASONS = ["Winter", "Spring", "Summer", "Fall"]
STATUSES = {"Airing": ("current", "RELEASING"), "Finished": ("finished", "FINISHED"), "Upcoming": ("upcoming", "NOT_YET_RELEASED")}
DEF_OPTS = {"sort": "Popularity", "desc": True, "season": None, "year": None, "status": None}
HIDE_CJK_ONLY = True  # hide titles with no Latin letters (AniList can't match them)
_TAIL = re.compile(r"(?:\s*[-\u2013|]\s*(?:now on|streaming|available)|\s*\|\s*(?:from|s\d+ starting|starting))\b.*$", re.I)


from hayate.core.theme import theme

def _clean(t):
    t = re.sub(r"#ULTRAEarlyBird\S*", "", t)
    t = re.sub(r"\u3010[^\u3011]*\u3011", "", t)  # 【Ani-One Asia】 style channel tags
    parts = re.findall(r"\u300a([^\u300b]*)\u300b", t)  # 《English》|《中文》: keep the English part
    for x in parts:
        x = re.sub(r"\s+", " ", x).strip(" |-")
        if re.search(r"[A-Za-z]{3}", x) and not _CJK.search(x): t = x; break
    return _TAIL.sub("", t).strip()

_FAIL = object()
_SEASON = re.compile(r"\s*[-\u2013:]?\s*\b(?:(?:the\s+)?(?:final|second|third|fourth|fifth|\d+(?:st|nd|rd|th))"
                     r"\s+(?:season|cour|part)|season\s*\d+|part\s*\d+|cour\s*\d+)\b.*$", re.I)
_ROMAN = re.compile(r"\s+(?:[IVX]+|\d+)$")


_SN = re.compile(r"\s+S\d+$", re.I)
_ACT = re.compile(r"\s*\bact\s*[IVX\d]+$", re.I)
_CJK = re.compile(r"[\u3040-\u30ff\u3400-\u9fff]")


def _norm(t):
    t = unicodedata.normalize("NFKC", t)
    return t.replace("\u02bc", "'").replace("\u2019", "'")


def _variants(t):
    """Search strings to try for a title, most specific first."""
    t = _norm(t); strip = " -\u2013:"
    a = _SEASON.sub("", t).strip(strip)
    a = _SN.sub("", a).strip(strip)
    a = _ACT.sub("", a).strip(strip)
    b = _ROMAN.sub("", t).strip(strip)
    c = _ROMAN.sub("", a).strip(strip)
    lat = ""
    if _CJK.search(t):
        lat = re.sub(r"\s+", " ", re.sub(r"[^A-Za-z0-9' ]+", " ", _CJK.sub(" ", t))).strip()
    out = [t]
    for v in (a, b, c, t.split(":")[0].strip(), lat):
        if len(v) >= 3 and v.lower() not in {x.lower() for x in out}: out.append(v)
    return out


class _GenrePanel(QFrame):
    changed = pyqtSignal()
    _CHIP = ("QPushButton{background:#242436;color:#cbd5e1;border:1.5px solid #2f2f44;border-radius:8px;"
             "padding:0 10px;font-weight:600;font-size:11px;}QPushButton:hover{border-color:#6366f1;}"
             "QPushButton:checked{background:#6366f1;color:#ffffff;border-color:#6366f1;}")

    def __init__(self, parent, genres, selected, opts=None):
        super().__init__(parent, Qt.WindowType.Popup)
        self.setObjectName("gpanel"); self.setFixedWidth(400)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAutoFillBackground(False)
        lay = QVBoxLayout(self); lay.setContentsMargins(14, 12, 14, 14); lay.setSpacing(10)
        head = QHBoxLayout(); t = QLabel("Genres")
        t.setStyleSheet("color:#e2e8f0;font-weight:800;font-size:13px;background:transparent;border:none;")
        clr = QPushButton("Clear all"); clr.setCursor(Qt.CursorShape.PointingHandCursor); clr.setFixedHeight(26)
        clr.setStyleSheet("QPushButton{background:transparent;color:#94a3b8;border:none;font-weight:700;"
                          "font-size:11px;}QPushButton:hover{color:#ffffff;}")
        clr.clicked.connect(self._clear_all)
        head.addWidget(t); head.addStretch(); head.addWidget(clr); lay.addLayout(head)
        grid = QGridLayout(); grid.setSpacing(8); self.chips = {}
        for i, g in enumerate(genres):
            b = QPushButton(g); b.setCheckable(True); b.setChecked(g in selected); b.setFixedHeight(30)
            b.setCursor(Qt.CursorShape.PointingHandCursor); b.setStyleSheet(self._CHIP)
            b.toggled.connect(lambda _on: self.changed.emit()); grid.addWidget(b, i // 3, i % 3); self.chips[g] = b
        lay.addLayout(grid)
        o = self._o = dict(DEF_OPTS, **(opts or {})); self._heads, self._opt_btns = [], []
        def head(text):
            h = QLabel(text); self._heads.append(h); lay.addWidget(h)
        def row():
            r = QHBoxLayout(); r.setSpacing(8); lay.addLayout(r); return r
        def chip(text, on):
            b = QPushButton(text); b.setCheckable(True); b.setChecked(on); b.setFixedHeight(30)
            b.setCursor(Qt.CursorShape.PointingHandCursor); self._opt_btns.append(b); return b
        head("Sort by"); r = row(); self._sort = {}
        for s in SORTS:
            b = chip(s, s == o["sort"]); b.clicked.connect(lambda _=False, s=s: self._pick_sort(s)); r.addWidget(b); self._sort[s] = b
        self._dir = chip("", False); self._dir.setCheckable(False); self._dir.setFixedWidth(34)
        self._dir.clicked.connect(self._flip); r.addWidget(self._dir); self._dir_txt()
        head("Season / year"); r = row(); self._seas = {}
        for s in SEASONS:
            b = chip(s, s == o["season"]); b.clicked.connect(lambda _=False, s=s: self._pick("season", self._seas, s)); r.addWidget(b); self._seas[s] = b
        self._year = QComboBox(); self._year.setFixedSize(120, 30); self._year.setMaxVisibleItems(12)
        self._year.addItem("Any year", None)
        for y in range(time.localtime().tm_year + 1, 1968, -1): self._year.addItem(str(y), y)
        self._year.setCurrentIndex(max(0, self._year.findData(o["year"])))
        self._year.currentIndexChanged.connect(self._on_year)
        r = row(); r.addWidget(self._year); r.addStretch()
        head("Status"); r = row(); self._stat = {}
        for s in STATUSES:
            b = chip(s, s == o["status"]); b.clicked.connect(lambda _=False, s=s: self._pick("status", self._stat, s)); r.addWidget(b); self._stat[s] = b
        r.addStretch()
        self._title, self._clr = t, clr
        theme.changed.connect(self._restyle); self._restyle()

    def _pick_sort(self, s):
        if s != self._o["sort"]: self._o["desc"] = s != "Title"
        for k, b in self._sort.items(): b.setChecked(k == s)
        self._o["sort"] = s; self._dir_txt(); self.changed.emit()

    def _flip(self): self._o["desc"] = not self._o["desc"]; self._dir_txt(); self.changed.emit()

    def _dir_txt(self): self._dir.setText("\u2193" if self._o["desc"] else "\u2191")

    def _pick(self, key, grp, s):
        on = grp[s].isChecked()
        for k, b in grp.items(): b.setChecked(on and k == s)
        self._o[key] = s if on else None; self.changed.emit()

    def _on_year(self, _i): self._o["year"] = self._year.currentData(); self.changed.emit()

    def options(self): return dict(self._o)

    def _clear_all(self):
        self._o.update(DEF_OPTS)
        for grp, sel in ((self._sort, "Popularity"), (self._seas, None), (self._stat, None)):
            for k, b in grp.items(): b.setChecked(k == sel)
        self._year.blockSignals(True); self._year.setCurrentIndex(0); self._year.blockSignals(False)
        self._dir_txt(); self.set_selected(set(), emit=True)

    def _restyle(self):
        d = theme.derived()
        self._title.setStyleSheet(f"/*raw*/color:{d['text_primary']};font-weight:800;font-size:13px;background:transparent;border:none;")
        self._clr.setStyleSheet(f"/*raw*/QPushButton{{background:transparent;color:{d['text_secondary']};border:none;font-weight:700;"
                                f"font-size:11px;}}QPushButton:hover{{color:{d['text_primary']};}}")
        chip = (f"/*raw*/QPushButton{{background:{d['button_background']};color:{d['button_text']};border:1.5px solid {d['card_border']};"
                f"border-radius:8px;padding:0 10px;font-weight:600;font-size:11px;}}QPushButton:hover{{border-color:{d['accent']};}}"
                f"QPushButton:checked{{background:{d['accent']};color:{d['text_on_accent']};border-color:{d['accent']};}}")
        for b in list(self.chips.values()) + self._opt_btns: b.setStyleSheet(chip)
        for h in self._heads: h.setStyleSheet(f"/*raw*/color:{d['text_secondary']};font-weight:800;font-size:11px;background:transparent;border:none;")
        self._year.setStyleSheet(f"/*raw*/QComboBox{{background:{d['button_background']};color:{d['button_text']};border:1.5px solid {d['card_border']};"
                                 f"border-radius:8px;padding:0 6px;font-weight:600;font-size:11px;}}QComboBox:hover{{border-color:{d['accent']};}}"
                                 f"QComboBox::drop-down{{border:none;width:22px;}}"
                                 f"QComboBox QAbstractItemView{{background:{d['button_background']};color:{d['button_text']};"
                                 f"border:1px solid {d['card_border']};selection-background-color:{d['accent']};"
                                 f"selection-color:{d['text_on_accent']};outline:none;}}")
        self.update()

    def paintEvent(self, e):
        p = QPainter(self); p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setBrush(theme.c("card_background")); p.setPen(QPen(theme.c("card_border"), 1.5))
        p.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), 12, 12)

    def selected(self): return {g for g, b in self.chips.items() if b.isChecked()}

    def set_selected(self, sel, emit=False):
        for g, b in self.chips.items():
            b.blockSignals(True); b.setChecked(g in sel); b.blockSignals(False)
        if emit: self.changed.emit()


class _TitlesWorker(QThread):
    done = pyqtSignal(list)

    def run(self):
        try:
            from hayate.providers.builtins.ytdlp_provider import YtDlpProvider
            self.done.emit(YtDlpProvider().catalogue_titles())
        except Exception as e:
            print(f"[catalogue] titles error: {e}"); self.done.emit([])


class _KitsuWorker(QThread):
    chunk = pyqtSignal(int, list, int)  # gen, medias, total (-1 = failed)
    done = pyqtSignal(int)

    def __init__(self, gen, off, count, cap, text='', cats=(), extra=()):
        super().__init__(); self.gen, self.off, self.count, self.cap, self.abort = gen, off, count, cap, False
        self.text, self.cats = text, tuple(cats); self.extra = tuple(extra)

    def run(self):
        try:
            from concurrent.futures import ThreadPoolExecutor
            from hayate.core import kitsu
            L = kitsu.LIMIT; end = min(self.off + self.count, self.cap)
            cs = list(range(self.off // L, -(-end // L))); seen = set()
            with ThreadPoolExecutor(4) as ex:
                futs = [ex.submit(kitsu.get_chunk, c, self.text, self.cats, self.extra) for c in cs]
                for c, f in zip(cs, futs):  # emit in order as soon as each chunk is ready
                    if self.abort:
                        for g in futs: g.cancel()
                        return
                    items, total = f.result()
                    lo, hi = max(self.off, c * L) - c * L, min(end, c * L + L) - c * L
                    out = []
                    for m in items[lo:hi]:
                        if m and m.id not in seen: seen.add(m.id); out.append(m)
                    self.chunk.emit(self.gen, out, total)
            kitsu.save(); self.done.emit(self.gen)
        except Exception as e:
            print(f"[catalogue] kitsu error: {e}"); self.chunk.emit(self.gen, [], -1); return
        try:  # quietly warm the next page
            for c in range(end // L, -(-min(end + self.count, self.cap) // L)):
                if self.abort: break
                kitsu.get_chunk(c, self.text, self.cats, self.extra)
            kitsu.save()
        except Exception: pass


class _Resolver(QThread):
    """Matches titles to AniList in small batches, lowest priority key first; failures retry up to 3x."""
    resolved = pyqtSignal(str, object)
    BATCH = 8

    def __init__(self):
        super().__init__(); self.q, self.done, self.tries, self._stop = PriorityQueue(), set(), {}, False

    def request(self, t, prio=(0, 0)):
        if t in self.done: return False
        self.q.put((prio, t)); return True

    def stop(self): self._stop = True

    def _bgm(self, t):
        """Japanese name(s) for a Chinese title via Bangumi (cached on disk)."""
        import json, urllib.parse, urllib.request
        from hayate.core import net
        from hayate.core.settings import config_dir
        if not hasattr(self, "_bc"):
            self._bf = config_dir() / "catalogue_bgm.json"
            try: self._bc = json.loads(self._bf.read_text())
            except Exception: self._bc = {}
        if t in self._bc: return self._bc[t]
        qs = [t]
        q2 = re.sub(r"\s*第\s*[\d一二三四五六七八九十]+\s*[季期部]\s*$", "", t).strip()
        if q2 and q2 != t: qs.append(q2)
        names = []
        for q in qs:
            try:
                u = "https://api.bgm.tv/search/subject/" + urllib.parse.quote(q) + "?type=2&max_results=1"
                with net.client(timeout=10, headers={"User-Agent": "Hayate/0.2"}) as _c:
                    _resp = _c.get(u)
                    _resp.raise_for_status()
                    r = _resp.json()
                names = [x["name"] for x in (r.get("list") or [])[:1] if x.get("name")]
            except Exception as e:
                print(f"[catalogue] bangumi failed for {q!r}: {e}"); return []
            time.sleep(0.3)
            if names: break
        self._bc[t] = names
        try:
            self._bf.parent.mkdir(parents=True, exist_ok=True)
            self._bf.write_text(json.dumps(self._bc, ensure_ascii=False))
        except Exception: pass
        return names

    def _ladder(self, t):
        base = _variants(t)
        if not _CJK.search(t): return base
        jp = []
        for n in self._bgm(t):
            for v in _variants(n) + [re.sub(r"(?<=[^\sA-Za-z])[IVX]+$", "", _norm(n)).strip()]:
                if len(v) >= 2 and v not in jp: jp.append(v)
        rest = [v for v in base if not _CJK.search(v)]
        return (jp + rest) or base

    def _fetch(self, titles):
        from hayate.core.anilist import search_anime_sync, search_many_sync
        lad = {t: self._ladder(t) for t in titles}
        res, pending = {}, list(titles)
        for k in range(max(len(v) for v in lad.values())):
            qs = [(t, lad[t][k]) for t in pending if k < len(lad[t])]
            if not qs: break
            try: ms = search_many_sync([q for _, q in qs])
            except Exception as e:
                print(f"[catalogue] batch failed ({e}); falling back to single lookups")
                ms = []
                for _, q in qs:
                    try:
                        r = search_anime_sync(q, per_page=1)
                        if isinstance(r, tuple): r = r[0]
                        ms.append(r[0] if r else None)
                    except Exception as e2:
                        print(f"[catalogue] match failed for {q!r}: {e2}"); ms.append(_FAIL)
                    time.sleep(0.35)
            for (t, _), m in zip(qs, ms):
                if m is _FAIL: res[t] = (None, False); pending.remove(t)
                elif m is not None: res[t] = (m, True); pending.remove(t)
            time.sleep(0.35)
        for t in pending:
            print(f"[catalogue] no match: {t!r}"); res[t] = (None, True)
        return [res[t] for t in titles]

    def run(self):
        while not self._stop:
            try: batch = [self.q.get(timeout=0.5)]
            except Empty: continue
            while len(batch) < self.BATCH:
                try: batch.append(self.q.get_nowait())
                except Empty: break
            seen, todo = set(), []
            for prio, t in batch:
                if t not in self.done and t not in seen: seen.add(t); todo.append((prio, t))
            if not todo: continue
            res, all_ok = self._fetch([t for _, t in todo]), True
            for (prio, t), (m, ok) in zip(todo, res):
                if ok: self.done.add(t)
                else:
                    all_ok = False; self.tries[t] = self.tries.get(t, 0) + 1
                    if self.tries[t] < 3: self.q.put((prio, t))
                self.resolved.emit(t, m)
            time.sleep(0.7 if all_ok else 2.0)


class CatalogueView(QWidget):
    back_requested = pyqtSignal()
    card_clicked = pyqtSignal(object)
    card_hovered = pyqtSignal(object, QPoint, QWidget)
    scrolled = pyqtSignal()

    def __init__(self):
        super().__init__()
        self._items, self._rows, self._cols = [], {}, 0
        self._all, self._page, self._gen = [], 0, 0
        self._view, self._idx, self._byid, self._dupes = [], {}, {}, set()
        self._ref_timer = QTimer(self); self._ref_timer.setSingleShot(True); self._ref_timer.setInterval(400)
        self._ref_timer.timeout.connect(self._refresh_view)
        self._media, self._loaded, self._n = {}, False, 0
        self._resolver = self._tw = None
        self._mode, self._g_total, self._gmedia, self._gworkers = "inapp", 0, {}, []
        self.setObjectName("pageroot"); self.setStyleSheet("#pageroot{background:#171722;}")
        root = QVBoxLayout(self); root.setContentsMargins(40, 24, 40, 0)
        top = QHBoxLayout(); b = QPushButton("\u2190  Back"); b.clicked.connect(self.back_requested.emit)
        top.addWidget(b); top.addSpacing(12)
        top.addWidget(QLabel("<span style='font-size:20px;font-weight:800'>Catalogue</span>"))
        self.count_lbl = QLabel(); top.addWidget(self.count_lbl); top.addSpacing(24)
        self.search = QLineEdit(); self.search.setPlaceholderText('Search catalogue\u2026'); self.search.setFixedHeight(38)
        self.search.setStyleSheet('QLineEdit{background:#1e1e2d;color:#e2e8f0;border:1.5px solid #2f2f44;'
                                  'border-radius:8px;padding:0 12px;}QLineEdit:focus{border-color:#6366f1;}')
        self.filter_btn = QPushButton('Filter'); self.filter_btn.setFixedHeight(38); self.filter_btn.setMinimumWidth(68)
        self.filter_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.filter_btn.setStyleSheet('QPushButton{background:#1e1e2d;color:#cbd5e1;border:1.5px solid #2f2f44;'
                                      'border-radius:8px;font-weight:700;}QPushButton:hover{border-color:#6366f1;}')
        top.addWidget(self.search, 1); top.addWidget(self.filter_btn); top.addSpacing(12)
        self._q, self._genres, self._gp, self._st = '', set(), None, {}; self._opts = dict(DEF_OPTS)
        self._qt = QTimer(self); self._qt.setSingleShot(True); self._qt.setInterval(350)
        self._qt.timeout.connect(self._on_query)
        self.search.textChanged.connect(lambda _t: self._qt.start())
        self.filter_btn.clicked.connect(self._open_filter)
        self._pill_anims, self._pl, self._pr = [], 0.0, 0.0
        sw = self._switcher = QFrame()
        sw.setStyleSheet("QFrame{background-color:#212130;border-radius:8px;border:1.5px solid #3d3d52;}")
        sl = QHBoxLayout(sw); sl.setContentsMargins(3, 3, 3, 3); sl.setSpacing(4)
        self._mode_btns = {}
        for key, text in (("inapp", "In-App"), ("global", "Global")):
            mb = QPushButton(text); mb.setFixedHeight(32); mb.setCursor(Qt.CursorShape.PointingHandCursor)
            mb.clicked.connect(lambda _=False, k=key: self.set_mode(k)); sl.addWidget(mb); self._mode_btns[key] = mb
        top.addWidget(sw)
        self._pill = QFrame(sw); self._pill.setObjectName("pill")
        self._pill.setStyleSheet("QFrame#pill{background-color:#38bdf8;border:none;border-radius:6px;}")
        self._pill.lower(); sw.installEventFilter(self)
        self._style_modes()
        root.addLayout(top)
        root.addSpacing(16)
        self.scroll = QScrollArea(); self.scroll.setWidgetResizable(True); self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setStyleSheet("QScrollArea{background:transparent;border:none;}")
        self.scroll.viewport().setAutoFillBackground(False)
        self.content = QWidget(); self.content.setStyleSheet("background:transparent;")
        self.scroll.setWidget(self.content); root.addWidget(self.scroll, 1)
        self.pager_bar = QWidget(); self.pager_bar.setStyleSheet('background:transparent;')
        _pl = QHBoxLayout(self.pager_bar); _pl.setContentsMargins(0, 8, 0, 12); _pl.setSpacing(6)
        root.addWidget(self.pager_bar)
        self.scroll.verticalScrollBar().valueChanged.connect(self._on_scroll)
        QApplication.instance().aboutToQuit.connect(self._shutdown)

    # ---- data ----
    def _cache_file(self):
        from hayate.core.settings import config_dir
        return config_dir() / "catalogue_media.pkl"

    def load(self):
        if self._loaded: return
        self._loaded = True
        try: self._media = pickle.loads(self._cache_file().read_bytes())
        except Exception: self._media = {}
        self.count_lbl.setText("   loading\u2026")
        w = self._tw = _TitlesWorker(); w.done.connect(self._on_titles); w.start()

    def _on_titles(self, titles):
        if not titles:
            self._loaded = False; self.count_lbl.setText("   no titles found"); return
        seen, items = set(), []
        for t in map(_clean, titles):
            if HIDE_CJK_ONLY and not re.search(r"[A-Za-z]{3}", _norm(t)): continue
            if t and t.lower() not in seen: seen.add(t.lower()); items.append(t)
        self._all = items; self._cols = self._calc_cols()
        self._idx = {t: i for i, t in enumerate(items)}; self._byid, self._dupes = {}, set()
        for t in items:
            m = self._media.get(t)
            if m: self._register(t, m)
        self._view = self._filtered()
        if self._mode == "inapp":
            self.count_lbl.setText(f"   {len(self._view)} titles")
            self._set_page(0); self._kick()

    def _register(self, t, m):
        """Track which titles resolve to the same AniList id; earliest in the list wins. True if new dupes."""
        mid = getattr(m, "id", None)
        if mid is None or t not in self._idx: return False
        lst = self._byid.setdefault(mid, [])
        if t not in lst: lst.append(t)
        if len(lst) < 2: return False
        lst.sort(key=self._idx.get)
        losers = set(lst[1:])
        if losers <= self._dupes: return False
        self._dupes |= losers; return True

    def _refresh_view(self):
        if self.scroll.viewport().underMouse(): self._ref_timer.start(); return  # don't rebuild cards under the cursor
        self._view = self._filtered()
        if self._mode != "inapp": return
        self.count_lbl.setText(self._count_text())
        v = self.scroll.verticalScrollBar().value()
        self._set_page(self._page)
        QTimer.singleShot(0, lambda: self.scroll.verticalScrollBar().setValue(v))

    def _per_page(self): return ROWS_PER_PAGE * max(1, self._cols)

    def _set_page(self, p):
        if self._mode == "global": return self._set_gpage(p)
        self._gen += 1
        per = self._per_page(); pages = max(1, -(-len(self._view) // per))
        self._page = max(0, min(p, pages - 1))
        self._items = self._view[self._page * per:(self._page + 1) * per]
        self._clear_rows()
        self.content.setMinimumHeight(-(-len(self._items) // max(1, self._cols)) * PITCH)
        self.scroll.verticalScrollBar().setValue(0)
        self._rebuild_pager(pages)
        self._update_rows()

    @staticmethod
    def _page_list(total, cur):
        if total <= 7: return list(range(total))
        if cur <= 3: return [0, 1, 2, 3, 4, None, total - 1]
        if cur >= total - 4: return [0, None] + list(range(total - 5, total))
        return [0, None, cur - 1, cur, cur + 1, None, total - 1]

    def _rebuild_pager(self, pages):
        lay = self.pager_bar.layout()
        while lay.count():
            w = lay.takeAt(0).widget()
            if w: w.hide(); w.deleteLater()
        self.pager_bar.setVisible(pages > 1)
        cur = self._page
        def btn(text, p, on=True, active=False):
            b = QPushButton(text); b.setFixedSize(36, 32); b.setEnabled(on)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            bg, fg, bd = ("#6366f1", "#ffffff", "#6366f1") if active else ("#1e1e2d", "#cbd5e1", "#2f2f44")
            b.setStyleSheet(f"QPushButton{{background:{bg};color:{fg};border:1.5px solid {bd};"
                            f"border-radius:8px;font-weight:700;}}QPushButton:disabled{{color:#475569;}}"
                            f"QPushButton:hover:enabled{{border-color:#6366f1;}}")
            b.clicked.connect(lambda _=False, p=p: self._set_page(p)); lay.addWidget(b)
        lay.addStretch(); btn("\u2039", cur - 1, cur > 0)
        for p in self._page_list(pages, cur):
            if p is None:
                l = QLabel("\u2026"); l.setStyleSheet("color:#64748b;background:transparent;"); lay.addWidget(l)
            else: btn(str(p + 1), p, active=(p == cur))
        btn("\u203a", cur + 1, cur < pages - 1); lay.addStretch()

    def _style_modes(self):
        on = ("QPushButton{background-color:transparent;color:#090a0f;font-weight:800;font-size:11px;"
              "border-radius:6px;border:none;padding:0 12px;}")
        off = ("QPushButton{background-color:transparent;color:#94a3b8;font-weight:600;font-size:11px;"
               "border-radius:6px;border:none;padding:0 12px;}QPushButton:hover{color:#ffffff;}")
        for k, b in self._mode_btns.items(): b.setStyleSheet(on if k == self._mode else off)
        self._glide_pill(True)

    def eventFilter(self, obj, ev):
        if obj is getattr(self, "_switcher", None) and ev.type() in (QEvent.Type.Resize, QEvent.Type.Show):
            QTimer.singleShot(0, self._pill_resync)
        return super().eventFilter(obj, ev)

    def _pill_resync(self):
        self._glide_pill(any(a.state() == QVariantAnimation.State.Running for a in self._pill_anims))

    def _glide_pill(self, animate):
        if not hasattr(self, "_pill"): return
        btn = self._mode_btns[self._mode]
        self._switcher.layout().activate()
        g = btn.geometry()
        tl, tr, top, h = float(g.left()), float(g.right() + 1), g.top(), g.height()
        for a in self._pill_anims: a.stop()
        self._pill_anims = []

        def apply(): self._pill.setGeometry(round(self._pl), top, max(1, round(self._pr - self._pl)), h)

        if not animate or not self._switcher.isVisible() or self._pr <= self._pl:
            self._pl, self._pr = tl, tr; apply(); return
        going_right = tl >= self._pl

        def edge(attr, end, ms):
            a = QVariantAnimation(); a.setStartValue(getattr(self, attr)); a.setEndValue(end)
            a.setDuration(ms); a.setEasingCurve(QEasingCurve.Type.OutCubic)
            a.valueChanged.connect(lambda v, at=attr: (setattr(self, at, float(v)), apply()))
            a.start(); self._pill_anims.append(a)

        edge("_pr", tr, 220 if going_right else 380)  # leading edge fast
        edge("_pl", tl, 380 if going_right else 220)  # trailing edge lags

    def set_mode(self, mode):
        if mode == self._mode: return
        if self._mode == 'inapp': self._ipage = self._page
        else: self._gpage = self._page
        self._qt.stop(); self._st[self._mode] = (self.search.text(), set(self._genres))
        self._mode = mode; self._style_modes()
        q, g = self._st.get(mode, ('', set())); self._q, self._genres = q.strip(), set(g)
        self.search.blockSignals(True); self.search.setText(q); self.search.blockSignals(False)
        self._sync_filter_btn()
        self._items = []; self._clear_rows()
        self.count_lbl.setText("   loading\u2026" if mode == "global" else (f"   {len(self._view)} titles" if self._view else ""))
        self._cols = self._calc_cols()
        if mode == 'inapp':
            self._view = self._filtered()
            if self._all: self.count_lbl.setText(self._count_text())
            self._kick()
        self._set_page(getattr(self, '_gpage' if mode == 'global' else '_ipage', 0))

    def _set_gpage(self, p):
        self._gen += 1
        for w in self._gworkers: w.abort = True
        per = self._per_page()
        if not self._g_total: self._g_total = KITSU_MAX
        pages = max(1, -(-self._g_total // per))
        self._page = max(0, min(p, pages - 1))
        start = self._page * per; n = max(0, min(per, self._g_total - start))
        self._gmedia, self._gfilled = {}, 0
        self._items = [f"g{i}" for i in range(n)]  # empty slots show instantly, filled as chunks arrive
        self._clear_rows(); self.content.setMinimumHeight(-(-n // max(1, self._cols)) * PITCH)
        self.scroll.verticalScrollBar().setValue(0)
        self._rebuild_pager(pages)
        w = _KitsuWorker(self._gen, start, n, KITSU_MAX, self._q, [GENRE_SLUG[g] for g in sorted(self._genres) if g in GENRE_SLUG], self._kextra())
        w.chunk.connect(self._on_gchunk); w.done.connect(self._on_gdone)
        self._gworkers = [x for x in self._gworkers if x.isRunning()] + [w]; w.start()
        self._update_rows()

    def _kextra(self):
        o, x = self._opts, []
        if o["sort"] != "Popularity" or not o["desc"]: x.append(("sort", ("-" if o["desc"] else "") + KSORT[o["sort"]]))
        if o["season"]: x.append(("filter[season]", o["season"].lower()))
        if o["year"]: x.append(("filter[seasonYear]", str(o["year"])))
        if o["status"]: x.append(("filter[status]", STATUSES[o["status"]][0]))
        return x

    def _on_gchunk(self, gen, items, total):
        if gen != self._gen or self._mode != "global": return
        if total < 0: self.count_lbl.setText("   Kitsu unreachable"); return
        real = min(total, KITSU_MAX)
        if real != self._g_total:
            self._g_total = real; self._rebuild_pager(max(1, -(-real // self._per_page())))
        self.count_lbl.setText(f"   {real:,} titles")
        for m in items:
            if self._gfilled >= len(self._items): break
            key = self._items[self._gfilled]; self._gfilled += 1; self._gmedia[key] = m
            for row in self._rows.values():
                ph = row._ph.pop(key, None)
                if ph: ph[0].deleteLater(); self._add_card(row, m, ph[1])

    def _on_gdone(self, gen):
        if gen != self._gen or self._mode != "global": return
        left = self._items[self._gfilled:]
        if not left: return  # unmapped/duplicate titles leave a few empty trailing slots: drop them
        self._items = self._items[:self._gfilled]
        for row in self._rows.values():
            for key in left:
                ph = row._ph.pop(key, None)
                if ph: ph[0].deleteLater()
        self.content.setMinimumHeight(-(-len(self._items) // max(1, self._cols)) * PITCH)

    # ---- search / filter ----
    @staticmethod
    def _linked():
        try:
            from hayate.core.settings import load_settings
            return bool(load_settings().get("catalogue_link", True))
        except Exception: return True

    def _filtered(self):
        q = _norm(self._q).lower().strip(); out = []
        for t in self._all:
            if t in self._dupes: continue
            if q and q not in _norm(t).lower(): continue
            m = self._media.get(t)
            if self._genres and (not m or not self._genres <= set(m.genres or [])): continue
            if self._media_opts() and not (m and self._opt_ok(m)): continue
            out.append(t)
        return self._sort_view(out)

    def _media_opts(self): o = self._opts; return bool(o["season"] or o["year"] or o["status"])

    def _opt_ok(self, m):
        o = self._opts
        if o["year"] and getattr(m, "season_year", None) != o["year"]: return False
        if o["season"] and str(getattr(m, "season", "") or "").upper() != o["season"].upper(): return False
        if o["status"] and str(getattr(m, "status", "") or "").upper() != STATUSES[o["status"]][1]: return False
        return True

    def _sort_view(self, out):
        o = self._opts
        if o["sort"] == "Title": return sorted(out, key=lambda t: _norm(t).lower(), reverse=o["desc"])
        f = {"Popularity": lambda m: getattr(m, "popularity", None),
             "Score": lambda m: getattr(m, "average_score", None), "Released": lambda m: getattr(m, "season_year", None)}[o["sort"]]
        have, miss = [], []
        for t in out:
            m = self._media.get(t); v = f(m) if m else None
            (have if v is not None else miss).append((v, t))
        have.sort(key=lambda x: x[0], reverse=o["desc"])
        return [t for _, t in have] + [t for _, t in miss]

    def _match_progress(self):
        r = self._resolver; d = r.done if r else ()
        n = sum(1 for t in self._all if t in self._media or t in d or (r and r.tries.get(t, 0) >= 3))
        return n, len(self._all)

    def _count_text(self):
        t = f"   {len(self._view)} titles"
        if self._mode == "inapp" and (self._genres or self._opts["sort"] != "Title") and self._all:
            n, tot = self._match_progress()
            if n < tot: t += f"  \u00b7  matching {n}/{tot}"
        return t

    def _kick(self):  # genres exist only on AniList-matched titles: match the rest in the background
        if not (self._genres or self._media_opts() or self._opts["sort"] != "Title"): return
        for i, t in enumerate(self._all):
            if t not in self._media: self._ensure_resolver().request(t, (1, i))

    def _on_query(self):
        q = self.search.text().strip()
        if q == self._q: return
        self._q = q
        if q and self._genres and not self._linked():
            self._genres = set(); self._sync_filter_btn()
        self._apply()

    def _on_genres_changed(self, sel):
        self._genres = set(sel)
        if sel and self.search.text().strip() and not self._linked():
            self._qt.stop(); self._q = ""
            self.search.blockSignals(True); self.search.clear(); self.search.blockSignals(False)
        self._sync_filter_btn(); self._apply()

    def _on_panel(self, gp):
        self._opts = gp.options(); self._on_genres_changed(gp.selected())

    def _sync_filter_btn(self):
        o = self._opts; n = len(self._genres) + sum(bool(o[k]) for k in ("season", "year", "status")) + (o["sort"] != "Popularity" or not o["desc"])
        self.filter_btn.setText(f"Filter ({n})" if n else "Filter")

    def _open_filter(self):
        try:
            from hayate.core.settings import load_settings
            adult = bool(load_settings().get("allow_adult", False))
        except Exception: adult = False
        if self._gp:
            try: self._gp.deleteLater()
            except RuntimeError: pass
        gp = self._gp = _GenrePanel(self, [g for g in GENRES if adult or g != "Ecchi"], self._genres, self._opts)
        gp.changed.connect(lambda: self._on_panel(gp))
        gp.adjustSize(); b = self.filter_btn
        gp.move(b.mapToGlobal(QPoint(b.width() - gp.width(), b.height() + 6))); gp.show()

    def _apply(self):
        if self._mode == "global":
            self._g_total = 0; self._set_gpage(0); return
        self._view = self._filtered()
        self.count_lbl.setText(self._count_text())
        self._set_page(0); self._kick()

    def _ensure_resolver(self):
        if self._resolver is None:
            self._resolver = _Resolver(); self._resolver.resolved.connect(self._on_resolved); self._resolver.start()
        return self._resolver

    def _on_resolved(self, title, media):
        if media is None:
            if self._genres and self._mode == 'inapp' and self._all: self.count_lbl.setText(self._count_text())
            return
        self._media[title] = media
        if self._mode == "inapp" and (self._media_opts() or self._opts["sort"] != "Title") and not self._ref_timer.isActive(): self._ref_timer.start()
        if self._register(title, media): self._ref_timer.start()
        elif self._genres and self._mode == 'inapp' and self._genres <= set(media.genres or []):
            if not self._ref_timer.isActive(): self._ref_timer.start()
        for row in self._rows.values():
            ph = row._ph.pop(title, None)
            if ph: ph[0].deleteLater(); self._add_card(row, media, ph[1])
        self._n += 1
        if self._genres and self._mode == 'inapp' and self._all: self.count_lbl.setText(self._count_text())
        if self._n % 12 == 0: self._save()

    def _save(self):
        try:
            f = self._cache_file(); f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes(pickle.dumps(self._media))
        except Exception: pass

    def _shutdown(self):
        for w in self._gworkers: w.abort = True
        for w in self._gworkers: w.wait(2000)
        if self._resolver: self._resolver.stop(); self._resolver.wait(2000)
        self._save()

    # ---- grid ----
    def _on_scroll(self, _=0): self.scrolled.emit(); self._update_rows()

    def showEvent(self, e): super().showEvent(e); QTimer.singleShot(0, self._update_rows)

    def resizeEvent(self, e): super().resizeEvent(e); QTimer.singleShot(0, self._update_rows)

    def _calc_cols(self): return max(1, (self.scroll.viewport().width() + GAP) // (CARD_W + GAP))

    def _clear_rows(self):
        for w in self._rows.values(): w.deleteLater()
        self._rows.clear()

    def _update_rows(self):
        cols = self._calc_cols()
        if cols != self._cols:
            first = self._page * self._per_page()
            self._cols = cols
            self._set_page(first // self._per_page()); return
        n = -(-len(self._items) // cols)
        if not n: return
        top, h = self.scroll.verticalScrollBar().value(), self.scroll.viewport().height()
        mid = (top + h // 2) // PITCH
        first = max(0, min(mid - WINDOW // 2, n - WINDOW)); last = min(n - 1, first + WINDOW - 1)
        for r in [r for r in self._rows if r < first or r > last]:
            self._rows.pop(r).deleteLater()
        x0 = max(0, (self.scroll.viewport().width() - (cols * CARD_W + (cols - 1) * GAP)) // 2)
        for r in sorted(range(first, last + 1), key=lambda r: -abs(r - mid)):
            row = self._rows.get(r)
            if row is None: row = self._rows[r] = self._build_row(r, cols)
            row.move(x0, r * PITCH); row.show()

    def _add_card(self, row, media, x):
        from hayate.ui.main_window import PosterCard
        c = PosterCard(media, priority=50, parent=row); c.move(x, 0)
        c.clicked.connect(self.card_clicked.emit); c.hovered.connect(self.card_hovered.emit); c.show()

    def _build_row(self, r, cols):
        row = QWidget(self.content); row.setFixedSize(cols * CARD_W + (cols - 1) * GAP, CARD_H)
        row.setStyleSheet("background:transparent;"); row._ph = {}
        todo = []; glob = self._mode == "global"
        for i, t in enumerate(self._items[r * cols:(r + 1) * cols]):
            x, m = i * (CARD_W + GAP), (self._gmedia if glob else self._media).get(t)
            if m: self._add_card(row, m, x); continue
            ph = QLabel("" if glob else t, row); ph.setWordWrap(True); ph.setAlignment(Qt.AlignmentFlag.AlignCenter)
            ph.setFixedSize(CARD_W, CARD_H); ph.move(x, 0); ph.show()
            ph.setStyleSheet("background:#1e1e2d;border:1.5px solid #2f2f44;border-radius:12px;"
                             "color:#94a3b8;padding:12px;font-weight:700;")
            row._ph[t] = (ph, x)
            if not glob: todo.append((t, r * cols + i))
        for t, pos in todo: self._ensure_resolver().request(t, (-self._gen, pos))  # newest page, then top-left first
        return row
