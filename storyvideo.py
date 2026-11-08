"""Turn a Story into a ~40 second HyperFrames video with its own soundtrack.

Scenes are chosen by the data: a scene with nothing interesting to say is skipped.
"""

from __future__ import annotations

import json
import math
import os
import re
from pathlib import Path
from typing import Any

from . import fontfit as F
from .music import make_music, mood_for
from .render import _e, base_css, render_html
from .story import Story, _hour_label

FORMATS = {"landscape": (1920, 1080), "portrait": (1080, 1920)}


def _money(v: float) -> str:
    return f"${v:,.0f}" if v >= 100 else f"${v:,.2f}"


def _n(k: int, word: str) -> str:
    return f"{k} {word}" + ("" if k == 1 else "s")


def _hours(v: float) -> str:
    return f"{v:.0f}" if v >= 10 else f"{v:.1f}".rstrip("0").rstrip(".")


def _per(txt: str, w: str) -> str:
    """Canned lines say "week"; match the period actually wrapped."""
    return re.sub(r"\bweek\b", w, txt) if w in ("day", "month", "year") else txt


def plan_scenes(s: Story, *, show_cost: bool = True, show_titles: bool = True,
                copy: dict[str, str] | None = None) -> list[dict[str, Any]]:
    """Ordered scenes with their on-screen lines. Pure function of the Story."""
    copy = copy or {}
    w = s.period_word
    sc: list[dict[str, Any]] = []

    def per(txt: str) -> str:
        return _per(txt, w)

    def add(name: str, length: float, say: str, **kw: Any) -> None:
        sc.append({"name": name, "length": length, "say": say, **kw})

    # 0. Title: the brand, big
    add("title", 2.8, "Hermes Wrapped.", sub=(f"your {s.period_label}" if s.period_label.startswith("week")
                                              else "your year with Hermes" if w == "year" else s.period_label))

    # 1. Hook
    if s.sessions == 0:
        add("hook", 4.0, f"A quiet {w} with Hermes.", big="0", line1=f"sessions this {w}.",
            line2=copy.get("hook") or f"Next {w} is yours.")
        add("verdict", 6.0, f"You are {s.archetype}.", **_verdict(s, show_cost, copy))
        return sc
    if s.gap_days:
        add("hook", 3.8, f"You were gone {s.gap_days} days. Then you came back swinging.",
            big=str(s.gap_days), line1="days away.", line2=copy.get("hook") or "Then you came back swinging.")
    elif s.first_week:
        add("hook", 3.6, f"Your first {w} with Hermes.", big="1ST", line1=f"Your first {w} with Hermes.",
            line2=copy.get("hook") or "Let's see what you built.")
    else:
        add("hook", 3.6, f"Your {w} with Hermes.", big=str(s.sessions),
            line1=f"{'session' if s.sessions == 1 else 'sessions'} this {w}.",
            line2=copy.get("hook") or "Let's see what you did.")

    # 2. Scale: agent-hours vs. your hours
    if s.agent_hours >= s.wall_hours * 1.15 and s.wall_hours > 0:
        add("scale", 4.6, f"Hermes worked {_hours(s.agent_hours)} hours. You watched {_hours(s.wall_hours)}.",
            a=s.agent_hours, b=s.wall_hours,
            line1=f"Hermes worked {_hours(s.agent_hours)} hours.", line2=f"You watched {_hours(s.wall_hours)}.")
    else:
        add("scale", 4.0, f"{_n(s.sessions, 'session')} over {_n(s.active_days, 'day')}.", a=s.agent_hours, b=0,
            line1=f"{_hours(s.agent_hours)} hours of work.", line2=f"{_n(s.sessions, 'session')} over {_n(max(1, s.active_days), 'day')}.")

    # 3. Crew
    deleg = s.delegated / s.sessions if s.sessions else 0
    if deleg >= 0.3 or s.peak_agents >= 3:
        add("crew", 4.8, f"You didn't use an agent. You ran a team. {s.peak_agents} agents at once.",
            n=max(2, min(8, s.peak_agents)),
            line1="You didn't use an agent.", line2="You ran a team.",
            line3=f"{s.peak_agents} agents at once · {s.peak_when}")

    # 4. Clock
    if s.asks >= 5:
        pct = round(s.night_share * 100)
        sub = (f"{pct}% of your asks came after 11pm." if pct >= 15
               else "A daytime operator." if 8 <= s.peak_hour <= 18 else "An evening person.")
        add("clock", 5.0, f"Your peak hour: {_hour_label(s.peak_hour)}. {sub}",
            hours=s.ask_hours, peak=s.peak_hour, hour=_hour_label(s.peak_hour), line1=f"Your peak hour: {_hour_label(s.peak_hour)}.", line2=sub)

    # 5. Latest message
    if s.latest_ask:
        add("late", 4.4, f"Latest message: {s.latest_ask}.", line1="Latest message", when=s.latest_ask,
            quote=s.latest_ask_text, line2="" if s.latest_ask_text else "Hermes was still up.")

    # 6. Biggest job (a session plus everything it spawned)
    if s.big_job and s.big_job_minutes >= 10:
        mins = s.big_job_minutes
        dur = f"{mins} minutes" if mins < 120 else f"{_hours(mins / 60)} hours of work"
        meta = [dur]
        if s.big_job_helpers:
            meta.append(f"{s.big_job_helpers} helpers")
        if show_cost and s.big_job_cost:
            meta.append(_money(s.big_job_cost))
        title = s.big_job if show_titles else "Your biggest project"
        add("job", 4.4, f"Biggest job: {title}. {dur}.", title=title, meta=" · ".join(meta))

    # 7. Your thing (or the agent's eyes as a fallback)
    if s.tic:
        add("tic", 4.2, f"You said {s.tic} {s.tic_count} times. {copy.get('tic') or per(s.tic_line)}",
            word=s.tic, count=s.tic_count, line2=copy.get("tic") or per(s.tic_line))
    elif s.vision_checks >= 20 or s.browser_actions >= 50:
        add("eyes", 4.0, f"Hermes checked its own work {s.vision_checks} times.",
            line1=f"Hermes looked at its own work {s.vision_checks} times.",
            line2=f"{s.browser_actions} browser clicks. {s.edits} file edits.")

    # 8. Receipt
    rows = []
    if show_cost and s.cost > 0:
        rows.append((f"This {w}" if w in ("day", "week", "month", "year") else "This period", _money(s.cost)))
        if s.lifetime_cost > s.cost * 1.05:
            rows.append(("All time", _money(s.lifetime_cost)))
    rows.append(("Working for you", f"{100 - s.upkeep_pct}%"))
    rows.append(("Upkeep", f"{s.upkeep_pct}%"))
    share = s.cost / s.lifetime_cost if s.lifetime_cost else 0
    if show_cost and s.cost > 0 and 0.5 <= share < 0.999 and s.lifetime_cost > s.cost * 1.05:
        note = f"{round(share * 100)}% of everything you've ever spent on Hermes."
    elif s.upkeep_pct < 20:
        note = "Mostly working for you."
    elif s.upkeep_pct < 45:
        note = "A fair split."
    else:
        note = "More tamagotchi than assistant."
    upkeep_note = f"Upkeep: just {s.upkeep_pct}%." if s.upkeep_pct < 20 else f"Upkeep: {s.upkeep_pct}%."
    add("receipt", 5.2, f"{note} {upkeep_note}", rows=rows, note=note)

    # 9. Verdict
    add("verdict", 6.6, f"You are {s.archetype}.", **_verdict(s, show_cost, copy))
    return sc


def _verdict(s: Story, show_cost: bool, copy: dict[str, str]) -> dict[str, Any]:
    stats = [_n(s.sessions, "session"), f"{_hours(s.agent_hours)}h of work"]
    if show_cost and s.cost:
        stats.append(_money(s.cost))
    return {
        "name_text": s.archetype.replace("The ", "THE ").upper(),
        "side": f"with a side of {s.side_trait}" if s.side_trait else "",
        "line": copy.get("verdict") or _per(s.archetype_line, s.period_word),
        "stats": " · ".join(stats),
        "footer": f"Hermes Wrapped · {s.period_label}",
    }


def _timed(scenes: list[dict[str, Any]]) -> float:
    t = 0.0
    for sc in scenes:
        sc["start"] = round(t, 3)
        t += sc["length"]
    return round(t, 3)


def _clock_svg(hours: list[int], peak: int, size: int = 760) -> str:
    top = max(hours) or 1
    cx = cy = 260
    parts = [f'<circle cx="{cx}" cy="{cy}" r="136" fill="none" stroke="var(--line)" stroke-width="3" stroke-dasharray="4 8"/>']
    for h, n in enumerate(hours):
        ang = (h / 24) * 2 * math.pi - math.pi / 2
        r0, r1 = 150, 150 + 98 * (n / top)
        x0, y0 = cx + r0 * math.cos(ang), cy + r0 * math.sin(ang)
        x1, y1 = cx + max(r1, r0 + 6) * math.cos(ang), cy + max(r1, r0 + 6) * math.sin(ang)
        night = h >= 23 or h < 4
        color = "var(--amber)" if h == peak else ("var(--ink)" if night else "rgba(216,202,172,0.5)")
        glow = ' filter="url(#glow)"' if h == peak or night else ""
        parts.append(f'<line class="hbar" x1="{x0:.1f}" y1="{y0:.1f}" x2="{x1:.1f}" y2="{y1:.1f}" '
                     f'stroke="{color}" stroke-width="18"{glow}/>')
    for h, lab in ((0, "12AM"), (6, "6AM"), (12, "12PM"), (18, "6PM")):
        ang = (h / 24) * 2 * math.pi - math.pi / 2
        x, y = cx + 100 * math.cos(ang), cy + 100 * math.sin(ang) + 12
        parts.append(f'<text x="{x:.1f}" y="{y:.1f}" text-anchor="middle" fill="var(--dim)" '
                     f'font-family="VT323, monospace" font-size="40">{lab}</text>')
    defs = ('<defs><filter id="glow" x="-50%" y="-50%" width="200%" height="200%"><feGaussianBlur stdDeviation="4" result="b"/>'
            '<feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter></defs>')
    return f'<svg viewBox="0 0 520 520" width="{size}" height="{size}">{defs}{"".join(parts)}</svg>'


def _typed(text: str) -> str:
    """Characters as spans so the timeline can type them out, plus a block cursor glued to the last word."""
    head, _, last = text.rpartition(" ")
    chars = lambda s: "".join(f'<span class="tc">{_e(c)}</span>' for c in s)
    lead = chars(head + " ") if head else ""
    return f'{lead}<span class="nw">{chars(last)}<span class="cur"></span></span>'


def _crew_svg(n: int, width: int) -> str:
    """Hermes in the middle, n terminal windows fanned around it, dotted links (Nous 'one gateway' grammar)."""
    cx, cy = 450, 250
    parts = []
    for k in range(n):
        a = math.pi * (1.08 + 0.84 * (k / max(1, n - 1))) if n > 1 else math.pi * 1.5
        x, y = cx + 330 * math.cos(a), cy + 190 * math.sin(a)
        parts.append(f'<line class="cl" x1="{cx}" y1="{cy - 40}" x2="{x:.0f}" y2="{y + 34:.0f}" stroke="var(--ink)" '
                     f'stroke-width="3" stroke-dasharray="3 9" opacity="0.7"/>')
        parts.append(f'<g class="cw" transform="translate({x - 52:.0f} {y - 34:.0f})" filter="url(#cglow)">'
                     '<rect width="104" height="70" fill="var(--bg)" stroke="var(--ink)" stroke-width="4"/>'
                     '<line x1="0" y1="16" x2="104" y2="16" stroke="var(--ink)" stroke-width="3"/>'
                     '<text x="14" y="56" fill="var(--amber)" font-family="VT323, monospace" font-size="34">&gt;_</text></g>')
    hub = (f'<g id="c-hub" filter="url(#cglow)"><rect x="{cx - 46}" y="{cy - 46}" width="92" height="92" fill="none" '
           f'stroke="var(--amber)" stroke-width="5"/><rect x="{cx - 22}" y="{cy - 22}" width="44" height="44" fill="var(--amber)"/></g>')
    defs = ('<defs><filter id="cglow" x="-40%" y="-40%" width="180%" height="180%"><feGaussianBlur stdDeviation="5" result="b"/>'
            '<feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter></defs>')
    return f'<svg id="c-net" viewBox="0 0 900 330" width="{width}" height="{round(width * 330 / 900)}">{defs}{"".join(parts)}{hub}</svg>'


MOON = ('<svg class="icon" id="l-moon" viewBox="0 0 120 120" width="150" height="150"><path d="M78 14a48 48 0 1 0 28 76A40 40 0 0 1 78 14z" '
        'fill="none" stroke="var(--amber)" stroke-width="6"/><path d="M92 30h8M96 26v8M30 22h6M33 19v6" stroke="var(--ink)" stroke-width="4"/></svg>')


def compose_story_html(s: Story, scenes: list[dict[str, Any]], total: float, audio: list[dict[str, Any]],
                       fmt: str = "landscape") -> str:
    W, H = FORMATS.get(fmt, FORMATS["landscape"])
    tall = H > W
    count = len(scenes)
    body = []
    CW = 912 if tall else 1580                       # content width inside the frame padding
    COL = 912 if tall else 670                       # text column beside the clock
    RCOL = 912 if tall else 730                      # text column beside the receipt
    CARD = 800 if tall else 1312                     # inside the verdict card

    def fs(text: str, face: str, mx: int, box: float, lines: int = 1, mn: int = 44) -> str:
        return f'style="font-size:{F.fit(text, face, mx, box, lines, mn)}px"'
    for i, sc in enumerate(scenes):
        n = sc["name"]
        attrs = (f'id="sc-{n}" class="scene clip" data-start="{sc["start"]}" '
                 f'data-duration="{sc["length"]}" data-track-index="{i % 2}"')
        hud = f'<div class="hud-n">{i + 1:02d} / {count:02d}</div>'
        if n == "title":
            letters = lambda w: "".join(f'<span class="ch">{_e(c)}</span>' for c in w)
            body.append(f"""<section {attrs}>{hud}<div class="scene-content center">
  <div class="brand" id="b-wrap"><div class="bw ink-glow" {fs('HERMES', 'px', 150 if tall else 210, CW)} id="b-1">{letters('HERMES')}</div>
  <div class="bw amber-glow" {fs('WRAPPED', 'px', 150 if tall else 210, CW)} id="b-2">{letters('WRAPPED')}</div></div>
  <div class="sub type" {fs(sc['sub'] + '  ', 'term', 72 if tall else 80, CW)} id="b-sub">{_typed(sc['sub'])}</div></div></section>""")
        elif n == "hook":
            body.append(f"""<section {attrs}>{hud}<div class="scene-content center">
  <div class="mega amber-glow" {fs(sc['big'], 'px', 380 if tall else 360, CW)} id="h-big">{_e(sc['big'])}</div>
  <div class="l1" {fs(sc['line1'], 'term', 96 if tall else 104, CW, 2)} id="h-l1">{_e(sc['line1'])}</div>
  <div class="l2 amber type" id="h-l2">{_typed(sc['line2'])}</div></div></section>""")
        elif n == "scale":
            a, b = sc["a"], sc["b"]
            wb = round(100 * b / a) if a and b else 0
            bars = (f'<div class="race"><div class="lane"><span class="who">HERMES</span><span class="trk"><span class="fill amber" id="r-a" style="width:100%"></span></span><span class="v amber-glow">{_e(_hours(a))}h</span></div>'
                    + (f'<div class="lane"><span class="who">YOU</span><span class="trk"><span class="fill ink" id="r-b" style="width:{wb}%"></span></span><span class="v ink-glow">{_e(_hours(b))}h</span></div>' if b else "")
                    + "</div>")
            body.append(f"""<section {attrs}>{hud}<div class="scene-content center">
  <div class="kicker" id="s-k">&gt; AGENT TIME</div>
  <div class="l1 big" {fs(sc['line1'], 'term', 104 if tall else 116, CW, 2)} id="s-l1">{_e(sc['line1'])}</div>{bars}
  <div class="l2 dim type" id="s-l2">{_typed(sc['line2'])}</div></div></section>""")
        elif n == "crew":
            body.append(f"""<section {attrs}>{hud}<div class="scene-content center">
  {_crew_svg(sc["n"], 860 if tall else 900)}
  <div class="l1" {fs(sc['line1'], 'term', 92 if tall else 100, CW, 2)} id="c-l1">{_e(sc['line1'])}</div>
  <div class="l1 big amber-glow" {fs(sc['line2'], 'term', 112 if tall else 128, CW, 2)} id="c-l2">{_e(sc['line2'])}</div>
  <div class="meta" {fs(sc['line3'], 'term', 68 if tall else 72, CW)} id="c-l3">{_e(sc['line3'])}</div></div></section>""")
        elif n == "clock":
            body.append(f"""<section {attrs}>{hud}<div class="scene-content row2">
  <div class="clockwrap" id="k-clock">{_clock_svg(sc['hours'], sc['peak'], 820 if tall else 820)}</div>
  <div class="col"><div class="kicker" id="k-k">&gt; YOUR PEAK HOUR</div>
  <div class="mega2 amber-glow" {fs(sc['hour'].upper(), 'px', 220 if tall else 230, COL)} id="k-l1">{_e(sc['hour'].upper())}</div>
  <div class="l2 amber type" id="k-l2">{_typed(sc['line2'])}</div></div></div></section>""")
        elif n == "late":
            bubble = (f'<div class="bubble" id="l-q">&gt; {_e(sc["quote"])}</div>' if sc["quote"]
                      else f'<div class="l2 dim type" id="l-q">{_typed(sc["line2"])}</div>')
            clock_t, _, day = sc["when"].upper().partition(", ")
            body.append(f"""<section {attrs}>{hud}<div class="scene-content center">{MOON}
  <div class="kicker" id="l-l1">&gt; {_e(sc['line1'].upper())}</div>
  <div class="mega2 ink-glow" {fs(clock_t, 'px', 230 if tall else 230, CW)} id="l-when">{_e(clock_t)}</div>
  {f'<div class="dayline amber-glow" {fs(day, "px", 96 if tall else 100, CW)} id="l-day">{_e(day)}</div>' if day else ''}{bubble}</div></section>""")
        elif n == "job":
            body.append(f"""<section {attrs}>{hud}<div class="scene-content center">
  <div class="kicker" id="j-l">&gt; BIGGEST JOB</div>
  <div class="jobtitle amber-glow" {fs(sc['title'].upper(), 'px', 140 if tall else 170, CW, 3)} id="j-t">{_e(sc['title'].upper())}</div>
  <div class="l2" {fs(sc['meta'], 'term', 72 if tall else 80, CW, 2)} id="j-m">{_e(sc['meta'])}</div></div></section>""")
        elif n == "tic":
            cell = 420 if tall else 480
            ssize = F.fit(f"[{sc['word'].upper()}]", "px", 56 if tall else 64, cell - 70, 1, 28)
            stamps = "".join(f'<span class="stamp s{k}" style="font-size:{ssize}px">[{_e(sc["word"].upper())}]</span>' for k in range(8))
            body.append(f"""<section {attrs}>{hud}<div class="scene-content center">
  <div class="stamps" id="t-st">{stamps}</div>
  <div class="l1 big" {fs(f"You said “{sc['word']}” {sc['count']} times.", 'term', 104 if tall else 116, CW, 2)} id="t-l1">You said “{_e(sc['word'])}” <span class="amber-glow" id="t-n">{sc['count']}</span>&nbsp;times.</div>
  <div class="l2 dim type" id="t-l2">{_typed(sc['line2'])}</div></div></section>""")
        elif n == "eyes":
            body.append(f"""<section {attrs}>{hud}<div class="scene-content center">
  <div class="l1 big" {fs(sc['line1'], 'term', 104 if tall else 116, CW, 3)} id="e-l1">{_e(sc['line1'])}</div>
  <div class="l2 dim type" id="e-l2">{_typed(sc['line2'])}</div></div></section>""")
        elif n == "receipt":
            rows = "".join(f'<div class="rrow{" tot" if j == 0 else ""}"><span>{_e(k.upper())}</span><span class="dots"></span><span>{_e(v)}</span></div>'
                           for j, (k, v) in enumerate(sc["rows"]))
            body.append(f"""<section {attrs}>{hud}<div class="scene-content row2">
  <div class="receipt" id="p-r"><div class="rhead">RECEIPT · HERMES</div>{rows}<div class="rfoot">THANK YOU FOR YOUR BUSINESS</div></div>
  <div class="col"><div class="note" {fs(sc['note'], 'term', 92 if tall else 100, RCOL, 3)} id="p-n">{_e(sc['note'])}</div></div></div></section>""")
        elif n == "verdict":
            body.append(f"""<section {attrs}>{hud}<div class="burst" id="v-burst"></div><div class="scene-content center">
  <div class="l1 dim" id="v-pre">YOU ARE…</div>
  <div class="card" id="v-card"><i class="cb tl"></i><i class="cb tr"></i><i class="cb bl"></i><i class="cb br"></i><div class="sweep" id="v-shine"></div>
  <div class="arch amber-glow" {fs(sc['name_text'], 'px', 124 if tall else 128, CARD, 3 if tall else 2)} id="v-arch">{_e(sc['name_text'])}</div>
  {f'<div class="side" {fs(sc["side"], 'term', 68 if tall else 72, CARD)} id="v-side">{_e(sc["side"])}</div>' if sc['side'] else ''}
  <div class="vline" {fs(sc['line'], 'term', 76 if tall else 84, CARD, 2, 56)} id="v-line">{_e(sc['line'])}</div></div>
  <div class="meta" {fs(sc['stats'], 'term', 68 if tall else 72, CW)} id="v-stats">{_e(sc['stats'])}</div></div></section>""")

    audio_tags = "".join(
        f'<audio id="{_e(a["id"])}" class="clip" src="{_e(a["src"])}" data-start="{a["start"]}" '
        f'data-track-index="{a["track"]}" data-volume="{a["volume"]}"'
        + (f' data-duration="{a["duration"]}"' if a.get("duration") else "")
        + (f' data-fade-out="{a["fade_out"]}"' if a.get("fade_out") else "")
        + "></audio>"
        for a in audio
    )
    hud_frame = (f'<div class="frame"><i class="cb tl"></i><i class="cb tr"></i><i class="cb bl"></i><i class="cb br"></i>'
                 f'<div class="hud-l">HERMES WRAPPED</div><div class="hud-bl">{_e(s.period_label.upper())}</div>'
                 f'<div class="hud-br"><span class="rec" id="rec"></span>REC</div></div>'
                 '<div class="scan"></div><div class="vig"></div>')
    pad = "190px 84px 170px" if tall else "150px 170px 140px"
    css = f"""
@font-face {{ font-family:'Silkscreen'; src:url('fonts/Silkscreen-Regular.ttf') format('truetype'); font-weight:400; }}
@font-face {{ font-family:'Silkscreen'; src:url('fonts/Silkscreen-Bold.ttf') format('truetype'); font-weight:700; }}
@font-face {{ font-family:'VT323'; src:url('fonts/VT323-Regular.ttf') format('truetype'); font-weight:400; }}
:root {{ --bg:#070604; --ink:#F5EBD6; --dim:#D8CAAC; --faint:#4A4237; --amber:#FFB547; --line:rgba(245,235,214,0.30);
  --px:'Silkscreen', 'VT323', monospace; --term:'VT323', 'Silkscreen', monospace; }}
html, body, #stage {{ width:{W}px; height:{H}px; }}
html, body {{ background:var(--bg); color:var(--ink); font-family:var(--term); }}
#stage {{ position:relative; overflow:hidden; background:radial-gradient(ellipse at 50% 45%, #15110A 0%, var(--bg) 70%); }}
.scene-content {{ padding:{pad}; gap:{'44px' if tall else '40px'}; }}
.center > * {{ flex:none; }}
.card {{ flex:none; }}
.l1, .l2, .note, .vline, .jobtitle, .arch, .bubble {{ text-wrap:balance; }}
.nw {{ white-space:nowrap; }}
.icon {{ filter:drop-shadow(0 0 10px rgba(255,181,71,0.7)); }}
.dayline {{ font-family:var(--px); font-weight:700; line-height:1.0; margin-top:-10px; }}
.race {{ width:100%; }}
.ink-glow {{ color:var(--ink); text-shadow:0 0 6px rgba(245,235,214,0.55), 0 0 26px rgba(245,235,214,0.22); }}
.amber-glow {{ color:var(--amber); text-shadow:0 0 2px rgba(255,246,220,0.6), 0 0 14px rgba(255,181,71,0.8), 0 0 48px rgba(255,140,0,0.45); }}
.amber {{ color:var(--amber); }} .ink {{ color:var(--ink); }} .dim {{ color:var(--dim); }}
.kicker {{ font-family:var(--px); font-weight:700; font-size:{44 if tall else 46}px; letter-spacing:0.04em; color:var(--amber); }}
.l1 {{ font-size:{88 if tall else 96}px; line-height:1.0; }}
.l1.big {{ font-size:{100 if tall else 112}px; }}
.l2 {{ font-size:{68 if tall else 76}px; line-height:1.05; }}
.meta {{ font-family:var(--term); color:var(--dim); white-space:nowrap; line-height:1.1; }}
.clockwrap, .clockwrap svg {{ flex:none; }}
.l1, .l2, .note, .side, .vline, .sub, .bubble {{ font-family:var(--term); }}
.note {{ font-size:{88 if tall else 96}px; line-height:1.0; }}
.mega {{ font-family:var(--px); font-weight:700; font-size:{380 if tall else 360}px; line-height:0.9; white-space:nowrap; }}
.mega2 {{ font-family:var(--px); font-weight:700; font-size:{150 if tall else 170}px; line-height:1.05; }}
.jobtitle {{ font-family:var(--px); font-weight:700; font-size:{140 if tall else 170}px; line-height:1.0; overflow-wrap:normal; }}
.brand {{ display:flex; flex-direction:column; align-items:center; gap:{'18px' if tall else '10px'}; width:100%; }}
.bw {{ font-family:var(--px); font-weight:700; line-height:1.0; white-space:nowrap; }}
.bw .ch {{ display:inline-block; }}
.sub {{ color:var(--dim); white-space:nowrap; line-height:1.1; margin-top:{'40px' if tall else '30px'}; }}
.type .cur {{ display:inline-block; width:0.5em; height:0.85em; margin-left:0.12em; background:currentColor; vertical-align:-0.08em;
  box-shadow:0 0 12px rgba(245,235,214,0.7); background:var(--ink); }}
.race {{ display:flex; flex-direction:column; gap:34px; margin:10px 0; }}
.lane {{ display:flex; align-items:center; gap:30px; font-size:{60 if tall else 64}px; }}
.who {{ font-family:var(--px); font-weight:700; font-size:{40 if tall else 44}px; width:{190 if tall else 230}px; color:var(--dim); }}
.v {{ font-family:var(--px); font-weight:700; width:{190 if tall else 230}px; text-align:right; font-size:{52 if tall else 60}px; }}
.trk {{ flex:1; height:{84 if tall else 80}px; border:3px solid var(--line); padding:8px; }}
.fill {{ display:block; height:100%; transform-origin:left center; }}
.fill.amber {{ background:repeating-linear-gradient(90deg, var(--amber) 0 26px, transparent 26px 34px); filter:drop-shadow(0 0 10px rgba(255,181,71,0.6)); }}
.fill.ink {{ background:repeating-linear-gradient(90deg, var(--ink) 0 26px, transparent 26px 34px); filter:drop-shadow(0 0 8px rgba(245,235,214,0.4)); }}
.row2 {{ flex-direction:{'column' if tall else 'row'}; align-items:center; gap:{'50px' if tall else '90px'}; {'text-align:center;' if tall else ''} }}
.col {{ flex:1; min-width:0; display:flex; flex-direction:column; gap:34px; {'flex:0 0 auto; width:100%; align-items:center;' if tall else ''} }}
.bubble {{ max-width:{900 if tall else 1400}px; border:3px solid var(--line); background:rgba(245,235,214,0.04); font-size:{68 if tall else 72}px;
  line-height:1.1; padding:34px 48px; text-align:left; }}
.stamps {{ position:relative; width:{900 if tall else 1500}px; height:{460 if tall else 260}px; }}
.stamp {{ position:absolute; left:50%; top:50%; font-family:var(--px); font-weight:700; color:var(--amber);
  border:4px solid var(--amber); padding:6px 22px; white-space:nowrap; box-shadow:0 0 18px rgba(255,181,71,0.35), inset 0 0 14px rgba(255,181,71,0.2);
  text-shadow:0 0 10px rgba(255,181,71,0.6); background:var(--bg); }}
.receipt {{ width:{900 if tall else 760}px; border:4px double var(--ink); background:rgba(245,235,214,0.03); font-size:{58 if tall else 56}px;
  padding:40px 48px; transform-origin:top center; text-align:left; box-shadow:0 0 30px rgba(245,235,214,0.08); }}
.rhead, .rfoot {{ text-align:center; font-family:var(--px); font-weight:700; font-size:{44 if tall else 40}px; color:var(--dim); white-space:nowrap; }}
.rhead {{ padding-bottom:24px; border-bottom:3px dashed var(--line); margin-bottom:20px; }}
.rfoot {{ padding-top:24px; border-top:3px dashed var(--line); margin-top:20px; font-family:var(--term); font-weight:400; font-size:{52 if tall else 50}px; }}
.rrow {{ display:flex; gap:18px; padding:8px 0; align-items:baseline; white-space:nowrap; }}
.rrow .dots {{ flex:1; border-bottom:3px dotted var(--faint); transform:translateY(-10px); }}
.rrow.tot span:last-child {{ color:var(--amber); text-shadow:0 0 10px rgba(255,181,71,0.6); }}
.burst {{ position:absolute; left:50%; top:48%; width:1800px; height:1800px; margin:-900px 0 0 -900px; border-radius:50%;
  background:radial-gradient(circle, rgba(255,181,71,0.22), rgba(255,140,0,0.06) 40%, transparent 68%); }}
.card {{ position:relative; overflow:hidden; border:4px solid var(--amber); background:rgba(255,181,71,0.06);
  box-shadow:0 0 40px rgba(255,181,71,0.35), inset 0 0 60px rgba(255,181,71,0.12);
  padding:{'64px 56px' if tall else '60px 90px'}; display:flex; flex-direction:column; gap:24px; align-items:center; width:{920 if tall else 1500}px; }}
.card .cb {{ border-color:var(--ink); width:44px; height:44px; }}
.arch {{ font-family:var(--px); font-weight:700; font-size:{120 if tall else 140}px; line-height:1.0; }}
.side {{ color:var(--amber); white-space:nowrap; line-height:1.1; }}
.vline {{ font-size:{68 if tall else 72}px; line-height:1.05; color:var(--ink); }}
.sweep {{ position:absolute; left:0; right:0; top:0; height:30%; background:linear-gradient(180deg, transparent, rgba(255,214,150,0.22), transparent); }}
#v-pre {{ font-size:{80 if tall else 84}px; }}
.frame::after {{ content:''; position:absolute; inset:26px; border:1px solid rgba(245,235,214,0.14); }}
.frame {{ position:absolute; inset:{'44px' if tall else '40px'}; pointer-events:none; z-index:50; }}
.cb {{ position:absolute; width:{56 if tall else 60}px; height:{56 if tall else 60}px; border:0 solid rgba(255,181,71,0.85); }}
.cb.tl {{ left:0; top:0; border-left-width:5px; border-top-width:5px; }}
.cb.tr {{ right:0; top:0; border-right-width:5px; border-top-width:5px; }}
.cb.bl {{ left:0; bottom:0; border-left-width:5px; border-bottom-width:5px; }}
.cb.br {{ right:0; bottom:0; border-right-width:5px; border-bottom-width:5px; }}
.hud-l, .hud-bl, .hud-br, .hud-n {{ position:absolute; font-family:var(--term); font-size:44px; color:var(--dim); opacity:0.8; letter-spacing:0.06em; white-space:nowrap; }}
.hud-l {{ left:86px; top:8px; }} .hud-bl {{ left:86px; bottom:6px; }} .hud-br {{ right:86px; bottom:6px; }}
.hud-n {{ right:{44 + 84 if tall else 40 + 84}px; top:{44 + 8 if tall else 40 + 8}px; z-index:51; }}
.rec {{ display:inline-block; width:22px; height:22px; border-radius:50%; background:var(--amber); box-shadow:0 0 12px var(--amber); margin-right:14px; vertical-align:2px; }}
.scan {{ position:absolute; inset:0; z-index:60; pointer-events:none;
  background:repeating-linear-gradient(0deg, rgba(0,0,0,0.22) 0 2px, transparent 2px 4px); }}
.vig {{ position:absolute; inset:0; z-index:61; pointer-events:none; background:radial-gradient(ellipse at center, transparent 58%, rgba(0,0,0,0.55) 100%); }}
"""
    js = """
(function () {
  window.__timelines = window.__timelines || {};
  var D = JSON.parse(document.getElementById('reel-data').textContent);
  var tl = gsap.timeline({ paused: true });
  function at(n) { for (var i = 0; i < D.scenes.length; i++) if (D.scenes[i].name === n) return D.scenes[i]; return null; }
  function sceneIn(n) { var s = at(n); if (!s) return null;
    tl.fromTo('#sc-' + n, { opacity: 0 }, { opacity: 1, duration: 0.18, ease: 'steps(3)' }, s.start);
    tl.to('#sc-' + n, { opacity: 0, duration: 0.2, ease: 'power1.in' }, s.start + s.length - 0.2);
    return s; }
  function up(sel, t, d) { if (document.querySelector(sel)) tl.from(sel, { y: 34, opacity: 0, duration: d || 0.5, ease: 'expo.out' }, t); }
  function type(sel, t) {
    var el = document.querySelector(sel); if (!el) return;
    var cs = el.querySelectorAll('.tc'), cur = el.querySelector('.cur');
    if (cs.length) tl.from(cs, { opacity: 0, duration: 0.001, stagger: Math.min(0.035, 1.1 / cs.length) }, t);
    if (cur) { tl.from(cur, { opacity: 0, duration: 0.001 }, t);
      tl.to(cur, { opacity: 0, duration: 0.001, repeat: 9, yoyo: true, repeatDelay: 0.38 }, t + Math.min(1.2, cs.length * 0.035) + 0.2); }
  }
  tl.to('#rec', { opacity: 0.15, duration: 0.001, repeat: Math.floor(D.total / 0.6), yoyo: true, repeatDelay: 0.6 }, 0);
  var s;
  if ((s = sceneIn('title'))) {
    tl.fromTo('#b-wrap', { scaleX: 0.02, scaleY: 0.02 }, { scaleX: 1, duration: 0.22, ease: 'power2.out' }, s.start + 0.05);
    tl.to('#b-wrap', { scaleY: 1, duration: 0.28, ease: 'power3.out' }, s.start + 0.27);
    tl.from('#b-wrap .ch', { opacity: 0, duration: 0.001, stagger: { each: 0.035, from: 'random' } }, s.start + 0.3);
    type('#b-sub', s.start + 0.9);
    tl.to('#b-wrap', { scale: 1.04, duration: 2.0, ease: 'none' }, s.start + 0.6);
  }
  if ((s = sceneIn('hook'))) {
    var big = document.getElementById('h-big'), target = parseInt(big.textContent, 10);
    if (!isNaN(target) && target > 1) {
      var o = { v: 0 }, pad = big.textContent.length;
      tl.to(o, { v: target, duration: 0.9, ease: 'power2.out', onUpdate: function () {
        var t = String(Math.round(o.v)); while (t.length < pad) t = '0' + t; big.textContent = t; } }, s.start + 0.1);
    }
    tl.from('#h-big', { opacity: 0, scaleY: 0.1, duration: 0.35, ease: 'power3.out' }, s.start + 0.05);
    up('#h-l1', s.start + 0.7);
    type('#h-l2', s.start + 1.4);
  }
  if ((s = sceneIn('scale'))) {
    up('#s-k', s.start + 0.05); up('#s-l1', s.start + 0.15);
    tl.from('#r-a', { scaleX: 0, duration: 1.3, ease: 'steps(14)' }, s.start + 0.5);
    if (document.getElementById('r-b')) tl.from('#r-b', { scaleX: 0, duration: 1.3, ease: 'steps(14)' }, s.start + 0.5);
    tl.from('#sc-scale .lane', { opacity: 0, duration: 0.3, stagger: 0.1 }, s.start + 0.4);
    type('#s-l2', s.start + 2.0);
  }
  if ((s = sceneIn('crew'))) {
    tl.from('#c-hub', { opacity: 0, duration: 0.001 }, s.start + 0.2);
    tl.from('#c-net .cl', { opacity: 0, duration: 0.001, stagger: 0.12 }, s.start + 0.4);
    tl.from('#c-net .cw', { opacity: 0, duration: 0.001, stagger: 0.12 }, s.start + 0.46);
    up('#c-l1', s.start + 0.9);
    tl.from('#c-l2', { opacity: 0, duration: 0.001 }, s.start + 1.6);
    tl.from('#c-l2', { scaleY: 0.1, duration: 0.25, ease: 'power3.out' }, s.start + 1.6);
    up('#c-l3', s.start + 2.3);
  }
  if ((s = sceneIn('clock'))) {
    tl.from('#k-clock .hbar', { opacity: 0, duration: 0.001, stagger: 0.04 }, s.start + 0.2);
    up('#k-k', s.start + 0.6);
    tl.from('#k-l1', { opacity: 0, scaleY: 0.1, duration: 0.3, ease: 'power3.out' }, s.start + 0.8);
    type('#k-l2', s.start + 1.8);
  }
  if ((s = sceneIn('late'))) {
    tl.from('#l-moon', { opacity: 0, duration: 0.001 }, s.start + 0.05);
    up('#l-l1', s.start + 0.1);
    up('#l-day', s.start + 0.8);
    tl.from('#l-when', { opacity: 0, scaleY: 0.1, duration: 0.35, ease: 'power3.out' }, s.start + 0.35);
    if (document.querySelector('#l-q .tc')) type('#l-q', s.start + 1.3); else up('#l-q', s.start + 1.3);
  }
  if ((s = sceneIn('job'))) {
    up('#j-l', s.start + 0.1);
    tl.from('#j-t', { opacity: 0, x: -60, duration: 0.5, ease: 'steps(5)' }, s.start + 0.3);
    up('#j-m', s.start + 1.2);
  }
  if ((s = sceneIn('tic'))) {
    var st = document.querySelectorAll('#t-st .stamp');
    for (var k = 0; k < st.length; k++) {
      var c = D.cells[k];
      tl.fromTo(st[k], { xPercent: -50, yPercent: -50, x: c[0], y: c[1], rotation: c[2], opacity: 0 },
        { opacity: 1, duration: 0.001 }, s.start + 0.15 + k * 0.14);
    }
    up('#t-l1', s.start + 1.2);
    var tn = document.getElementById('t-n'), tv = parseInt(tn.textContent, 10), to = { v: 1 };
    if (tv > 1) tl.to(to, { v: tv, duration: 0.8, ease: 'power2.out', onUpdate: function () { tn.textContent = Math.round(to.v); } }, s.start + 1.25);
    type('#t-l2', s.start + 2.0);
  }
  if ((s = sceneIn('eyes'))) { up('#e-l1', s.start + 0.2); type('#e-l2', s.start + 1.2); }
  if ((s = sceneIn('receipt'))) {
    tl.from('#p-r', { scaleY: 0, duration: 0.8, ease: 'steps(10)' }, s.start + 0.15);
    tl.from('#p-r .rrow', { opacity: 0, duration: 0.001, stagger: 0.22 }, s.start + 0.8);
    up('#p-n', s.start + 1.8);
  }
  if ((s = sceneIn('verdict'))) {
    up('#v-pre', s.start + 0.1);
    tl.from('#v-burst', { scale: 0, opacity: 0, duration: 1.2, ease: 'expo.out' }, s.start + 0.9);
    tl.fromTo('#v-card', { scaleX: 1, scaleY: 0.02, opacity: 0 }, { scaleY: 0.02, opacity: 1, duration: 0.12, ease: 'none' }, s.start + 0.8);
    tl.to('#v-card', { scaleY: 1, duration: 0.45, ease: 'expo.out' }, s.start + 0.92);
    tl.from('#v-arch', { opacity: 0, duration: 0.001 }, s.start + 1.25);
    tl.from('#v-arch', { scaleY: 0.2, duration: 0.25, ease: 'power3.out' }, s.start + 1.25);
    tl.fromTo('#v-shine', { yPercent: -120 }, { yPercent: 380, duration: 1.4, ease: 'none' }, s.start + 1.4);
    up('#v-side', s.start + 1.6); up('#v-line', s.start + 1.9);
    up('#v-stats', s.start + 2.4);
  }
  window.__timelines['root'] = tl;
})();
"""
    data = {"scenes": [{k: v for k, v in sc.items() if k in ("name", "start", "length")} for sc in scenes],
            "total": total,
            "cells": ([[-170, -150, -9], [150, -170, 7], [-120, -40, 5], [170, -20, -6], [-180, 90, -4], [120, 110, 10], [-30, 180, -8], [10, 20, 3]] if tall
                      else [[-470, -50, -9], [-160, -80, 7], [170, -60, -5], [470, -40, 8], [-330, 60, 4], [10, 70, -7], [330, 70, 6], [0, -10, -2]])}
    return f"""<!doctype html>
<html lang="en"><head><meta charset="UTF-8" /><meta name="viewport" content="width={W}, height={H}" />
<link rel="preload" href="fonts/Silkscreen-Bold.ttf" as="font" crossorigin><link rel="preload" href="fonts/VT323-Regular.ttf" as="font" crossorigin>
<script src="gsap.min.js"></script><style>{base_css()}{css}</style></head>
<body><div id="stage" data-composition-id="root" data-start="0" data-duration="{total}" data-width="{W}" data-height="{H}">
{''.join(body)}{hud_frame}{audio_tags}
</div>
<script id="reel-data" type="application/json">{json.dumps(data)}</script>
<script>{js}</script></body></html>
"""


def render_story(s: Story, out_dir: str | os.PathLike[str], *, quality: str = "standard", show_cost: bool = True,
                 fmt: str = "landscape", music: bool = True, show_titles: bool = True) -> Path:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    scenes = plan_scenes(s, show_cost=show_cost, show_titles=show_titles)
    total = _timed(scenes)
    audio: list[dict[str, Any]] = []
    if music:
        mood = mood_for(s.archetype, s.peak_hour, s.night_share)
        make_music(out / "music.wav", total, seed=f"{s.start:.0f}{s.archetype}", mood=mood)
        audio.append({"id": "music", "src": "music.wav", "start": 0, "duration": total, "track": 5,
                      "volume": 0.9, "fade_out": 1.5})
    page = compose_story_html(s, scenes, total, audio, fmt=fmt)
    return render_html(page, out, data={"story": s.to_dict(), "scenes": scenes, "format": fmt,
                                               "mood": mood_for(s.archetype, s.peak_hour, s.night_share) if music else None}, quality=quality)
