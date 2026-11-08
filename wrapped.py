"""Hermes Wrapped: what your agent did for you this week, what it cost, and how much of it was upkeep.

Everything comes from Hermes's own session records. No model calls.
"""

from __future__ import annotations

import json
import os
import re
import time
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from . import timeline as T
from .redact import short
from .render import _e, _fmt_cost, _fmt_duration, _fmt_tokens, base_css, render_html

# "Upkeep" = time spent working ON Hermes (setup, config, models, plugins, fixing the agent itself)
# rather than Hermes working FOR you. Strong title words count on their own; weak ones need the
# session's steps to actually touch Hermes too.
STRONG_TITLE = re.compile(
    r"\b(hermes|gateway|plugins?|profiles?|providers?|soul\.md|config(?:uration)?|api[ -]?keys?|"
    r"change model|switch model|model (?:setup|switch|change)|hermes model)\b",
    re.I,
)
WEAK_TITLE = re.compile(r"\b(setup|set up|install\w*|update|upgrade|auth\w*|token|cron|skills?|memory|debug\w*|troubleshoot\w*|broken|fix\w*|errors?)\b", re.I)
HERMES_STEP = re.compile(
    r"(~/\.hermes|\bhermes\s+(config|model|auth|plugins?|profile|gateway|update|doctor|setup|skills?|cron|memory)\b|"
    r"\bconfig\.yaml\b|\bSOUL\.md\b)",
    re.I,
)


def upkeep_reason(tl: T.Timeline) -> str | None:
    """Why a session counts as upkeep, or None if it was real work."""
    if tl.cwd.startswith("~/.hermes"):
        return "ran inside ~/.hermes"
    step_share = (sum(1 for s in tl.steps if HERMES_STEP.search(s.label or "")) / len(tl.steps)) if tl.steps else 0.0
    if STRONG_TITLE.search(tl.title or ""):
        return "about Hermes itself"
    if step_share >= 0.4:
        return f"{round(step_share * 100)}% of steps touched Hermes"
    if WEAK_TITLE.search(tl.title or "") and step_share >= 0.2:
        return "fixing or setting up Hermes"
    return None


@dataclass
class Period:
    start: float
    end: float
    sessions: int = 0
    steps: int = 0
    files: int = 0
    tokens: int = 0
    cost_usd: float = 0.0
    cost_known: bool = False
    active_seconds: float = 0.0
    upkeep_seconds: float = 0.0
    errors: int = 0
    tools: list[tuple[str, int]] = field(default_factory=list)
    top_work: list[dict[str, Any]] = field(default_factory=list)
    top_upkeep: list[dict[str, Any]] = field(default_factory=list)
    previous: "Period | None" = None

    @property
    def days(self) -> int:
        return max(1, round((self.end - self.start) / 86400))

    @property
    def work_seconds(self) -> float:
        return max(0.0, self.active_seconds - self.upkeep_seconds)

    @property
    def upkeep_pct(self) -> int:
        return round(100 * self.upkeep_seconds / self.active_seconds) if self.active_seconds else 0

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["previous"] = self.previous.to_dict() if self.previous else None
        d.update(work_seconds=self.work_seconds, upkeep_pct=self.upkeep_pct, days=self.days)
        return d


def _collect(start: float, end: float, db_path: str | None) -> Period:
    p = Period(start=start, end=end)
    tools: Counter[str] = Counter()
    work, upkeep = [], []
    for r in T.list_sessions(db_path, limit=1000, since=start):
        if r["started_at"] >= end or not (r.get("message_count") or 0):
            continue
        try:
            tl = T.load_timeline(r["id"], db_path, include_ancestors=False)
        except Exception:
            continue
        p.sessions += 1
        p.steps += len(tl.steps)
        p.files += len(tl.files_touched)
        p.tokens += tl.input_tokens + tl.output_tokens
        if tl.cost_usd is not None:
            p.cost_usd += tl.cost_usd
            p.cost_known = True
        p.active_seconds += tl.duration_seconds
        p.errors += sum(1 for s in tl.steps if s.error)
        for s in tl.steps:
            tools[s.category] += 1
        item = {"title": tl.title, "steps": len(tl.steps), "seconds": tl.duration_seconds}
        reason = upkeep_reason(tl)
        if reason:
            p.upkeep_seconds += tl.duration_seconds
            upkeep.append({**item, "reason": reason})
        else:
            if item["steps"] > 0:
                work.append(item)
    p.tools = tools.most_common(5)
    p.top_work = sorted(work, key=lambda x: -x["seconds"])[:3]
    p.top_upkeep = sorted(upkeep, key=lambda x: -x["seconds"])[:3]
    return p


def load_period(days: int = 7, db_path: str | None = None, now: float | None = None) -> Period:
    end = now or time.time()
    start = end - days * 86400
    cur = _collect(start, end, db_path)
    cur.previous = _collect(start - days * 86400, start, db_path)
    return cur


# Backwards-compatible name used by older callers/tests.
def load_week(days: int = 7, db_path: str | None = None, now: float | None = None) -> Period:
    return load_period(days, db_path, now)


def _period_word(days: int) -> str:
    return {7: "week", 1: "day", 14: "two weeks"}.get(
        days, "month" if 28 <= days <= 31 else "year" if 360 <= days <= 366 else f"{days} days")


def _delta(cur: float, prev: float, fmt) -> str:
    if prev <= 0 and cur <= 0:
        return ""
    if prev <= 0:
        return "new"
    diff = cur - prev
    if abs(diff) < 1e-9:
        return "same as before"
    sign = "+" if diff > 0 else "−"
    return f"{sign}{fmt(abs(diff))}"


def compose_wrapped_html(p: Period) -> str:
    word = _period_word(p.days)
    prev = p.previous if (p.previous and p.previous.sessions) else None
    heading = f"Your {word} with Hermes"
    rng = f"{datetime.fromtimestamp(p.start):%b %d} – {datetime.fromtimestamp(p.end):%b %d}"
    work_pct = (100 - p.upkeep_pct) if p.active_seconds else 0
    if p.sessions == 0:
        verdict = "A quiet week. Nothing to wrap."
    elif work_pct >= 70:
        verdict = "Mostly working for you."
    elif work_pct >= 45:
        verdict = "A fair split."
    else:
        verdict = f"More tamagotchi than assistant this {word}."
    if prev:
        d = p.upkeep_pct - prev.upkeep_pct
        trend = (
            f"Upkeep down from {prev.upkeep_pct}% last {word}" if d < 0
            else f"Upkeep up from {prev.upkeep_pct}% last {word}" if d > 0
            else f"Same upkeep as last {word}"
        )
    else:
        trend = f"First {word} on record"

    cells = [
        ("sessions", str(p.sessions), _delta(p.sessions, prev.sessions, lambda v: f"{v:.0f}") if prev else ""),
        ("steps taken", str(p.steps), _delta(p.steps, prev.steps, lambda v: f"{v:.0f}") if prev else ""),
        ("files edited", str(p.files), _delta(p.files, prev.files, lambda v: f"{v:.0f}") if prev else ""),
        ("active time", _fmt_duration(p.active_seconds), _delta(p.active_seconds, prev.active_seconds, _fmt_duration) if prev else ""),
        ("tokens", _fmt_tokens(p.tokens), _delta(p.tokens, prev.tokens, lambda v: _fmt_tokens(int(v))) if prev else ""),
        ("cost", _fmt_cost(p.cost_usd if p.cost_known else None), _delta(p.cost_usd, prev.cost_usd, lambda v: f"${v:.2f}") if prev else ""),
    ]
    max_tool = max((n for _, n in p.tools), default=1)

    plan = [("title", 3.6), ("numbers", 5.4)]
    if p.tools:
        plan.append(("tools", 4.4))
    if p.top_work:
        plan.append(("work", 4.4))
    plan.append(("split", 6.0))
    if p.top_upkeep:
        plan.append(("upkeep", 5.0))
    plan.append(("outro", 3.0))
    scenes, t = [], 0.0
    for name, length in plan:
        scenes.append({"name": name, "start": round(t, 3), "length": length})
        t += length
    total = round(t, 3)

    body = []
    for i, sc in enumerate(scenes):
        name = sc["name"]
        attrs = (f'id="sc-{name}" class="scene clip" data-start="{sc["start"]}" '
                 f'data-duration="{sc["length"]}" data-track-index="{i % 2}"')
        if name == "title":
            body.append(f"""<section {attrs}><div class="scene-content center">
  <div class="kicker" id="t-kicker"><span class="wing">◆</span> HERMES WRAPPED</div>
  <h1 class="headline" id="t-title">{_e(heading)}</h1>
  <div class="meta" id="t-meta">{_e(rng)}</div></div></section>""")
        elif name == "numbers":
            grid = "".join(
                f'<div class="cell"><div class="big">{_e(v)}</div><div class="cap">{_e(c)}</div>'
                + (f'<div class="delta">{_e(dl)} vs last {_e(word)}</div>' if dl and dl not in ("new",) else "")
                + "</div>"
                for c, v, dl in cells
            )
            body.append(f"""<section {attrs}><div class="scene-content">
  <div class="label" id="n-label">THE {_e(word.upper())} IN NUMBERS</div><div class="grid">{grid}</div></div></section>""")
        elif name == "tools":
            bars = "".join(
                f'<div class="bar-row"><span class="bar-name">{_e(cat)}</span>'
                f'<span class="bar-track"><span class="bar" style="width:{max(4, round(100 * n / max_tool))}%"></span></span>'
                f'<span class="bar-n">{n}</span></div>'
                for cat, n in p.tools
            )
            body.append(f"""<section {attrs}><div class="scene-content">
  <div class="label" id="b-label">WHAT IT REACHED FOR</div><div class="bars">{bars}</div></div></section>""")
        elif name == "work":
            rows = "".join(
                f'<div class="row"><span class="num">{k + 1:02d}</span><span class="lbl">{_e(short(s["title"], 58))}</span>'
                f'<span class="secs">{_e(_fmt_duration(s["seconds"]))} · {s["steps"]} steps</span></div>'
                for k, s in enumerate(p.top_work)
            )
            body.append(f"""<section {attrs}><div class="scene-content">
  <div class="label" id="w-label">WHAT IT DID FOR YOU</div><div class="rows">{rows}</div></div></section>""")
        elif name == "split":
            body.append(f"""<section {attrs}><div class="scene-content">
  <div class="label" id="u-label">WORKING FOR YOU vs. BEING WORKED ON</div>
  <div class="split">
    <div class="split-bar"><span class="split-work" style="width:{work_pct}%"></span><span class="split-upkeep" style="width:{p.upkeep_pct}%"></span></div>
    <div class="split-legend">
      <div><span class="dot mint"></span>Working for you · {_e(_fmt_duration(p.work_seconds))} · {work_pct}%</div>
      <div><span class="dot gold"></span>Upkeep: setup, config, fixing Hermes · {_e(_fmt_duration(p.upkeep_seconds))} · {p.upkeep_pct}%</div>
    </div>
  </div>
  <div class="verdict" id="u-verdict">{_e(verdict)}</div>
  <div class="trend" id="u-trend">{_e(trend)}</div></div></section>""")
        elif name == "upkeep":
            rows = "".join(
                f'<div class="row up"><span class="num">{k + 1:02d}</span><span class="lbl">{_e(short(s["title"], 50))}</span>'
                f'<span class="secs">{_e(_fmt_duration(s["seconds"]))} · {_e(s["reason"])}</span></div>'
                for k, s in enumerate(p.top_upkeep)
            )
            body.append(f"""<section {attrs}><div class="scene-content">
  <div class="label" id="k-label">WHAT ATE YOUR TIME</div><div class="rows">{rows}</div>
  <div class="hint" id="k-hint">Fix these once and next {_e(word)}'s bar turns greener.</div></div></section>""")
        elif name == "outro":
            body.append(f"""<section {attrs}><div class="scene-content center">
  <div class="outro-mark" id="o-mark">◆</div><div class="outro-title" id="o-title">See you next {_e(word)}</div>
  <div class="meta" id="o-sub">Hermes Wrapped · made from your sessions, no tokens spent</div></div></section>""")

    extra_css = """
.cell .delta { margin-top:10px; font-family:var(--mono); font-size:22px; color:var(--muted); }
#sc-numbers .cell .big { font-size:88px; white-space:nowrap; }
.bars { display:flex; flex-direction:column; gap:26px; }
.bar-row { display:flex; align-items:center; gap:30px; font-family:var(--mono); font-size:30px; }
.bar-name { width:260px; color:var(--muted); }
.bar-track { flex:1; height:44px; background:var(--panel); border-radius:10px; overflow:hidden; }
.bar { display:block; height:100%; background:var(--mint); border-radius:10px; transform-origin:left center; }
.bar-n { width:120px; text-align:right; font-variant-numeric:tabular-nums; }
.split { display:flex; flex-direction:column; gap:34px; }
.split-bar { display:flex; height:90px; border-radius:18px; overflow:hidden; background:var(--panel); }
.split-work { background:var(--mint); transform-origin:left center; }
.split-upkeep { background:var(--gold); transform-origin:left center; }
.split-legend { display:flex; flex-direction:column; gap:14px; font-family:var(--mono); font-size:32px; }
.dot { display:inline-block; width:22px; height:22px; border-radius:50%; margin-right:16px; vertical-align:middle; }
.dot.mint { background:var(--mint); } .dot.gold { background:var(--gold); }
.verdict { font-size:72px; font-weight:800; letter-spacing:-0.035em; }
.trend { font-family:var(--mono); font-size:30px; color:var(--muted); margin-top:-18px; }
.row.up .chip { background:var(--gold); }
.row.up { border-color:rgba(233,180,76,0.35); }
.hint { font-family:var(--mono); font-size:28px; color:var(--gold); }
"""
    js = """
(function () {
  window.__timelines = window.__timelines || {};
  var D = JSON.parse(document.getElementById('reel-data').textContent);
  var tl = gsap.timeline({ paused: true });
  function at(n) { for (var i = 0; i < D.scenes.length; i++) if (D.scenes[i].name === n) return D.scenes[i]; return null; }
  function sceneIn(n) { var s = at(n); if (!s) return null; tl.fromTo('#sc-' + n, { opacity: 0 }, { opacity: 1, duration: 0.45, ease: 'power2.out' }, s.start); return s; }
  function label(sel, s) { tl.from(sel, { x: -30, opacity: 0, duration: 0.5, ease: 'power2.out' }, s.start + 0.15); }
  var s;
  if ((s = sceneIn('title'))) {
    tl.from('#t-kicker', { y: 24, opacity: 0, duration: 0.6, ease: 'power3.out' }, s.start + 0.15);
    tl.from('#t-title', { y: 60, opacity: 0, filter: 'blur(10px)', duration: 0.9, ease: 'expo.out' }, s.start + 0.35);
    tl.from('#t-meta', { opacity: 0, duration: 0.7, ease: 'sine.out' }, s.start + 0.9);
  }
  if ((s = sceneIn('numbers'))) {
    label('#n-label', s);
    tl.from('#sc-numbers .cell', { y: 50, opacity: 0, scale: 0.96, duration: 0.7, stagger: 0.12, ease: 'expo.out' }, s.start + 0.35);
    tl.from('#sc-numbers .delta', { opacity: 0, duration: 0.5, stagger: 0.08, ease: 'sine.out' }, s.start + 1.4);
  }
  if ((s = sceneIn('tools'))) {
    label('#b-label', s);
    tl.from('#sc-tools .bar-row', { opacity: 0, y: 20, duration: 0.5, stagger: 0.1, ease: 'power3.out' }, s.start + 0.3);
    tl.from('#sc-tools .bar', { scaleX: 0, duration: 0.9, stagger: 0.1, ease: 'expo.out' }, s.start + 0.45);
  }
  if ((s = sceneIn('work'))) {
    label('#w-label', s);
    tl.from('#sc-work .row', { x: -80, opacity: 0, duration: 0.6, stagger: 0.35, ease: 'expo.out' }, s.start + 0.4);
  }
  if ((s = sceneIn('split'))) {
    label('#u-label', s);
    tl.from('.split-work', { scaleX: 0, duration: 1.0, ease: 'power3.out' }, s.start + 0.4);
    tl.from('.split-upkeep', { scaleX: 0, duration: 0.8, ease: 'power2.out' }, s.start + 1.2);
    tl.from('.split-legend > div', { opacity: 0, y: 16, duration: 0.5, stagger: 0.2, ease: 'sine.out' }, s.start + 1.6);
    tl.from('#u-verdict', { opacity: 0, y: 30, duration: 0.8, ease: 'back.out(1.4)' }, s.start + 2.4);
    tl.from('#u-trend', { opacity: 0, duration: 0.6, ease: 'sine.out' }, s.start + 3.1);
  }
  if ((s = sceneIn('upkeep'))) {
    label('#k-label', s);
    tl.from('#sc-upkeep .row', { x: 80, opacity: 0, duration: 0.6, stagger: 0.3, ease: 'power3.out' }, s.start + 0.4);
    tl.from('#k-hint', { opacity: 0, y: 14, duration: 0.6, ease: 'sine.out' }, s.start + 1.8);
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
<html lang="en"><head><meta charset="UTF-8" /><meta name="viewport" content="width=1920, height=1080" />
<script src="gsap.min.js"></script><style>{base_css()}{extra_css}</style></head>
<body><div id="stage" data-composition-id="root" data-start="0" data-duration="{total}" data-width="1920" data-height="1080">
{''.join(body)}
</div>
<script id="reel-data" type="application/json">{json.dumps({"scenes": scenes})}</script>
<script>{js}</script></body></html>
"""


def text_summary(p: Period) -> str:
    """Plain-text Wrapped for chats and for platforms where a video can't be sent."""
    word = _period_word(p.days)
    if p.sessions == 0:
        return f"Your {word} with Hermes: no sessions yet. Nothing to wrap."
    lines = [
        f"Your {word} with Hermes",
        f"{p.sessions} sessions · {p.steps} steps · {p.files} files edited · {_fmt_duration(p.active_seconds)} active · "
        f"{_fmt_cost(p.cost_usd if p.cost_known else None)}",
        f"Working for you {100 - p.upkeep_pct if p.active_seconds else 0}% · upkeep {p.upkeep_pct}%",
    ]
    if p.top_upkeep:
        lines.append("What ate your time: " + "; ".join(f"{s['title']} ({_fmt_duration(s['seconds'])})" for s in p.top_upkeep))
    return "\n".join(lines)


def render_wrapped(p: Period, out_dir: str | os.PathLike[str], *, quality: str = "standard") -> Path:
    return render_html(compose_wrapped_html(p), out_dir, data=p.to_dict(), quality=quality)
