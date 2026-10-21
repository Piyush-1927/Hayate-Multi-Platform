import pickle, re, time, unicodedata
from queue import PriorityQueue, Empty
from PyQt6.QtCore import QPoint, QThread, QTimer, Qt, pyqtSignal
from PyQt6.QtWidgets import (QApplication, QFrame, QHBoxLayout, QLabel, QPushButton,
                             QScrollArea, QVBoxLayout, QWidget)

CARD_W, CARD_H, GAP = 185, 305, 20
PITCH = CARD_H + GAP
WINDOW = 10  # rows kept alive, viewport row in the middle
ROWS_PER_PAGE = 10  # rows per page (set 8 for shorter pages)
HIDE_CJK_ONLY = False  # hide titles with no Latin letters (AniList can't match them)
_TAIL = re.compile(r"\s*[-\u2013|]\s*(now on|streaming|available)\b.*$", re.I)


def _clean(t): return _TAIL.sub("", t).strip()

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


class _TitlesWorker(QThread):
    done = pyqtSignal(list)

    def run(self):
        try:
            from hayate.providers.builtins.ytdlp_provider import YtDlpProvider
            self.done.emit(YtDlpProvider().catalogue_titles())
        except Exception as e:
            print(f"[catalogue] titles error: {e}"); self.done.emit([])


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
        self.setObjectName("pageroot"); self.setStyleSheet("#pageroot{background:#171722;}")
        root = QVBoxLayout(self); root.setContentsMargins(40, 24, 40, 0)
        top = QHBoxLayout(); b = QPushButton("\u2190  Back"); b.clicked.connect(self.back_requested.emit)
        top.addWidget(b); top.addSpacing(12)
        top.addWidget(QLabel("<span style='font-size:20px;font-weight:800'>Catalogue</span>"))
        self.count_lbl = QLabel(); top.addWidget(self.count_lbl); top.addStretch()
        root.addLayout(top)
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
            if HIDE_CJK_ONLY and not re.search(r"[A-Za-z]", _norm(t)): continue
            if t and t.lower() not in seen: seen.add(t.lower()); items.append(t)
        self._all = items; self._cols = self._calc_cols()
        self._idx = {t: i for i, t in enumerate(items)}; self._byid, self._dupes = {}, set()
        for t in items:
            m = self._media.get(t)
            if m: self._register(t, m)
        self._view = [t for t in items if t not in self._dupes]
        self.count_lbl.setText(f"   {len(self._view)} titles")
        self._set_page(0)

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
        self._view = [t for t in self._all if t not in self._dupes]
        self.count_lbl.setText(f"   {len(self._view)} titles")
        v = self.scroll.verticalScrollBar().value()
        self._set_page(self._page)
        QTimer.singleShot(0, lambda: self.scroll.verticalScrollBar().setValue(v))

    def _per_page(self): return ROWS_PER_PAGE * max(1, self._cols)

    def _set_page(self, p):
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

    def _ensure_resolver(self):
        if self._resolver is None:
            self._resolver = _Resolver(); self._resolver.resolved.connect(self._on_resolved); self._resolver.start()
        return self._resolver

    def _on_resolved(self, title, media):
        if media is None: return
        self._media[title] = media
        if self._register(title, media): self._ref_timer.start()
        for row in self._rows.values():
            ph = row._ph.pop(title, None)
            if ph: ph[0].deleteLater(); self._add_card(row, media, ph[1])
        self._n += 1
        if self._n % 12 == 0: self._save()

    def _save(self):
        try:
            f = self._cache_file(); f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes(pickle.dumps(self._media))
        except Exception: pass

    def _shutdown(self):
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
        todo = []
        for i, t in enumerate(self._items[r * cols:(r + 1) * cols]):
            x, m = i * (CARD_W + GAP), self._media.get(t)
            if m: self._add_card(row, m, x); continue
            ph = QLabel(t, row); ph.setWordWrap(True); ph.setAlignment(Qt.AlignmentFlag.AlignCenter)
            ph.setFixedSize(CARD_W, CARD_H); ph.move(x, 0); ph.show()
            ph.setStyleSheet("background:#1e1e2d;border:1.5px solid #2f2f44;border-radius:12px;"
                             "color:#94a3b8;padding:12px;font-weight:700;")
            row._ph[t] = (ph, x); todo.append((t, r * cols + i))
        for t, pos in todo: self._ensure_resolver().request(t, (-self._gen, pos))  # newest page, then top-left first
        return row
