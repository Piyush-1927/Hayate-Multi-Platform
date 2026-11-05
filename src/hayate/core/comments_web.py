"""Giscus comments: the page URL and the AniList id -> GitHub Discussion number map."""
PAGE_URL = "https://piyush-1927.github.io/hayate-comments/"

# Created by hand for now. Later this can be fetched from JSON without touching callers.
DISCUSSIONS = {
    21355: 1,   # Re:ZERO
}


def discussion_for(anilist_id):
    try:
        return DISCUSSIONS.get(int(anilist_id)) if anilist_id else None
    except (TypeError, ValueError):
        return None


# Where "start a discussion" requests are sent. Change this one line.
REQUEST_EMAIL = "neonblade0018@gmail.com"


def request_mailto(anilist_id):
    from urllib.parse import quote
    a = str(anilist_id or "unknown")
    subject = "Hayate: please start a discussion for AniList id %s" % a
    body = ("Hi, I'd like a comments discussion started for this anime.\n\n"
            "AniList id: %s\nAniList link: https://anilist.co/anime/%s\n" % (a, a))
    return "mailto:%s?subject=%s&body=%s" % (REQUEST_EMAIL, quote(subject), quote(body))


def request_parts(anilist_id):
    a = str(anilist_id or "unknown")
    subject = "Hayate: please start a discussion for AniList id %s" % a
    body = ("Hi, I'm a Hayate user and I'd like a comments discussion thread to be created for this anime so viewers can discuss episodes there. "
            "AniList id: %s - AniList link: https://anilist.co/anime/%s - Thank you!" % (a, a))
    return REQUEST_EMAIL, subject, body


# Injected into the comments page (top frame) and the giscus iframe.
# top frame: asks giscus to emit metadata, learns who is signed in, shows a notice when signed out.
# giscus frame: adds Edit/Delete links only on the signed-in user's own comments.
CARD_JS = r'''(function(){
 var h=location.hostname,G="https://giscus.app";
 if(h==="giscus.app"){
  var viewer=null;
  function authorOf(a){
   var n=a.parentElement;
   for(var i=0;n&&i<8;i++,n=n.parentElement){
    var l=n.querySelectorAll("a[href]");
    for(var k=0;k<l.length;k++){
     var m=l[k].href.match(/^https:\/\/github\.com\/([^\/?#]+)\/?$/);
     if(m)return m[1].toLowerCase();
    }
   }
   return "";
  }
  function fix(){
   document.querySelectorAll('a[href*="discussioncomment-"]').forEach(function(a){
    if(!a.querySelector("time"))return;
    var au=authorOf(a),key=!viewer?"out":(au===viewer?"own":"other");
    var nx=a.nextElementSibling,has=!!(nx&&nx.classList.contains("hy-edit"));
    if(a.dataset.hyk===key&&has)return;
    while(a.nextElementSibling&&a.nextElementSibling.classList.contains("hy-edit"))a.nextElementSibling.remove();
    a.dataset.hyk=key;
    var m=a.href.match(/discussioncomment-(\d+)/);if(!m)return;
    var d=(a.href.match(/discussions\/(\d+)#/)||[])[1]||"",id=d+"-"+m[1];
    var tm=a.querySelector("time")||(a.parentElement&&a.parentElement.querySelector("time")),tx="";
    for(var n=a.parentElement,j=0;n&&j<8&&!tx;j++,n=n.parentElement){
     var ce=n.querySelector(".markdown,.gsc-comment-content,[class*=comment-content],[class*=comment-body],[class*=markdown]");
     if(ce)tx=ce.textContent.trim().replace(/\s+/g," ").slice(0,300);
    }
    var mail=["Report by mail","hayate-report:"+encodeURIComponent(JSON.stringify({a:au,l:a.href,t:tx,w:(tm&&(tm.getAttribute("datetime")||tm.title))||a.textContent}))];
    var acts=key==="own"?[["Edit","hayate-edit:"+id],["Delete","hayate-edit:del:"+id],mail]
     :key==="other"?[["Report on GitHub","hayate-edit:rep:"+id],mail]:[mail],prev=a;
    acts.forEach(function(x){
     var e=document.createElement("a");
     e.href=x[1];e.textContent=x[0];e.className="hy-edit";
     e.style.cssText="margin-left:8px;font-size:12px;text-decoration:underline;cursor:pointer;color:inherit;opacity:.8";
     prev.insertAdjacentElement("afterend",e);prev=e;
    });
   });
  }
  window.addEventListener("message",function(ev){
   var d=ev.data;
   if(ev.source!==window.parent||!d||typeof d!=="object"||!d.hayate)return;
   if(d.hayate.clear){try{localStorage.clear();sessionStorage.clear();}catch(e){}viewer="";return;}
   var v=d.hayate.viewer;viewer=v?String(v).toLowerCase():"";
   fix();
  });
  new MutationObserver(fix).observe(document.documentElement,{childList:true,subtree:true});
  fix();
  var BAD=__BLOCKED__,BADSET={};
  function norm(t){
   t=String(t).toLowerCase();
   try{t=t.normalize("NFKD").replace(/[\u0300-\u036f]/g,"");}catch(e){}
   return t.replace(/[@4]/g,"a").replace(/3/g,"e").replace(/[1!|]/g,"i").replace(/0/g,"o").replace(/[5$]/g,"s").replace(/7/g,"t");
  }
  function sq(w){return w.replace(/(.)\1+/g,"$1");}
  BAD.forEach(function(w){BADSET[sq(norm(w))]=1;});
  function hit(w){
   var c=sq(w);
   return !!(BADSET[c]||BADSET[c.replace(/(ing|ers?|ed|es|s|y)$/,"")]);
  }
  function bad(text){
   var t=norm(text).split(/[^a-z]+/).filter(Boolean),out=[],run="";
   t.forEach(function(w){
    if(w.length===1){run+=w;return;}
    if(run.length>1)out.push(run);
    run="";out.push(w);
   });
   if(run.length>1)out.push(run);
   for(var i=0;i<out.length;i++){if(hit(out[i]))return true;}
   return false;
  }
  var FW={},TG={};
  ["fuck","fucking","fucked","fucker","fuckers","fucks","fck","fuk","fuckin","fukin"].forEach(function(k){FW[sq(k)]=1;});
  ["you","u","ya","yo","ur","your","yourself","off","him","her","them","everyone"].forEach(function(k){TG[sq(k)]=1;});
  function directed(text){
   var w=norm(text).split(/\s+/);
   for(var i=0;i<w.length-1;i++){
    if(/[,.;:?\-]$/.test(w[i]))continue;
    var c=sq(w[i].replace(/[^a-z]/g,"")),n=sq(w[i+1].replace(/[^a-z]/g,""));
    if(FW[c]&&TG[n])return true;
   }
   return false;
  }
  function guard(e){
   var f=e.target&&e.target.closest?e.target.closest("form"):null;
   var ta=f?f.querySelector("textarea"):null;
   if(!ta)return;
   var o=f.querySelector(".hy-block");
   if(!bad(ta.value)&&!directed(ta.value)){if(o)o.remove();return;}
   e.preventDefault();e.stopImmediatePropagation();
   if(o)return;
   o=document.createElement("div");o.className="hy-block";
   o.textContent="Not posted: your comment contains language that isn\u2019t allowed here. Please reword it and try again.";
   o.style.cssText="font:12px sans-serif;padding:6px 8px;margin:6px 0;border-radius:6px;color:#ff6b6b;border:1px solid #ff6b6b";
   ta.insertAdjacentElement("afterend",o);
  }
  document.addEventListener("submit",guard,true);
  document.addEventListener("click",function(e){
   var b=e.target&&e.target.closest?e.target.closest("button[type=submit],button.gsc-comment-box-button"):null;
   if(b)guard(e);
  },true);
  document.addEventListener("keydown",function(e){
   if(e.key==="Enter"&&(e.ctrlKey||e.metaKey)&&e.target&&e.target.tagName==="TEXTAREA")guard(e);
  },true);
  document.addEventListener("input",function(e){
   var f=e.target&&e.target.closest?e.target.closest("form"):null,o=f&&f.querySelector(".hy-block");
   if(o&&!bad(e.target.value)&&!directed(e.target.value))o.remove();
  },true);
 }else if(/\.github\.io$/.test(h)){
  var st=null,raw=null,tries=0,dark=/theme=(transparent_dark|dark)/.test(location.search);
  function send(m){var f=document.querySelector("iframe.giscus-frame");if(f&&f.contentWindow)f.contentWindow.postMessage(m,G);}
  function note(){
   var n=document.getElementById("hy-note");
   if(st!==""){if(n)n.remove();return;}
   if(n)return;
   n=document.createElement("div");n.id="hy-note";
   n.textContent="You can\u2019t edit or delete comments, or report them on GitHub, unless you\u2019re logged in with GitHub. Reporting by mail works without signing in.";
   n.style.cssText="font:12px sans-serif;padding:6px 8px 10px;color:"+(dark?"#9a9ab0":"#666677");
   document.body.insertBefore(n,document.body.firstChild);
  }
  function hasSession(){try{return Object.keys(localStorage).some(function(k){return /giscus/i.test(k)&&!!localStorage.getItem(k);});}catch(e){return false;}}
  function push(){
   if(raw===null)return;
   st=(raw&&hasSession()&&!/\[bot\]$/i.test(raw))?raw:"";
   send({hayate:{viewer:st}});note();
  }
  function ask(){if(raw!==null||tries++>40)return;send({giscus:{setConfig:{emitMetadata:true}}});setTimeout(ask,700);}
  window.addEventListener("message",function(ev){
   if(ev.origin!==G)return;
   var d=ev.data&&ev.data.giscus;
   if(!d||typeof d!=="object"||!("discussion" in d))return;
   raw=(d.viewer&&d.viewer.login)?String(d.viewer.login):"";
   console.log("[hayate] giscus viewer="+(raw||"none")+" session="+hasSession());
   push();
  });
  setInterval(push,3000);
  ask();
 }
})();'''

# Blocked client-side (whole words; case, repeats, spacing and leetspeak are normalised).
# Add your own entries here, one string each.
BLOCKED_WORDS = [
    "shit", "bitch", "asshole", "bastard", "cunt", "dick", "pussy",
]
import json as _json
CARD_JS = CARD_JS.replace("__BLOCKED__", _json.dumps(sorted(set(BLOCKED_WORDS))))


def report_mailto(raw):
    """raw = hayate-report: link; payload = percent-encoded JSON (or legacy 'author|link')."""
    import json, re
    from urllib.parse import quote, unquote
    payload = unquote(raw.split(":", 1)[1]) if ":" in raw else ""
    try:
        d = json.loads(payload)
    except ValueError:
        a, _, l = payload.partition("|")
        d = {"a": a, "l": l}
    link = d.get("l") or "unknown"
    m = re.search(r"discussioncomment-(\d+)", link)
    cid = m.group(1) if m else "unknown"
    m = re.search(r"discussions/(\d+)", link)
    did = m.group(1) if m else "?"
    author = d.get("a") or "unknown"
    subject = "Hayate: report comment #%s by %s" % (cid, author)
    body = "\r\n".join([
        "Hi, I'd like to report a comment on Hayate.", "",
        "Commenter: %s" % author,
        "Discussion: #%s" % did,
        "Comment ID: %s" % cid,
        "Posted: %s" % (d.get("w") or "unknown"),
        "Link: %s" % link, "",
        "Comment:", '"%s"' % (d.get("t") or "(unavailable)"), "",
        "Reason:", "",
    ])
    return "mailto:%s?subject=%s&body=%s" % (REQUEST_EMAIL, quote(subject), quote(body))

def open_mailto(url):
    """Open a mailto: URL. Thunderbird gets decoded text directly (xdg-email leaves it percent-encoded)."""
    import shutil, subprocess
    from urllib.parse import urlsplit, unquote
    sp = urlsplit(url)
    f = {}
    for kv in sp.query.split("&"):
        k, _, v = kv.partition("=")
        f[k] = unquote(v)
    clean = lambda t: t.replace("\r\n", "\n").replace("'", "\u2019")
    tb = shutil.which("thunderbird")
    if tb:
        arg = "to='%s',subject='%s',body='%s'" % (clean(unquote(sp.path)), clean(f.get("subject", "")), clean(f.get("body", "")))
        subprocess.Popen([tb, "-compose", arg])
        return
    from PyQt6.QtCore import QUrl
    from PyQt6.QtGui import QDesktopServices
    QDesktopServices.openUrl(QUrl(url))


_LS_FILE = "~/.config/hayate/giscus_login.json"
_LS_JS = r'''(function(){
 if(location.hostname!=="giscus.app")return;
 var d=__DATA__,k;
 try{for(k in d){if(localStorage.getItem(k)===null)localStorage.setItem(k,d[k]);}}catch(e){}
 var last=null,had=false;
 function push(){
  try{
   var o={},i,n;
   for(i=0;i<localStorage.length;i++){n=localStorage.key(i);if(/^giscus/i.test(n))o[n]=localStorage.getItem(n);}
   var has=Object.keys(o).length>0;
   if(has)had=true;
   if(!has&&!had)return;
   var j=JSON.stringify(o);
   if(j===last)return;
   last=j;
   var a=document.createElement("a");
   a.href="hayate-ls:"+encodeURIComponent(j);
   (document.body||document.documentElement).appendChild(a);a.click();a.remove();
  }catch(e){}
 }
 setInterval(push,3000);
})();'''


def ls_load():
    import json, os
    try:
        with open(os.path.expanduser(_LS_FILE)) as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def ls_save(raw):
    """raw = hayate-ls: link; payload = percent-encoded JSON of giscus localStorage."""
    import json, os
    from urllib.parse import unquote
    try:
        d = json.loads(unquote(raw.split(":", 1)[1]))
        assert isinstance(d, dict)
        p = os.path.expanduser(_LS_FILE)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        fd = os.open(p + ".tmp", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(d, f)
        os.replace(p + ".tmp", p)
    except Exception as e:
        print("giscus login sync failed:", e, flush=True)


def ls_script():
    import json
    return _LS_JS.replace("__DATA__", json.dumps(ls_load()))
