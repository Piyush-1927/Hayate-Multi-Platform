"""Optional embedded web player (QtWebEngine + Widevine). WEB_OK is False when unavailable."""
import glob, os, re

CRAWL = bool(os.environ.get("HAYATE_CRAWL"))

WEB_OK = False
WebPane = None
_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
       "Chrome/124.0.0.0 Safari/537.36")


def _find_cdm():
    c = ["/usr/lib/chromium/WidevineCdm/_platform_specific/linux_x64/libwidevinecdm.so",
         "/opt/google/chrome/WidevineCdm/_platform_specific/linux_x64/libwidevinecdm.so"]
    c += glob.glob("/usr/lib/**/libwidevinecdm.so", recursive=True)
    c += sorted(glob.glob(os.path.expanduser("~/.config/*/**/libwidevinecdm.so"), recursive=True), reverse=True)
    return next((p for p in c if os.path.exists(p)), None)


_cdm = _find_cdm() if os.name == "posix" else None
if _cdm:
    os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = (
        os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", "") + f" --widevine-path={_cdm}").strip()
    try:
        from PyQt6.QtCore import QUrl
        from PyQt6.QtWebEngineCore import QWebEngineProfile, QWebEnginePage, QWebEngineSettings
        from PyQt6.QtWebEngineWidgets import QWebEngineView
        WEB_OK = True
    except Exception:
        WEB_OK = False

def _show_root(u):
    m = re.match(r"^(https?://[^/]+/in/(?:shows|movies)/[^/]+/\d+)", u or "")
    return m.group(1) if m else u


def _season_for(key):
    import json
    try:
        m = json.load(open(os.path.expanduser("~/.config/hayate/webplayer/seasons.json")))
    except (OSError, ValueError):
        return 0
    k = (key or "").lower()
    for name, n in m.items():
        if name.lower() in k:
            return int(n)
    return 0


def _direct_ep(k):
    import json
    try:
        return json.load(open(os.path.expanduser("~/.config/hayate/webplayer/episodes.json"))).get(k, "")
    except (OSError, ValueError):
        return ""


if WEB_OK:
    _PROFILE = None

    def _profile():
        global _PROFILE
        if _PROFILE is None:
            p = QWebEngineProfile("hayate-disney")
            p.setPersistentStoragePath(os.path.expanduser("~/.local/share/hayate/webplayer"))
            p.setHttpUserAgent(_UA)
            A = QWebEngineSettings.WebAttribute
            for a in (A.PlaybackRequiresUserGesture,):
                p.settings().setAttribute(a, False)
            for a in (A.FullScreenSupportEnabled, A.PluginsEnabled):
                p.settings().setAttribute(a, True)
            _PROFILE = p
        return _PROFILE

    class _Page(QWebEnginePage):
        def javaScriptConsoleMessage(self, level, msg, line, src):
            if msg.startswith("[hayate]"):
                print("js:", msg, flush=True)
                if msg.startswith("[hayate] open"):
                    self.parent()._autoplay = True
                if msg.startswith("[hayate] season"):
                    from PyQt6.QtCore import QTimer
                    QTimer.singleShot(1000, self.parent()._press_ep)
                if msg.startswith("[hayate] cr map "):
                    self.parent()._cr_got(msg[len("[hayate] cr map "):])
                if self.parent()._cr_active and (msg.startswith("[hayate] no episode") or " not found" in msg):
                    from PyQt6.QtCore import QTimer
                    QTimer.singleShot(500, self.parent()._cr_next)
                if msg.startswith("[hayate] more click "):
                    x, y = (int(v) for v in msg.split("click ", 1)[1].split()[:2])
                    self.parent()._real_click(x, y)
                if msg.startswith("[hayate] lang ") and msg.endswith(" not found") and not self.parent()._cr_active and self.parent()._cr_langs is not None:
                    from PyQt6.QtCore import QTimer
                    QTimer.singleShot(500, lambda t=self.parent()._cr_token: self.parent()._cr_next_lang(t))
                if msg.startswith("[hayate] lang ") and msg.endswith(" not found") and self.parent()._pending_watch:
                    from PyQt6.QtCore import QTimer
                    QTimer.singleShot(300, self.parent()._go_pending)
                if msg.startswith("[hayate] audio open "):
                    x, y = (int(v) for v in msg.split("open ", 1)[1].split()[:2])
                    self.parent()._aud_click(x, y)
                if msg.startswith("[hayate] audio pick "):
                    x, y = (int(v) for v in msg.split("pick ", 1)[1].split()[:2])
                    self.parent()._real_click(x, y)
                if msg.startswith("[hayate] episode card"):
                    self.parent()._ep_clicked = True
                if msg.startswith("[hayate] lang"):
                    from PyQt6.QtCore import QTimer
                    if "lang click" in msg:
                        x, y = (int(v) for v in msg.split("click ", 1)[1].split()[:2])
                        self.parent()._real_click(x, y)
                        QTimer.singleShot(1000, self.parent()._press_season)
                    else:
                        QTimer.singleShot(0, self.parent()._press_season)
                if msg.startswith("[hayate] click "):
                    x, y = (int(v) for v in msg.split("click ", 1)[1].split()[:2])
                    self.parent()._real_click(x, y)
                return
            if level == QWebEnginePage.JavaScriptConsoleMessageLevel.ErrorMessageLevel:
                print("js-error:", msg, flush=True)

    class WebPane(QWebEngineView):
        def __init__(self, owner):
            super().__init__(owner)
            self._owner = owner
            self.setPage(_Page(_profile(), self))
            self.page().fullScreenRequested.connect(self._fs)
            self.urlChanged.connect(self._on_url)

        dom_fs = False

        def _fs(self, req):
            req.accept()
            self.dom_fs = req.toggleOn()
            if self.dom_fs and not self._owner.window().isFullScreen():
                self._owner._toggle_fullscreen()

        def exit_page_fullscreen(self):
            self.page().triggerAction(QWebEnginePage.WebAction.ExitFullScreen)

        def press_f(self):
            from PyQt6.QtCore import Qt, QEvent
            from PyQt6.QtGui import QKeyEvent
            from PyQt6.QtWidgets import QApplication
            t = self.focusProxy() or self
            for ty in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease):
                QApplication.sendEvent(t, QKeyEvent(ty, Qt.Key.Key_F, Qt.KeyboardModifier.NoModifier, "f"))

        _target = ""
        _onboarding = False
        _retry = False

        def _on_url(self, u):
            s = u.toString()
            print('[web]', s, flush=True)
            self._learn(s)
            if "/watch" in s and getattr(self, "_lang", "") and not CRAWL and s.split("?")[0] != self._aud_url:
                self._aud_url = s.split("?")[0]
                from PyQt6.QtCore import QTimer
                QTimer.singleShot(4000, self._press_audio)
            if self._q and s.split('?')[0].rstrip('/').endswith('/search') and 'q=' not in s:
                self._type_query()
            elif self._autoplay and ('/shows/' in s or '/movies/' in s):
                self._autoplay = False
                from PyQt6.QtCore import QTimer
                QTimer.singleShot(1200, self._press_play)
            if "onboarding" in s:
                self._onboarding = True
            elif self._onboarding and self._retry and self._target.startswith("http") \
                    and s.rstrip("/") != self._target.rstrip("/"):
                self._onboarding = self._retry = False
                self.load(QUrl(self._target))

        _q = ""

        _key = ""

        def open(self, url, key="", ep=1):
            from urllib.parse import urlparse, parse_qs
            from urllib.parse import unquote
            url, _, lang = url.partition("#hayate_lang=")
            self._lang = unquote(lang)
            url = url if ("/watch" in url and not CRAWL) else _show_root(url)
            self._ep = ep
            self._season = _season_for(key)
            self._target, self._retry, self._onboarding = url, True, False
            self._key = key
            self._autoplay = "/shows/" in url or "/movies/" in url
            self._q = (parse_qs(urlparse(url).query).get("q") or [""])[0]
            self._ek = f"{key}|{ep}|{self._lang}"
            self._ep_clicked = False
            direct = url if "/watch" in url else ("" if CRAWL else _direct_ep(self._ek))
            self._pending_watch = ""
            if direct:
                print("[web] direct episode", direct, "lang", repr(self._lang), flush=True)
                if self._lang and not CRAWL and os.environ.get("HAYATE_LANG_DETOUR"):
                    from PyQt6.QtCore import QTimer
                    self._pending_watch = direct
                    url = _show_root(direct)
                    self._target, self._autoplay, self._season, self._q = url, True, 0, ""
                    self._pw_tok += 1
                    tok = self._pw_tok
                    QTimer.singleShot(30000, lambda: self._go_pending(tok))
                else:
                    self._target, self._autoplay, self._season, self._q = direct, False, 0, ""
                    url = direct
                self._advance()
            self.load(QUrl(url))

        _autoplay = False
        _ek = ""
        _ep_clicked = False
        _cr_active = False
        _cr_targets = None
        _cr_seen = None
        _cr_root = ""
        _cr_token = 0
        _cr_langs = None
        _pending_watch = ""
        _pw_tok = 0
        _aud_url = ""

        def _advance(self):
            from PyQt6.QtCore import QTimer
            if self._cr_active:
                QTimer.singleShot(1500, self._cr_next)
                return
            mx = int(os.environ.get("HAYATE_MAP_TO", "0") or 0)
            if mx and self._ep < mx:
                n = self._ep + 1
                print("[web] mapping: next episode", n, flush=True)
                QTimer.singleShot(1500, lambda: self._owner.play_episode(n))

        def _learn(self, s):
            import json, os, re
            if not self._key or not re.search(r"/(shows|movies)/", s):
                return
            if self._ek and self._ep_clicked and re.search(r"/watch(?:[/?#]|$)", s):
                ef = os.path.expanduser("~/.config/hayate/webplayer/episodes.json")
                try:
                    em = json.load(open(ef))
                except (OSError, ValueError):
                    em = {}
                if em.get(self._ek) != s:
                    em[self._ek] = s
                    json.dump(em, open(ef, "w"), indent=1)
                    print("[web] saved episode", self._ek, "->", s, flush=True)
                self._ep_clicked = False
                self._advance()
            f = os.path.expanduser("~/.config/hayate/webplayer/urls.json")
            try:
                m = json.load(open(f))
            except (OSError, ValueError):
                m = {}
            s = _show_root(s)
            if m.get(self._key) != s:
                m[self._key] = s
                os.makedirs(os.path.dirname(f), exist_ok=True)
                json.dump(m, open(f, "w"), indent=1)
                print("[web] saved", self._key, "->", s, flush=True)

        def _open_top(self):
            import json, re
            words = sorted(set(re.findall(r"[a-z0-9]{4,}", self._key.lower())))
            if not words:
                return
            js = """(function(words){var n=0,t=setInterval(function(){
              var need=Math.max(1,Math.ceil(words.length*0.6)),best=null,bs=0;
              [].slice.call(document.querySelectorAll('a[href*="/shows/"],a[href*="/movies/"]')).forEach(function(a){
                var im=a.querySelector('img');
                var h=(a.getAttribute('href')+' '+a.textContent+' '+(a.getAttribute('aria-label')||'')+' '+(im?im.alt:'')).toLowerCase();
                var sc=words.filter(function(w){return h.indexOf(w)>=0;}).length;
                if(sc>bs){bs=sc;best=a;}});
              if(best&&bs>=need){clearInterval(t);console.log('[hayate] open '+best.href+' score '+bs);best.click();}
              else if(++n>40){clearInterval(t);console.log('[hayate] no matching result ('+bs+'/'+words.length+'), pick one yourself');}
            },300);})(WORDS);""".replace("WORDS", json.dumps(words))
            self.page().runJavaScript(js)

        def _real_click(self, x, y):
            from PyQt6.QtCore import Qt, QPointF, QEvent
            from PyQt6.QtGui import QMouseEvent
            from PyQt6.QtWidgets import QApplication
            t = self.focusProxy() or self
            pt = QPointF(x, y)
            N, L, M = Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier
            for ty, b, bs in ((QEvent.Type.MouseMove, N, N),
                              (QEvent.Type.MouseButtonPress, L, L),
                              (QEvent.Type.MouseButtonRelease, L, N)):
                QApplication.sendEvent(t, QMouseEvent(ty, pt, b, bs, M))
            print("[web] real click", x, y, flush=True)

        def _press_play(self):
            lang = getattr(self, "_lang", "").replace("+", " ")
            if not lang:
                self._press_season()
                return
            import json
            js = r"""(function(lang){var n=0,t=setInterval(function(){
              var els=[].slice.call(document.querySelectorAll('button,[role=button],[role=tab],a,span,div')).filter(function(e){
                return e.offsetParent!==null&&e.children.length<=1;});
              var hit=els.filter(function(e){return (e.innerText||'').trim().toLowerCase()===lang.toLowerCase();});
              if(hit.length){clearInterval(t);var r=hit[0].getBoundingClientRect();
                console.log('[hayate] lang click '+Math.round(r.left+r.width/2)+' '+Math.round(r.top+r.height/2));return;}
              if(++n>20){clearInterval(t);console.log('[hayate] lang '+lang+' not found');}
            },300);})(LANG);""".replace("LANG", json.dumps(lang))
            self.page().runJavaScript(js)

        def _aud_click(self, x, y):
            from PyQt6.QtCore import QTimer, QPointF, QEvent, Qt
            from PyQt6.QtGui import QMouseEvent
            from PyQt6.QtWidgets import QApplication
            t = self.focusProxy() or self
            N, M = Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier
            QApplication.sendEvent(t, QMouseEvent(QEvent.Type.MouseMove, QPointF(x - 40, y + 60), N, N, M))
            QTimer.singleShot(500, lambda: self._real_click(x, y))

        def _press_audio(self):
            import json
            lang = (getattr(self, "_lang", "") or "").replace("+", " ")
            if not lang:
                return
            js = r"""(function(lang){var n=0,opens=0,w=lang.toLowerCase().split(' ')[0];
            var t=setInterval(function(){
              function vis(e){var r=e.getBoundingClientRect();return r.width>0&&r.height>0;}
              function first(e){return (e.innerText||'').trim().split('\n')[0].trim();}
              var all=[].slice.call(document.querySelectorAll('div,span,button,li,p,[role=button]')).filter(vis);
              var sub=all.filter(function(e){return (e.innerText||'').trim().toUpperCase()==='SUBTITLES';})[0];
              var head=all.filter(function(e){return (e.innerText||'').trim().toUpperCase()==='AUDIO';})[0];
              if(head&&sub){
                var sl=sub.getBoundingClientRect().left,hb=head.getBoundingClientRect().bottom;
                var c=all.filter(function(e){var r=e.getBoundingClientRect();
                  return e!==head&&r.left<sl-20&&r.top>hb-2&&r.width<300&&r.height<90&&(e.innerText||'').trim().length<40;});
                var hit=c.filter(function(e){return first(e).toLowerCase().split(' ')[0]===w;});
                clearInterval(t);
                if(!hit.length){console.log('[hayate] audio '+lang+' not available: '+JSON.stringify(c.map(first).slice(0,6)));return;}
                hit.sort(function(a,b){var x=a.getBoundingClientRect(),y=b.getBoundingClientRect();return y.width*y.height-x.width*x.height;});
                var e=hit[0];
                if(e.querySelector('svg')){console.log('[hayate] audio '+lang+' already set');return;}
                var r=e.getBoundingClientRect();
                console.log('[hayate] audio pick '+Math.round(r.left+r.width/2)+' '+Math.round(r.top+r.height/2));return;}
              if(n%7===0&&opens<5){
                var b=all.filter(function(e){return /^audio\s*&\s*subtitles$/i.test((e.innerText||'').trim());});
                if(b.length){var q=b[b.length-1].getBoundingClientRect();opens++;
                  console.log('[hayate] audio open '+Math.round(q.left+q.width/2)+' '+Math.round(q.top+q.height/2));}}
              if(++n>60){clearInterval(t);console.log('[hayate] audio menu not found');}
            },300);})(LANG);""".replace("LANG", json.dumps(lang))
            self.page().runJavaScript(js)

        def _go_pending(self, tok=None):
            from PyQt6.QtCore import QUrl
            pw = self._pending_watch
            if not pw or (tok is not None and tok != self._pw_tok):
                return
            self._pending_watch = ""
            self._target = pw
            print("[web] opening cached episode", pw, flush=True)
            self.load(QUrl(pw))

        def _press_season(self):
            if self._pending_watch:
                from PyQt6.QtCore import QTimer
                QTimer.singleShot(1200, self._go_pending)
                return
            n = int(getattr(self, "_season", 0) or 0)
            if not n:
                self._press_ep()
                return
            js = r"""(function(n){var k=0,opened=false,t=setInterval(function(){
              var re=new RegExp('^(season|s)\\s*0*'+n+'$','i');
              function first(e){return (e.innerText||'').trim().split('\n')[0].trim();}
              var els=[].slice.call(document.querySelectorAll('a,button,li,div,span,[role=button],[role=option],[role=menuitem]')).filter(function(e){return e.offsetParent!==null;});
              var hit=els.filter(function(e){var x=first(e);return x.length<14&&re.test(x);});
              if(hit.length){clearInterval(t);var e=hit[hit.length-1];
                (e.closest('a,button,[role=button],li')||e).click();
                console.log('[hayate] season '+n+' done');return;}
              if(!opened){var cur=els.filter(function(e){var x=first(e);return x.length<14&&/^season\s*\d+$/i.test(x);});
                if(cur.length){opened=true;(cur[0].closest('button,[role=button]')||cur[0]).click();}}
              if(++k>40){clearInterval(t);console.log('[hayate] season '+n+' not found; samples: '+JSON.stringify(
                els.map(first).filter(function(x){return /season|^s\d/i.test(x)&&x.length<30;}).slice(0,8)));}
            },300);})(SEASONNUM);""".replace("SEASONNUM", str(n))
            self.page().runJavaScript(js)

        def _cr_discover(self):
            if self._cr_langs is None:
                order = [x.strip() for x in os.environ.get("HAYATE_CRAWL_LANGS", "Hindi,English,Japanese Original").split(",") if x.strip()]
                cur = (self._lang or "").lower()
                self._cr_langs = [x for x in order if x.lower() != cur]
            print("[web] crawl: scanning seasons of", self._cr_root, self._lang, flush=True)
            self.page().runJavaScript(r"""(function(){
 var acc={};
 function vis(e){return e.offsetParent!==null;}
 function tabs(){var o={};[].slice.call(document.querySelectorAll('button,a,li,div,span,[role=tab],[role=button]')).forEach(function(e){
   if(!vis(e))return;var m=(e.innerText||'').trim().match(/^season\s*(\d+)$/i);if(m&&!o[m[1]])o[m[1]]=e;});return o;}
 function scan(sn){var n=0,last=null;[].slice.call(document.querySelectorAll('a,button,[role=button],li,div')).forEach(function(e){
   var x=(e.innerText||'').trim();if(!x||x.length>140||/^\s*(resume|watch|play|continue|start)/i.test(x))return;
   var m=x.match(/\bS\s*(\d+)\s*E\s*(\d+)\b/i);if(!m)return;var s=+m[1],ep=+m[2];if(sn&&s!==sn)return;
   (acc[s]=acc[s]||{})[ep]=1;n++;last=e;});return {n:n,last:last};}
 var mc=0;
 function more(){if(mc>15)return null;var best=null;var l=[].slice.call(document.querySelectorAll('button,a,[role=button],div,span'));
   for(var i=0;i<l.length;i++){var e=l[i];if(!vis(e))continue;var x=(e.innerText||'').trim();
     if(x.length<30&&/^(show|view|load|see)\s*(more|all)/i.test(x)){best=e;}}if(best)mc++;return best;}
 function settle(sn,cb){mc=0;var prev=-1,same=0,k=0;(function st(){var r=scan(sn);var c=0;
   for(var s in acc)c+=Object.keys(acc[s]).length;
   var mb=more();if(mb){mb.scrollIntoView({block:'center'});setTimeout(function(){var q=mb.getBoundingClientRect();console.log('[hayate] more click '+Math.round(q.left+q.width/2)+' '+Math.round(q.top+q.height/2));},300);same=0;prev=-1;setTimeout(st,1800);return;}
   if(r.last)r.last.scrollIntoView({block:'end'});
   if(c===prev)same++;else same=0;prev=c;
   if(same>=2||++k>15){cb();return;}setTimeout(st,500);})();}
 function done(){var o={};for(var s in acc)o[s]=Object.keys(acc[s]).map(Number).sort(function(a,b){return a-b;});
   console.log('[hayate] cr map '+JSON.stringify(o));}
 function go(){var T=tabs(),ss=Object.keys(T).map(Number).sort(function(a,b){return a-b;}),i=0;
   if(!ss.length){settle(0,done);return;}
   (function nx(){if(i>=ss.length){done();return;}var sn=ss[i++],e=tabs()[sn];if(!e){nx();return;}
     (e.closest('button,[role=button],[role=tab],li')||e).click();
     setTimeout(function(){settle(sn,nx);},1200);})();}
 var w=0;(function wait(){if(Object.keys(tabs()).length||scan(0).n||++w>25){go();return;}setTimeout(wait,300);})();
})();""")

        def _cr_got(self, payload):
            import json
            try:
                m = json.loads(payload)
            except ValueError:
                print("[web] crawl: could not read season map", flush=True)
                return
            ef = os.path.expanduser("~/.config/hayate/webplayer/episodes.json")
            try:
                done = json.load(open(ef))
            except (OSError, ValueError):
                done = {}
            lang = (self._lang or "default").replace(" ", "+")
            self._cr_targets = []
            for sn, eps in sorted(m.items(), key=lambda kv: int(kv[0])):
                for e in eps:
                    k = f"@{self._cr_root}|S{sn}|E{e}|{lang}"
                    if k not in done:
                        self._cr_targets.append((int(sn), int(e), k))
            print(f"[web] crawl: {sum(len(v) for v in m.values())} episodes found, "
                  f"{len(self._cr_targets)} to map", flush=True)
            self._cr_active = True
            self._cr_next()

        def _cr_next_lang(self, tok=None):
            from PyQt6.QtCore import QTimer, QUrl
            if tok is not None and tok != self._cr_token:
                return
            if self._cr_langs is None:
                return
            if not self._cr_langs:
                print("[web] crawl: all languages done", flush=True)
                self._cr_langs = None
                return
            self._lang = self._cr_langs.pop(0)
            print("[web] crawl: language", self._lang, flush=True)
            root = self._cr_root
            (self._cr_seen or set()).discard(root)
            self._cr_active = False
            self._ek, self._ep_clicked = "", False
            self._autoplay, self._onboarding, self._retry = True, False, False
            self._cr_token += 1
            self.load(QUrl("about:blank"))
            QTimer.singleShot(400, lambda: self.load(QUrl(root)))

        def _cr_next(self):
            from PyQt6.QtCore import QTimer, QUrl
            if not self._cr_targets:
                self._cr_active = False
                print("[web] crawl: finished", self._cr_root, self._lang, flush=True)
                self._cr_next_lang()
                return
            sn, e, k = self._cr_targets.pop(0)
            self._ek, self._season, self._ep, self._ep_clicked = k, sn, e, False
            self._autoplay, self._onboarding, self._retry = True, False, False
            self._cr_token += 1
            tok = self._cr_token
            QTimer.singleShot(45000, lambda: self._cr_timeout(tok, sn, e))
            print(f"[web] crawl: S{sn} E{e}", flush=True)
            if _show_root(self.url().toString()) == self._cr_root:
                self.load(QUrl("about:blank"))
                QTimer.singleShot(400, lambda: self.load(QUrl(self._cr_root)) if tok == self._cr_token else None)
            else:
                self.load(QUrl(self._cr_root))

        def _cr_timeout(self, tok, sn, e):
            if self._cr_active and tok == self._cr_token:
                print(f"[web] crawl: timeout on S{sn} E{e}, skipping", flush=True)
                self._cr_next()

        def _press_ep(self):
            if CRAWL and not self._cr_active:
                root = _show_root(self.url().toString())
                if "/shows/" in root:
                    if self._cr_seen is None:
                        self._cr_seen = set()
                    if root not in self._cr_seen:
                        self._cr_seen.add(root)
                        self._cr_root = root
                        self._cr_discover()
                        return
            ep = int(getattr(self, "_ep", 1) or 1)
            sn = int(getattr(self, "_season", 0) or 0)
            js = r"""(function(want,sn){var n=0,t=setInterval(function(){
              function ok(x){
                if(/^\s*(resume|watch|play|continue|start)/i.test(x)) return false;
                var m=x.match(/\bS\s*(\d+)\s*E\s*(\d+)\b/i);
                if(m) return (!sn||+m[1]===sn)&&+m[2]===want;
                m=x.match(/\b(?:EP|E|Episode)\s*\.?\s*(\d+)\b/i);
                return !!m&&+m[1]===want;
              }
              var c=[].slice.call(document.querySelectorAll('a,button,[role=button],li,div')).filter(function(e){
                var x=(e.innerText||'').trim();return x&&x.length<140&&ok(x);});
              c.sort(function(a,b){return a.innerText.length-b.innerText.length;});
              if(c.length){clearInterval(t);var card=c[0];
                for(var i=0;i<6&&card&&!card.querySelector('img');i++){card=card.parentElement;}
                var tg=(card&&card.querySelector('img'))||c[0];
                tg.scrollIntoView({block:'center'});
                console.log('[hayate] episode card: '+c[0].innerText.replace(/\s+/g,' ').slice(0,60));
                setTimeout(function(){var r=tg.getBoundingClientRect();
                  console.log('[hayate] click '+Math.round(r.left+r.width/2)+' '+Math.round(r.top+r.height/2));},600);
                return;}
              if(n%3===2){var mb=[].slice.call(document.querySelectorAll('button,a,[role=button],div,span')).filter(function(e){
                var x=(e.innerText||'').trim();return e.offsetParent!==null&&x.length<30&&/^(show|view|load|see)\s*(more|all)/i.test(x);})[0];
                if(mb){mb.scrollIntoView({block:'center'});setTimeout(function(){var r=mb.getBoundingClientRect();console.log('[hayate] more click '+Math.round(r.left+r.width/2)+' '+Math.round(r.top+r.height/2));},300);}}
              if(++n>60){clearInterval(t);
                var sm=[].slice.call(document.querySelectorAll('a,button,li,div')).map(function(e){
                  return (e.innerText||'').trim().replace(/\s+/g,' ');}).filter(function(x){
                  return x&&x.length<100&&/\bS\s*\d+\s*E\s*\d+/i.test(x);}).slice(0,8);
                console.log('[hayate] no episode '+want+' card; samples: '+JSON.stringify(sm));}
            },300);})(EPNUM,SEASONNUM);""".replace("EPNUM", str(ep)).replace("SEASONNUM", str(sn))
            self.page().runJavaScript(js)

        def _type_query(self):
            import json
            q, self._q = self._q, ""
            if not q:
                return
            js = """(function(q){var n=0,t=setInterval(function(){
              var i=document.querySelector('input[type=search],input[type=text],input:not([type])');
              if(i){clearInterval(t);i.focus();
                Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(i,q);
                i.dispatchEvent(new Event('input',{bubbles:true}));}
              else if(++n>40){clearInterval(t);}},250);})(%s);""" % json.dumps(q)
            self.page().runJavaScript(js)
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(1500, self._open_top)
