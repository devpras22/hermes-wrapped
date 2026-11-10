"""Build a HyperFrames composition from a timeline and render it to MP4.

The composition is written by code, not by a model: same session in, same video out.
"""

from __future__ import annotations

import html
import json
import os
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .timeline import Timeline, pick_highlights

HYPERFRAMES_VERSION = "0.8.217"  # pinned: catalog rule 3 (no floating remote code)
ASSETS = Path(__file__).resolve().parent / "assets"

PALETTE = {
    "ground": "#0C0C0E",
    "panel": "#16161A",
    "line": "#2A2A30",
    "ink": "#F3F0E8",
    "muted": "#8E8A80",
    "gold": "#E9B44C",
    "mint": "#7BD3B4",
    "red": "#FF6B5E",
}

SOURCE_NAMES = {
    "cli": "CLI", "tui": "Terminal", "acp": "Editor", "telegram": "Telegram", "discord": "Discord",
    "slack": "Slack", "whatsapp": "WhatsApp", "signal": "Signal", "cron": "Cron job", "desktop": "Desktop",
    "api": "API", "matrix": "Matrix",
}


def _e(text: Any) -> str:
    return html.escape(str(text), quote=True)


def _fmt_duration(sec: float) -> str:
    sec = int(round(sec))
    if sec < 60:
        return f"{sec}s"
    if sec < 3600:
        return f"{sec // 60}m {sec % 60:02d}s"
    return f"{sec // 3600}h {(sec % 3600) // 60:02d}m"


def _fmt_tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(n)


def _fmt_cost(c: float | None) -> str:
    if c is None:
        return "n/a"
    if c == 0:
        return "$0"
    return f"${c:.2f}" if c >= 0.01 else "<$0.01"


def _step_secs(s: float | None) -> str:
    if s is None:
        return ""
    return f"{s:.1f}s" if s < 60 else _fmt_duration(s)


def base_css() -> str:
    P = PALETTE
    return f"""
:root {{ --ground:{P['ground']}; --panel:{P['panel']}; --line:{P['line']}; --ink:{P['ink']}; --muted:{P['muted']};
  --gold:{P['gold']}; --mint:{P['mint']}; --red:{P['red']}; --display:'Inter', sans-serif; --mono:'JetBrains Mono', monospace; }}
* {{ margin:0; padding:0; box-sizing:border-box; }}
html, body {{ width:1920px; height:1080px; overflow:hidden; background:var(--ground); color:var(--ink); font-family:var(--display); }}
#stage {{ position:relative; width:1920px; height:1080px; background:
  radial-gradient(1200px 700px at 85% 10%, rgba(233,180,76,0.07), transparent 70%), var(--ground); }}
.scene {{ position:absolute; inset:0; }}
.scene-content {{ width:100%; height:100%; padding:120px 160px; display:flex; flex-direction:column; gap:40px; justify-content:center; }}
.center {{ align-items:center; text-align:center; }}
.kicker, .label {{ font-family:var(--mono); font-size:26px; letter-spacing:0.28em; color:var(--gold); }}
.wing {{ display:inline-block; margin-right:10px; }}
.headline {{ font-size:96px; font-weight:700; letter-spacing:-0.035em; line-height:1.04; max-width:1500px; }}
.meta {{ font-family:var(--mono); font-size:28px; color:var(--muted); }}
.quote {{ font-size:64px; font-weight:500; letter-spacing:-0.02em; line-height:1.18; max-width:1500px; }}
.work {{ flex-direction:row; align-items:center; gap:90px; }}
.work-left {{ flex:1; display:flex; flex-direction:column; gap:30px; min-width:0; }}
.rows {{ display:flex; flex-direction:column; gap:14px; }}
.row {{ display:flex; align-items:center; gap:24px; background:var(--panel); border:1px solid var(--line); border-radius:14px;
  padding:16px 26px; font-family:var(--mono); font-size:27px; }}
.row .num {{ color:var(--muted); width:52px; font-variant-numeric:tabular-nums; }}
.row .chip {{ color:var(--ground); background:var(--mint); border-radius:8px; padding:4px 12px; font-size:21px; font-weight:700; white-space:nowrap; }}
.row .lbl {{ flex:1; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; min-width:0; }}
.row .secs {{ color:var(--muted); font-variant-numeric:tabular-nums; white-space:nowrap; }}
.row.err {{ border-color:rgba(255,107,94,0.55); }}
.row.err .chip {{ background:var(--red); }}
.row.err .secs {{ color:var(--red); }}
.more {{ font-family:var(--mono); font-size:26px; color:var(--muted); padding-left:26px; }}
.work-right {{ width:380px; display:flex; flex-direction:column; align-items:flex-end; }}
.counter {{ font-size:220px; font-weight:800; letter-spacing:-0.05em; line-height:0.9; color:var(--gold); font-variant-numeric:tabular-nums; }}
.counter-cap {{ font-family:var(--mono); font-size:30px; color:var(--muted); letter-spacing:0.2em; }}
.clock {{ margin-top:36px; font-family:var(--mono); font-size:44px; font-variant-numeric:tabular-nums; }}
.grid {{ display:grid; grid-template-columns:repeat(3, 1fr); gap:28px; }}
.cell {{ background:var(--panel); border:1px solid var(--line); border-radius:20px; padding:44px 48px; }}
.cell .big {{ font-size:104px; font-weight:800; letter-spacing:-0.04em; font-variant-numeric:tabular-nums; }}
.cell .cap {{ margin-top:6px; font-family:var(--mono); font-size:26px; color:var(--muted); letter-spacing:0.1em; }}
.answer {{ font-size:52px; line-height:1.3; font-weight:500; letter-spacing:-0.015em; max-width:1560px; }}
.outro-mark {{ font-size:90px; color:var(--gold); }}
.outro-title {{ font-size:88px; font-weight:800; letter-spacing:-0.035em; }}
"""


def build_schedule(tl: Timeline) -> dict[str, Any]:
    """Scene start times and lengths in seconds. Length grows with the number of steps shown."""
    shown = pick_highlights(tl.steps, 7)
    work_len = 2.2 + 1.05 * max(1, len(shown)) + (1.6 if len(tl.steps) > len(shown) else 0)
    scenes = [
        ("title", 3.6),
        ("ask", 4.6 if tl.ask else 0),
        ("work", work_len if tl.steps else 0),
        ("stats", 4.8),
        ("answer", 5.2 if tl.answer else 0),
        ("outro", 3.0),
    ]
    out, t = [], 0.0
    for name, length in scenes:
        if length <= 0:
            continue
        out.append({"name": name, "start": round(t, 3), "length": round(length, 3)})
        t += length
    return {"scenes": out, "total": round(t, 3), "shown": shown}


def compose_html(tl: Timeline, *, gsap_src: str = "gsap.min.js") -> str:
    sched = build_schedule(tl)
    scenes, total, shown = sched["scenes"], sched["total"], sched["shown"]
    when = datetime.fromtimestamp(tl.started_at, tz=timezone.utc).strftime("%b %d, %Y")
    source = SOURCE_NAMES.get(tl.source, tl.source.title())
    P = PALETTE
    hidden = len(tl.steps) - len(shown)
    files_n = len(tl.files_touched)
    errors_n = sum(1 for s in tl.steps if s.error)

    body: list[str] = []
    for i, sc in enumerate(scenes):
        name, start, length = sc["name"], sc["start"], sc["length"]
        attrs = f'id="sc-{name}" class="scene clip" data-start="{start}" data-duration="{length}" data-track-index="{i % 2}"'
        if name == "title":
            body.append(f"""
<section {attrs}>
  <div class="scene-content center">
    <div class="kicker" id="t-kicker"><span class="wing">◆</span> HERMES WRAPPED · SESSION</div>
    <h1 class="headline" id="t-title">{_e(tl.title)}</h1>
    <div class="meta" id="t-meta">{_e(when)} · {_e(source)} · {_e(tl.model)}</div>
  </div>
</section>""")
        elif name == "ask":
            body.append(f"""
<section {attrs}>
  <div class="scene-content">
    <div class="label" id="a-label">THE ASK</div>
    <blockquote class="quote" id="a-quote">“{_e(tl.ask)}”</blockquote>
  </div>
</section>""")
        elif name == "work":
            rows = []
            for j, st in enumerate(shown):
                cls = "row err" if st.error else "row"
                rows.append(
                    f'<div class="{cls}" id="w-row-{j}"><span class="num">{st.index:02d}</span>'
                    f'<span class="chip">{_e(st.glyph)} {_e(st.category)}</span>'
                    f'<span class="lbl">{_e(st.label)}</span>'
                    f'<span class="secs">{_e("failed · " if st.error else "")}{_e(_step_secs(st.seconds))}</span></div>'
                )
            more = f'<div class="more" id="w-more">+ {hidden} more steps</div>' if hidden > 0 else ""
            body.append(f"""
<section {attrs}>
  <div class="scene-content work">
    <div class="work-left">
      <div class="label" id="w-label">THE WORK</div>
      <div class="rows">{''.join(rows)}{more}</div>
    </div>
    <div class="work-right">
      <div class="counter" id="w-counter">00</div>
      <div class="counter-cap" id="w-cap">steps</div>
      <div class="clock" id="w-clock">0s</div>
    </div>
  </div>
</section>""")
        elif name == "stats":
            cells = [
                ("steps", str(len(tl.steps))),
                ("active time", _fmt_duration(tl.duration_seconds)),
                ("files edited", str(files_n)),
                ("tokens", _fmt_tokens(tl.input_tokens + tl.output_tokens)),
                ("cost", _fmt_cost(tl.cost_usd)),
                ("errors recovered" if errors_n else "errors", str(errors_n)),
            ]
            grid = "".join(
                f'<div class="cell" id="s-cell-{k}"><div class="big">{_e(v)}</div><div class="cap">{_e(c)}</div></div>'
                for k, (c, v) in enumerate(cells)
            )
            body.append(f"""
<section {attrs}>
  <div class="scene-content">
    <div class="label" id="s-label">BY THE NUMBERS</div>
    <div class="grid">{grid}</div>
  </div>
</section>""")
        elif name == "answer":
            body.append(f"""
<section {attrs}>
  <div class="scene-content">
    <div class="label" id="r-label">THE RESULT</div>
    <p class="answer" id="r-text">{_e(tl.answer)}</p>
  </div>
</section>""")
        elif name == "outro":
            body.append(f"""
<section {attrs}>
  <div class="scene-content center">
    <div class="outro-mark" id="o-mark">◆</div>
    <div class="outro-title" id="o-title">Made by Hermes Wrapped</div>
    <div class="meta" id="o-sub">no screen recording · rendered from the session itself</div>
  </div>
</section>""")

    data = {
        "scenes": scenes,
        "rows": len(shown),
        "hidden": hidden,
        "steps": len(tl.steps),
        "active": round(tl.duration_seconds),
    }

    css = base_css()

    js = """
(function () {
  window.__timelines = window.__timelines || {};
  var D = JSON.parse(document.getElementById('reel-data').textContent);
  var tl = gsap.timeline({ paused: true });
  function at(name) { for (var i = 0; i < D.scenes.length; i++) if (D.scenes[i].name === name) return D.scenes[i]; return null; }
  function sceneIn(name) {
    var s = at(name); if (!s) return null;
    // crossfade transition: the incoming scene rises in over the outgoing one
    tl.fromTo('#sc-' + name, { opacity: 0 }, { opacity: 1, duration: 0.45, ease: 'power2.out' }, s.start);
    return s;
  }
  var s;
  if ((s = sceneIn('title'))) {
    tl.from('#t-kicker', { y: 24, opacity: 0, duration: 0.6, ease: 'power3.out' }, s.start + 0.15);
    tl.from('#t-title', { y: 60, opacity: 0, filter: 'blur(10px)', duration: 0.9, ease: 'expo.out' }, s.start + 0.35);
    tl.from('#t-meta', { opacity: 0, duration: 0.7, ease: 'sine.out' }, s.start + 0.9);
  }
  if ((s = sceneIn('ask'))) {
    tl.from('#a-label', { x: -30, opacity: 0, duration: 0.5, ease: 'power2.out' }, s.start + 0.15);
    tl.from('#a-quote', { y: 40, opacity: 0, duration: 1.0, ease: 'power4.out' }, s.start + 0.35);
  }
  if ((s = sceneIn('work'))) {
    tl.from('#w-label', { x: -30, opacity: 0, duration: 0.5, ease: 'power2.out' }, s.start + 0.15);
    tl.from('#w-counter, #w-cap, #w-clock', { opacity: 0, y: 20, duration: 0.6, stagger: 0.08, ease: 'back.out(1.6)' }, s.start + 0.25);
    var per = 1.05, t0 = s.start + 0.7;
    for (var i = 0; i < D.rows; i++) {
      tl.from('#w-row-' + i, { x: -80, opacity: 0, duration: 0.55, ease: i % 2 ? 'power3.out' : 'expo.out' }, t0 + i * per);
    }
    var countEnd = t0 + Math.max(1, D.rows) * per + (D.hidden > 0 ? 1.2 : 0);
    if (D.hidden > 0) tl.from('#w-more', { opacity: 0, y: 12, duration: 0.5, ease: 'sine.out' }, t0 + D.rows * per);
    var c = { n: 0, t: 0 };
    var counterEl = document.getElementById('w-counter'), clockEl = document.getElementById('w-clock');
    function fmt(sec) { sec = Math.round(sec); if (sec < 60) return sec + 's'; var m = Math.floor(sec / 60), r = sec % 60;
      if (m < 60) return m + 'm ' + (r < 10 ? '0' : '') + r + 's'; return Math.floor(m / 60) + 'h ' + (m % 60 < 10 ? '0' : '') + (m % 60) + 'm'; }
    tl.to(c, { n: D.steps, t: D.active, duration: Math.max(1, countEnd - t0), ease: 'power1.inOut',
      onUpdate: function () { var n = Math.round(c.n); counterEl.textContent = (n < 10 ? '0' : '') + n; clockEl.textContent = fmt(c.t); } }, t0);
  }
  if ((s = sceneIn('stats'))) {
    tl.from('#s-label', { x: -30, opacity: 0, duration: 0.5, ease: 'power2.out' }, s.start + 0.15);
    tl.from('.cell', { y: 50, opacity: 0, scale: 0.96, duration: 0.7, stagger: 0.12, ease: 'expo.out' }, s.start + 0.35);
  }
  if ((s = sceneIn('answer'))) {
    tl.from('#r-label', { x: -30, opacity: 0, duration: 0.5, ease: 'power2.out' }, s.start + 0.15);
    tl.from('#r-text', { y: 36, opacity: 0, duration: 1.0, ease: 'power3.out' }, s.start + 0.35);
  }
  if ((s = sceneIn('outro'))) {
    tl.from('#o-mark', { scale: 0.4, opacity: 0, rotation: -45, duration: 0.8, ease: 'back.out(2)' }, s.start + 0.15);
    tl.from('#o-title', { y: 30, opacity: 0, duration: 0.7, ease: 'power3.out' }, s.start + 0.4);
    tl.from('#o-sub', { opacity: 0, duration: 0.6, ease: 'sine.out' }, s.start + 0.8);
    tl.to('#sc-outro', { opacity: 0, duration: 0.5, ease: 'sine.in' }, s.start + s.length - 0.5);
  }
  window.__timelines['root'] = tl;
})();
"""

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=1920, height=1080" />
<script src="{_e(gsap_src)}"></script>
<style>{css}</style>
</head>
<body>
<div id="stage" data-composition-id="root" data-start="0" data-duration="{total}" data-width="1920" data-height="1080">
{''.join(body)}
</div>
<script id="reel-data" type="application/json">{json.dumps(data)}</script>
<script>{js}</script>
</body>
</html>
"""


class RenderError(RuntimeError):
    pass


# Variables the render tools (npx/npm, HyperFrames, headless Chrome, ffmpeg) actually need. Everything else in the
# Hermes process environment (provider API keys, tokens, gateway secrets) is NOT handed to them.
_ENV_KEYS = (
    "PATH", "HOME", "USER", "LOGNAME", "SHELL", "LANG", "LANGUAGE", "LC_ALL", "LC_CTYPE", "TZ", "TERM",
    "TMPDIR", "TMP", "TEMP",
    "XDG_CACHE_HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_RUNTIME_DIR",
    "NPM_CONFIG_CACHE", "NPM_CONFIG_PREFIX", "NPM_CONFIG_REGISTRY", "NPM_CONFIG_USERCONFIG",
    "npm_config_cache", "npm_config_prefix", "npm_config_registry", "npm_config_userconfig",
    "PUPPETEER_CACHE_DIR", "PUPPETEER_EXECUTABLE_PATH", "CHROME_PATH",
    "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "no_proxy",
    "SSL_CERT_FILE", "SSL_CERT_DIR", "NODE_EXTRA_CA_CERTS",
    "SYSTEMROOT", "COMSPEC", "PATHEXT", "APPDATA", "LOCALAPPDATA", "USERPROFILE",  # Windows
)


def clean_env() -> dict[str, str]:
    """A minimal environment for render subprocesses: an explicit allow-list, never the inherited one."""
    env = {k: os.environ[k] for k in _ENV_KEYS if k in os.environ}
    env.setdefault("PATH", os.defpath)
    return env


def hyperframes_available() -> tuple[bool, str]:
    for tool in ("node", "npx", "ffmpeg"):
        if not shutil.which(tool):
            return False, f"{tool} is not installed (Hermes Wrapped needs Node 22+, npx and ffmpeg)"
    return True, "ok"


def render(tl: Timeline, out_dir: str | os.PathLike[str], *, quality: str = "standard", timeout: int = 600) -> Path:
    """Write the session composition into out_dir and render it. Returns the MP4 path."""
    return render_html(compose_html(tl), out_dir, data=tl.to_dict(), quality=quality, timeout=timeout)


def render_html(
    page: str, out_dir: str | os.PathLike[str], *, data: Any = None, quality: str = "standard", timeout: int = 600
) -> Path:
    """Render any composition page with the pinned HyperFrames CLI."""
    ok, why = hyperframes_available()
    if not ok:
        raise RenderError(why)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ASSETS / "gsap.min.js", out / "gsap.min.js")
    if (ASSETS / "fonts").is_dir():  # bundled OFL fonts, so every machine renders the same type
        shutil.copytree(ASSETS / "fonts", out / "fonts", dirs_exist_ok=True)
    (out / "index.html").write_text(page, encoding="utf-8")
    if data is not None:
        (out / "timeline.json").write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    mp4 = out / "reel.mp4"
    if mp4.exists():
        mp4.unlink()
    cmd = [
        "npx", "--yes", f"hyperframes@{HYPERFRAMES_VERSION}", "render",
        "--quality", quality, "--output", str(mp4),
    ]
    started = time.time()
    try:
        proc = subprocess.run(cmd, cwd=out, capture_output=True, text=True, timeout=timeout, env=clean_env())
    except subprocess.TimeoutExpired as exc:
        raise RenderError(f"render timed out after {timeout}s") from exc
    if proc.returncode != 0 or not mp4.exists() or mp4.stat().st_size == 0:
        tail = (proc.stderr or proc.stdout or "")[-1500:]
        raise RenderError(f"hyperframes render failed (exit {proc.returncode}):\n{tail}")
    (out / "render.log").write_text(f"{time.time() - started:.1f}s\n{proc.stdout[-4000:]}", encoding="utf-8")
    return mp4
