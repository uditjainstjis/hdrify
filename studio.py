#!/usr/bin/env python3
"""hdrify studio — drop a file, see SDR vs HDR side by side, drag sliders, download.

Run:  python3 server.py   ->  http://localhost:8765
Open it in Chrome or Safari on an HDR display, with brightness NOT at max
(macOS needs brightness headroom to render above SDR white).
"""
import html
import json
import os
import re
import tempfile
import threading
import time
import traceback
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

import hdrify

ROOT = os.path.dirname(os.path.abspath(__file__))
WORK = os.path.join(ROOT, "out")
os.makedirs(WORK, exist_ok=True)

# The app has no window of its own: closing the browser tab is how you quit.
# The page heartbeats while it is open; when the beats stop, so do we.
LAST_BEAT = [0]      # 0 = no tab has connected yet
IDLE_TIMEOUT = 12      # seconds without a heartbeat before shutting down
GRACE = 25             # allow this long for the browser to open on startup

SESSIONS = {}          # token -> {"path":..., "name":..., "kind": "image"|"video"}
CACHE = {}             # render key -> (bytes, mime, filename)
LOCK = threading.Lock()

PAGE = r"""<!doctype html><meta charset=utf-8><title>hdrify studio</title>
<meta name=viewport content="width=device-width,initial-scale=1">
<style>
:root{color-scheme:dark;--bg:#08080a;--line:#232329;--dim:#7d7d87;--fg:#ececf0;--acc:#8b7bff}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
     font:14px/1.5 -apple-system,BlinkMacSystemFont,"SF Pro Text",sans-serif}
header{display:flex;align-items:baseline;gap:12px;padding:20px 28px;border-bottom:1px solid var(--line)}
h1{font-size:16px;margin:0;letter-spacing:-.01em}
header span{color:var(--dim);font-size:13px}
main{display:grid;grid-template-columns:1fr 320px;min-height:calc(100vh - 61px)}
@media(max-width:900px){main{grid-template-columns:1fr}}

#stage{padding:28px;display:grid;place-items:center}
#drop{width:100%;max-width:640px;border:1.5px dashed var(--line);border-radius:16px;
      padding:72px 24px;text-align:center;color:var(--dim);cursor:pointer;transition:.15s}
#drop:hover,#drop.on{border-color:var(--acc);color:var(--fg);background:#101017}

#compare{display:none;width:100%;gap:20px;grid-template-columns:1fr 1fr}
@media(max-width:640px){#compare{grid-template-columns:1fr}}
figure{margin:0;display:flex;flex-direction:column;gap:8px;min-width:0}
figcaption{font-size:11px;letter-spacing:.08em;text-transform:uppercase;color:var(--dim)}
figcaption b{color:var(--fg);font-weight:500;letter-spacing:0;text-transform:none}
.frame{background:#000;border:1px solid var(--line);border-radius:12px;overflow:hidden;
       display:grid;place-items:center;min-height:220px;position:relative}
.frame img,.frame video{display:block;max-width:100%;max-height:62vh;width:auto}
.frame.busy::after{content:"";position:absolute;inset:0;background:#0007}

aside{border-left:1px solid var(--line);padding:24px;display:flex;flex-direction:column;gap:22px}
@media(max-width:900px){aside{border-left:0;border-top:1px solid var(--line)}}
.ctl{display:flex;flex-direction:column;gap:7px}
.ctl .top{display:flex;justify-content:space-between;align-items:baseline}
.ctl label{font-size:13px}
.ctl output{font-variant-numeric:tabular-nums;color:var(--acc);font-size:13px}
.ctl small{color:var(--dim);font-size:11.5px;line-height:1.45}
input[type=range]{width:100%;accent-color:var(--acc);margin:0}
button{background:var(--acc);color:#0b0b10;border:0;border-radius:9px;padding:11px 14px;
       font:inherit;font-weight:600;cursor:pointer}
button:disabled{opacity:.4;cursor:default}
button.ghost{background:transparent;color:var(--dim);border:1px solid var(--line);font-weight:400}
.note{color:var(--dim);font-size:11.5px;line-height:1.5;border-top:1px solid var(--line);padding-top:16px}
.err{color:#ff8080;font-size:12.5px}
.fallback{padding:28px;text-align:center;color:var(--dim);font-size:12.5px;line-height:1.6}
#presets{display:flex;flex-wrap:wrap;gap:8px}
.chip{background:#141419;color:var(--dim);border:1px solid var(--line);border-radius:999px;
      padding:6px 13px;font-size:12.5px;font-weight:400}
.chip:hover{color:var(--fg);border-color:var(--acc)}
.hide{display:none!important}
</style>

<header><h1>hdrify studio</h1><span id=status>drop a file to begin</span></header>
<main>
  <section id=stage>
    <div id=drop>Drop an image or video here, or click to pick
      <input type=file id=file hidden accept="image/*,video/*"></div>
    <div id=compare>
      <figure><figcaption>Original &middot; <b>SDR</b></figcaption>
        <div class=frame id=fa></div></figure>
      <figure><figcaption>hdrify &middot; <b id=outlabel>HDR</b></figcaption>
        <div class=frame id=fb></div></figure>
    </div>
  </section>

  <aside>
    <div id=presets></div>
    <div class="ctl" id=c-boost>
      <div class=top><label for=boost>Brightness boost</label><output id=o-boost>16.0&times;</output></div>
      <input type=range id=boost min=1 max=64 step=.5 value=16>
      <small>Flat gain in linear light &mdash; colours and contrast stay pixel-identical, the
      whole picture just renders this many times above SDR white.</small>
    </div>
    <div class="ctl" id=c-knee>
      <div class=top><label for=knee>Highlights only</label><output id=o-knee>off</output></div>
      <input type=range id=knee min=0 max=.9 step=.05 value=0>
      <small>Above 0 the boost is reserved for bright areas. This <em>does</em> change the look.</small>
    </div>
    <div class="ctl" id=c-warmth>
      <div class=top><label for=warmth>Warmth</label><output id=o-warmth>0.00</output></div>
      <input type=range id=warmth min=-1 max=1 step=.05 value=0>
      <small>Splits the boost per channel &mdash; this is the golden/orange cast. Graded into
      the HDR layer only, so the SDR fallback stays neutral.</small>
    </div>
    <div class="ctl" id=c-vivid>
      <div class=top><label for=vivid>Vividness</label><output id=o-vivid>1.00</output></div>
      <input type=range id=vivid min=0 max=2.5 step=.05 value=1>
      <small>Colour saturation of the HDR intent. Below 1 the highlights bleach to white.</small>
    </div>
    <div class="ctl" id=c-glow>
      <div class=top><label for=glow>Bloom</label><output id=o-glow>off</output></div>
      <input type=range id=glow min=0 max=1 step=.05 value=0>
      <small>Light spilling off the bright areas, the way real overexposure blooms.</small>
    </div>
    <div class="ctl hide" id=c-nits>
      <div class=top><label for=nits>Peak white</label><output id=o-nits>1600 nits</output></div>
      <input type=range id=nits min=200 max=4000 step=100 value=1600>
      <small>PQ is absolute: past your panel's peak (~1600 nits on XDR) everything clips to
      flat white. Video has no gain map to tone-map it back.</small>
    </div>

    <button id=dl disabled>Download HDR file</button>
    <button class="ghost hide" id=render>Render video</button>
    <button class=ghost id=reset>New file</button>
    <div id=error class=err></div>

    <div class=note>The preview only glows in Chrome or Safari on an HDR display, and only when
    screen brightness is <b>not</b> at maximum &mdash; macOS needs headroom to go above white.</div>
  </aside>
</main>

<script>
// The server exits when this page stops beating, so closing the tab quits the app.
setInterval(()=>{fetch('/ping').catch(()=>{})},3000);
fetch('/ping').catch(()=>{});
addEventListener('pagehide',()=>{navigator.sendBeacon&&navigator.sendBeacon('/bye')});
const $=s=>document.querySelector(s);
const drop=$('#drop'),file=$('#file'),compare=$('#compare'),fa=$('#fa'),fb=$('#fb');
const boost=$('#boost'),knee=$('#knee'),nits=$('#nits');
const warmth=$('#warmth'),vivid=$('#vivid'),glow=$('#glow');
const PRESETS={
  'Untouched':{boost:16,knee:0,warmth:0,vivid:1,glow:0},
  'Golden':   {boost:12,knee:.35,warmth:.6,vivid:1.25,glow:.5},
  'Neon':     {boost:20,knee:.25,warmth:-.35,vivid:2,glow:.65},
  'Blowout':  {boost:32,knee:.5,warmth:.15,vivid:.6,glow:1}
};
let token=null,kind=null,seq=0,timer=null,lastURL=null;
// Chrome frequently lacks HEVC Main10 playback even though the encode is valid
const HEVC_OK = (window.MediaSource && MediaSource.isTypeSupported('video/mp4; codecs="hvc1.2.4.L120.90"'))
             || (document.createElement('video').canPlayType('video/mp4; codecs="hvc1"')!=='');

function fmt(){
  $('#o-boost').textContent=(+boost.value).toFixed(1)+'×';
  $('#o-knee').textContent=knee.value=='0'?'off':(+knee.value).toFixed(2);
  $('#o-nits').textContent=nits.value+' nits';
  $('#o-warmth').textContent=(+warmth.value).toFixed(2);
  $('#o-vivid').textContent=(+vivid.value).toFixed(2);
  $('#o-glow').textContent=glow.value=='0'?'off':(+glow.value).toFixed(2);
}
[boost,knee,nits,warmth,vivid,glow].forEach(s=>s.addEventListener('input',()=>{fmt();schedule()}));
fmt();

const bar=document.getElementById('presets');
for(const name in PRESETS){
  const b=document.createElement('button');b.className='chip';b.textContent=name;
  b.onclick=()=>{const p=PRESETS[name];
    boost.value=p.boost;knee.value=p.knee;warmth.value=p.warmth;vivid.value=p.vivid;glow.value=p.glow;
    fmt();schedule(0)};
  bar.appendChild(b);
}

drop.onclick=()=>file.click();
drop.ondragover=e=>{e.preventDefault();drop.classList.add('on')};
drop.ondragleave=()=>drop.classList.remove('on');
drop.ondrop=e=>{e.preventDefault();drop.classList.remove('on');upload(e.dataTransfer.files[0])};
file.onchange=()=>upload(file.files[0]);
$('#reset').onclick=()=>{token=null;compare.style.display='none';drop.classList.remove('hide');
  $('#dl').disabled=true;$('#status').textContent='drop a file to begin';
  fa.innerHTML=fb.innerHTML='';$('#error').textContent='';file.value=''};

async function upload(f){
  if(!f)return;
  $('#error').textContent='';$('#status').textContent='uploading '+f.name+'…';
  const fd=new FormData();fd.append('file',f);
  let j;
  try{ j=await(await fetch('/upload',{method:'POST',body:fd})).json() }
  catch(e){ $('#error').textContent=e.message; return }
  if(!j.ok){$('#error').textContent=j.error;$('#status').textContent='failed';return}
  token=j.token;kind=j.kind;
  drop.classList.add('hide');compare.style.display='grid';
  $('#c-nits').classList.toggle('hide',kind!=='video');
  $('#c-boost').classList.toggle('hide',kind==='video');
  ['#c-knee','#c-warmth','#c-vivid','#c-glow','#presets'].forEach(
      id=>$(id).classList.toggle('hide',kind==='video'));
  $('#render').classList.toggle('hide',kind!=='video');
  $('#outlabel').textContent=kind==='video'?'HEVC 10-bit PQ':'Ultra HDR';
  fa.innerHTML = kind==='video'
    ? '<video src="/orig?t='+token+'" controls muted loop autoplay playsinline></video>'
    : '<img src="/orig?t='+token+'">';
  fb.innerHTML='';$('#dl').disabled=true;
  $('#status').textContent=j.name+(j.w?' · '+j.w+'×'+j.h:'');
  if(kind==='image')schedule(0); else renderVideo();
}

function schedule(delay=170){
  if(!token||kind!=='image')return;
  clearTimeout(timer);timer=setTimeout(preview,delay);
}

async function show(blob,tag){
  if(lastURL)URL.revokeObjectURL(lastURL);
  lastURL=URL.createObjectURL(blob);
  if(kind==='video'){
    const v=document.createElement('video');
    v.src=lastURL;v.controls=v.muted=v.loop=v.autoplay=v.playsInline=true;
    v.onerror=()=>{
      fb.innerHTML='<div class=fallback>This browser can\'t decode HEVC 10-bit.<br>'
        +'The file itself is fine &mdash; download it and open in QuickTime or Safari.</div>';
    };
    fb.innerHTML='';fb.appendChild(v);
    if(!HEVC_OK)$('#error').textContent=
      'Heads up: this browser reports no HEVC 10-bit support, so the preview may stay blank. '
      +'The downloaded file is still valid \u2014 open it in QuickTime.';
  }else{
    fb.innerHTML='<img src="'+lastURL+'">';
  }
  $('#dl').disabled=false;$('#status').textContent=tag;
}

async function preview(){
  const my=++seq;
  fb.classList.add('busy');
  try{
    const r=await fetch('/hdr?'+params()+'&max=760');
    if(my!==seq)return;
    if(!r.ok)throw new Error(await r.text());
    await show(await r.blob(),'live · '+(+boost.value).toFixed(1)+'× above SDR white');
    $('#error').textContent='';
  }catch(e){$('#error').textContent=e.message}
  finally{if(my===seq)fb.classList.remove('busy')}
}

async function renderVideo(){
  const my=++seq;fb.classList.add('busy');$('#status').textContent='encoding video…';
  try{
    const r=await fetch('/hdr?'+params());
    if(!r.ok)throw new Error(await r.text());
    await show(await r.blob(),'rendered at '+nits.value+' nits');
    $('#error').textContent='';
  }catch(e){$('#error').textContent=e.message;$('#status').textContent='failed'}
  finally{fb.classList.remove('busy')}
}
$('#render').onclick=renderVideo;

$('#dl').onclick=()=>{location.href='/hdr?'+params()+'&dl=1'};
function params(){
  return 't='+token+'&boost='+boost.value+'&knee='+knee.value+'&nits='+nits.value
        +'&warmth='+warmth.value+'&vivid='+vivid.value+'&glow='+glow.value;
}
</script>
"""


def parse_multipart(body, boundary):
    fields, fname, fdata = {}, None, None
    for part in body.split(b"--" + boundary):
        head, sep, data = part.partition(b"\r\n\r\n")
        if not sep or b"Content-Disposition" not in head:
            continue
        data = data.rstrip(b"\r\n")
        name = re.search(rb'name="([^"]*)"', head)
        fn = re.search(rb'filename="([^"]*)"', head)
        if fn:
            fname, fdata = os.path.basename(fn.group(1).decode()), data
        elif name:
            fields[name.group(1).decode()] = data.decode(errors="replace")
    return fields, fname, fdata


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _send(self, code, ctype, body, extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    # ------------------------------------------------------------------ GET --
    def do_GET(self):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}

        if u.path == "/":
            return self._send(200, "text/html; charset=utf-8", PAGE.encode())

        sess = SESSIONS.get(q.get("t", ""))
        if u.path == "/orig":
            if not sess:
                return self._send(404, "text/plain", b"session expired")
            with open(sess["path"], "rb") as fh:
                return self._send(200, sess["mime"], fh.read())

        if u.path == "/ping":
            LAST_BEAT[0] = time.time()
            return self._send(200, "text/plain", b"ok")

        if u.path == "/bye":
            LAST_BEAT[0] = 0          # tab closed: shut down at the next check
            return self._send(200, "text/plain", b"bye")

        if u.path == "/hdr":
            if not sess:
                return self._send(404, "text/plain", b"session expired")
            try:
                body, mime, name = self._render(sess, q)
            except Exception as e:
                traceback.print_exc()
                return self._send(500, "text/plain", str(e)[:300].encode())
            extra = {"Content-Disposition": f'attachment; filename="{name}"'} if q.get("dl") else {}
            return self._send(200, mime, body, extra)

        self._send(404, "text/plain", b"not found")

    def _render(self, sess, q):
        boost = float(q.get("boost", 16))
        knee = float(q.get("knee", 0))
        nits = float(q.get("nits", 1600))
        warmth = float(q.get("warmth", 0))
        vivid = float(q.get("vivid", 1))
        glow = float(q.get("glow", 0))
        cap = int(q.get("max", 0))
        key = (sess["path"], sess["kind"], boost, knee, nits, cap, warmth, vivid, glow)
        with LOCK:
            hit = CACHE.get(key)
        if hit:
            return hit

        stem = os.path.splitext(sess["name"])[0]
        with tempfile.TemporaryDirectory() as td:
            src = sess["path"]
            if sess["kind"] == "image":
                if cap:
                    from PIL import Image
                    im = Image.open(src)
                    if max(im.size) > cap:
                        im.thumbnail((cap, cap), Image.LANCZOS)
                        src = os.path.join(td, "small.jpg")
                        im.convert("RGB").save(src, quality=92, subsampling=0)
                dst = os.path.join(td, "o.jpg")
                hdrify.hdrify_image(src, dst, boost=boost, knee=knee,
                                    warmth=warmth, vivid=vivid, glow=glow)
                mime, name = "image/jpeg", f"{stem}_hdr.jpg"
            else:
                dst = os.path.join(td, "o.mp4")
                hdrify.hdrify_video(src, dst, nits=nits)
                mime, name = "video/mp4", f"{stem}_hdr.mp4"
            with open(dst, "rb") as fh:
                out = (fh.read(), mime, name)

        with LOCK:
            if len(CACHE) > 24:
                CACHE.clear()
            CACHE[key] = out
        return out

    # ----------------------------------------------------------------- POST --
    def do_POST(self):
        if urlparse(self.path).path != "/upload":
            return self._send(404, "text/plain", b"not found")
        try:
            boundary = self.headers["Content-Type"].split("boundary=")[1].encode()
            body = self.rfile.read(int(self.headers["Content-Length"]))
            _, fname, data = parse_multipart(body, boundary)
            if not fname:
                raise ValueError("no file received")

            ext = os.path.splitext(fname)[1].lower()
            if ext in hdrify.IMAGE_EXT:
                kind = "image"
                mime = "image/jpeg" if ext in (".jpg", ".jpeg") else "image/png"
            elif ext in hdrify.VIDEO_EXT:
                kind, mime = "video", "video/mp4"
            else:
                raise ValueError(f"unsupported file type: {ext}")

            token = uuid.uuid4().hex[:10]
            path = os.path.join(WORK, f"{token}{ext}")
            with open(path, "wb") as fh:
                fh.write(data)

            w = h = 0
            if kind == "image":
                from PIL import Image
                w, h = Image.open(path).size
            SESSIONS[token] = {"path": path, "name": fname, "kind": kind, "mime": mime}
            self._send(200, "application/json", json.dumps(
                {"ok": True, "token": token, "kind": kind, "name": fname, "w": w, "h": h}).encode())
        except Exception as e:
            traceback.print_exc()
            self._send(200, "application/json",
                       json.dumps({"ok": False, "error": html.escape(str(e)[:200])}).encode())


if __name__ == "__main__":
    # Restore sessions from disk. Tokens used to live only in memory, so every
    # restart silently killed any open tab: its token 404'd and the UI just
    # stalled. Re-adopting the files keeps existing tabs working.
    restored = 0
    for f in sorted(os.listdir(WORK)):
        token, ext = os.path.splitext(f)
        if not ext:
            continue
        kind = "image" if ext.lower() in hdrify.IMAGE_EXT else (
               "video" if ext.lower() in hdrify.VIDEO_EXT else None)
        if not kind:
            continue
        SESSIONS[token] = {
            "path": os.path.join(WORK, f), "name": f, "kind": kind,
            "mime": "video/mp4" if kind == "video" else
                    ("image/jpeg" if ext.lower() in (".jpg", ".jpeg") else "image/png"),
        }
        restored += 1
    if restored:
        print(f"restored {restored} session(s) from disk")
    # daemon_threads: without it the process lingers after shutdown() waiting on
    # handler threads, so the port never actually closes.
    ThreadingHTTPServer.daemon_threads = True
    srv = ThreadingHTTPServer(("127.0.0.1", 8765), H)

    def watchdog():
        """Exit once no browser tab is holding the app open."""
        started = time.time()
        while True:
            time.sleep(2)
            beat = LAST_BEAT[0]
            if beat == 0:
                # nothing has ever connected — give the browser time to open
                if time.time() - started > GRACE:
                    break
                continue
            if time.time() - beat > IDLE_TIMEOUT:
                break
        print("no open tabs — shutting down")
        threading.Thread(target=srv.shutdown, daemon=True).start()
        # hard backstop: if anything is still holding the process 3s later, leave
        time.sleep(3)
        os._exit(0)

    threading.Thread(target=watchdog, daemon=True).start()
    print("hdrify studio -> http://localhost:8765  (closing the tab quits it)")
    try:
        srv.serve_forever()
    finally:
        srv.server_close()
