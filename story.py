"""The story behind your week: moments, an archetype and a verdict, computed from Hermes's records.

Everything here is deterministic. Same week in, same story out. No model calls.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import time
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone, tzinfo
from typing import Any

from . import timeline as T
from .redact import redact, short

IDLE = 300.0
SYSTEM_ASK = re.compile(r"^\s*(\[(ASYNC|CONTEXT|IMPORTANT|SYSTEM|CRON|SCHEDULED)|@file:|<)", re.I)
OOB = "[OUT-OF-BAND USER MESSAGE"
# Verbal tics we may count. A fixed list: free-text top words would leak project and client names.
TICS: list[tuple[str, str, str]] = [
    ("right?", r"\bright\?", "Hermes said yes. Mostly."),
    ("please", r"\bplease\b", "Politest operator on the network."),
    ("thanks", r"\bthanks?\b|\bthank you\b", "Hermes felt appreciated."),
    ("why", r"\bwhy\b", "Curiosity: maxed."),
    ("again", r"\bagain\b", "Persistence is a feature."),
    ("go ahead", r"\bgo ahead\b", "Green lights all week."),
    ("quick question", r"\bquick question\b", "None of them were quick."),
    ("wait", r"^\s*wait\b", "Plot twists, delivered."),
]
FIX_TITLE = re.compile(r"\b(fix\w*|debug\w*|errors?|bug\w*|broken|root cause|crash\w*)\b", re.I)
REDO_TITLE = re.compile(r"\b(redesign\w*|v[2-9]\b|again|redo|rewrite|resume)\b", re.I)
PHASE = re.compile(r"\bphase\s+(\d+)", re.I)
DOMAIN = re.compile(r"\b[\w-]+(?:\.[\w-]+)*\.[a-z]{2,}\b", re.I)  # any domain (and file.ext)


def _config_timezone() -> str:
    """Top-level `timezone:` from the active Hermes config.yaml (read as text; no private imports)."""
    home = os.environ.get("HERMES_HOME") or os.path.expanduser("~/.hermes")
    try:
        with open(os.path.join(home, "config.yaml"), encoding="utf-8") as f:
            for line in f:
                m = re.match(r"^timezone:\s*['\"]?([A-Za-z_]+(?:/[A-Za-z0-9_+\-]+)*)['\"]?\s*(#.*)?$", line.rstrip())
                if m:
                    return m.group(1)
    except OSError:
        pass
    return ""


def get_tz(override: str = "") -> tuple[tzinfo, bool]:
    """(timezone, guessed). Order: the plugin's `timezone` setting, Hermes's own setting
    (HERMES_TIMEZONE / config `timezone`), then the server clock. `guessed` is True when nothing was
    configured and the server runs on UTC, which is usually not where the user lives."""
    if override:
        try:
            from zoneinfo import ZoneInfo

            return ZoneInfo(override), False
        except Exception:
            pass
    name = os.environ.get("HERMES_TIMEZONE", "").strip() or _config_timezone()
    if name:
        try:
            from zoneinfo import ZoneInfo

            return ZoneInfo(name), False
        except Exception:
            pass
    local = datetime.now().astimezone()
    return (local.tzinfo or timezone.utc), (local.utcoffset() or timedelta(0)) == timedelta(0)


def screen_title(raw: str | None, limit: int = 40) -> str:
    """A session title that is safe to post publicly: no paths, domains or secrets."""
    t = (raw or "").strip()
    t = re.sub(r"^subagent:\s*", "", t, flags=re.I)
    t = redact(t)
    words = [w for w in t.split() if "/" not in w and "~" not in w and not DOMAIN.search(w) and "•" not in w]
    t = " ".join(words).strip(" -:·,.\"'")
    return short(t, limit) if t else ""


def _active(stamps: list[float]) -> float:
    return sum(min(b - a, IDLE) for a, b in zip(stamps, stamps[1:]) if b > a)


def _hour_label(h: int) -> str:
    return f"{h % 12 or 12}{'am' if h < 12 else 'pm'}"


def _clock(dt: datetime) -> str:
    return f"{dt.hour % 12 or 12}:{dt.minute:02d}{'am' if dt.hour < 12 else 'pm'}"


@dataclass
class Story:
    start: float
    end: float
    tz: str
    tz_guessed: bool = False
    sessions: int = 0
    delegated: int = 0
    asks: int = 0
    cost: float = 0.0
    lifetime_cost: float = 0.0
    agent_hours: float = 0.0
    wall_hours: float = 0.0
    gap_days: int = 0                  # days of silence before this period (0 = none worth mentioning)
    first_week: bool = False           # nothing on record before this period
    peak_agents: int = 0
    peak_when: str = ""
    ask_hours: list[int] = field(default_factory=lambda: [0] * 24)
    peak_hour: int = 0
    night_share: float = 0.0           # share of asks 11pm–4am
    late_asks: int = 0                 # asks between midnight and 4am
    latest_ask: str = ""               # e.g. "1:58am, Thursday"
    latest_ask_text: str = ""          # only filled when quotes are enabled and the text is safe
    busiest_day: str = ""
    busiest_day_hours: float = 0.0
    active_days: int = 0
    big_job: str = ""
    big_job_minutes: int = 0
    big_job_cost: float = 0.0
    big_job_helpers: int = 0
    tic: str = ""
    tic_count: int = 0
    tic_line: str = ""
    vision_checks: int = 0
    browser_actions: int = 0
    edits: int = 0
    max_phase: int = 0
    redo_sessions: int = 0
    fix_share: float = 0.0
    upkeep_pct: int = 0
    archetype: str = ""
    archetype_line: str = ""
    side_trait: str = ""
    period_label: str = ""
    period_word: str = "week"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


ARCHETYPE_LINES = {
    "The Night Shift Manager": "You clocked out. Your team didn't.",
    "The 2am Debugger": "Bugs fear the dark now.",
    "The Delegator": "Why do it yourself when you can staff it?",
    "The Config Tinkerer": "The agent was the project.",
    "The Marathoner": "One more thing. Then one more.",
    "The Perfectionist": "Good was never the goal.",
    "The Big Spender": "Expensive taste, big output.",
    "The Sprinter": "Quiet week, loud days.",
    "The Steady Hand": "Every day, a little further.",
}


def _archetypes(s: Story) -> list[str]:
    """Every archetype the week qualifies for, strongest first."""
    out = []
    deleg = s.delegated / s.sessions if s.sessions else 0
    if s.night_share >= 0.25 and deleg >= 0.5:
        out.append("The Night Shift Manager")
    if s.late_asks >= 5 and s.fix_share >= 0.3:
        out.append("The 2am Debugger")
    if deleg >= 0.5 or s.peak_agents >= 4:
        out.append("The Delegator")
    if s.upkeep_pct >= 30:
        out.append("The Config Tinkerer")
    if s.big_job_minutes >= 120 or s.busiest_day_hours >= 6:
        out.append("The Marathoner")
    if s.redo_sessions >= 3:
        out.append("The Perfectionist")
    if s.active_days and s.active_days <= 2 and s.sessions >= 5:
        out.append("The Sprinter")
    if not out:
        out.append("The Steady Hand")
    return out


def _safe_quote(text: str) -> str:
    """A latest-ask quote only if it is short, clean and names nobody. Otherwise nothing."""
    t = " ".join(text.split())
    if not t or len(t) > 70 or re.search(r"\bf+u+c*k|\bshit|\bwtf\b|\bdamn", t, re.I) or re.search(r"\d{4,}", t):
        return ""
    if redact(t) != t or DOMAIN.search(t) or "/" in t or "@" in t:
        return ""
    # Capitalised words after the first could be names or companies.
    if any(w[:1].isupper() and w.lower() not in {"i", "i'm", "ok", "okay"} for w in t.split()[1:]):
        return ""
    return t


def period_word(days: int) -> str:
    return {1: "day", 7: "week"}.get(days, "month" if 28 <= days <= 31 else "year" if 360 <= days <= 366 else f"{days} days")


def load_story(days: int = 7, db_path: str | None = None, now: float | None = None,
               upkeep_pct: int = 0, quotes: bool = False, tz_name: str = "") -> Story:
    end = now or time.time()
    start = end - days * 86400
    tz, guessed = get_tz(tz_name)
    s = Story(start=start, end=end, tz=str(tz), tz_guessed=guessed, upkeep_pct=upkeep_pct)
    s.period_word = period_word(days)
    d0, d1 = datetime.fromtimestamp(start, tz), datetime.fromtimestamp(end, tz)
    s.period_label = (f"week of {d0:%b} {d0.day}" if days == 7 else
                      f"{d0:%b %Y} – {d1:%b %Y}" if days >= 360 else
                      f"{d0:%b} {d0.day} – {d1:%b} {d1.day}")

    con = T._connect(db_path or T.default_db_path())
    try:
        cols = {r[1] for r in con.execute("PRAGMA table_info(sessions)")}
        parent_col = "parent_session_id" if "parent_session_id" in cols else "NULL"
        cost_col = ("COALESCE(actual_cost_usd, estimated_cost_usd, 0)" if "actual_cost_usd" in cols
                    else "COALESCE(estimated_cost_usd, 0)" if "estimated_cost_usd" in cols else "0")
        visible = " AND COALESCE(archived, 0) = 0" if "archived" in cols else ""
        visible += " AND COALESCE(hidden, 0) = 0" if "hidden" in cols else ""
        mcols = {r[1] for r in con.execute("PRAGMA table_info(messages)")}
        active_only = " AND active = 1" if "active" in mcols else ""
        rows = con.execute(
            f"SELECT id, title, source, {parent_col} AS parent, started_at, {cost_col} AS cost FROM sessions "
            f"WHERE started_at >= ? AND started_at < ?{visible}",
            (start, end),
        ).fetchall()
        sess: dict[str, dict[str, Any]] = {r["id"]: dict(r) for r in rows}
        life = con.execute(f"SELECT SUM({cost_col}) FROM sessions WHERE 1=1{visible}").fetchone()[0]
        prev = con.execute(f"SELECT MAX(started_at) FROM sessions WHERE started_at < ?{visible}", (start,)).fetchone()[0]
        msgs: list[sqlite3.Row] = []
        ids = list(sess)
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            msgs += con.execute(
                "SELECT session_id, role, timestamp, tool_calls, "
                "CASE WHEN role = 'user' THEN content ELSE NULL END AS content FROM messages "
                f"WHERE session_id IN ({','.join('?' * len(chunk))}){active_only} ORDER BY timestamp",
                chunk,
            ).fetchall()
    finally:
        con.close()

    with_msgs = {m["session_id"] for m in msgs}
    sess = {k: v for k, v in sess.items() if k in with_msgs}
    if not sess:
        s.first_week = prev is None
        s.archetype = "The Steady Hand"
        s.archetype_line = "Nothing yet. Next week is yours."
        return s

    def is_sub(sid: str) -> bool:
        # Helpers spawned by delegate_task. (A parent id alone isn't enough: sessions continued
        # after context compression also have one.)
        r = sess[sid]
        return (r["title"] or "").lower().startswith("subagent:") or (r["source"] or "") == "subagent"

    s.sessions = len(sess)
    s.delegated = sum(1 for sid in sess if is_sub(sid))
    s.cost = sum(r["cost"] or 0 for r in sess.values())
    s.lifetime_cost = float(life or 0)
    if prev is None:
        s.first_week = True
    else:
        gap = (min(r["started_at"] for r in sess.values()) - prev) / 86400
        s.gap_days = int(gap) if gap >= 3 else 0

    # Time: per-session active (agent-hours, counts parallel helpers) vs merged wall clock.
    by_sess: dict[str, list[float]] = defaultdict(list)
    for m in msgs:
        by_sess[m["session_id"]].append(float(m["timestamp"]))
    per_sess = {sid: _active(v) for sid, v in by_sess.items()}
    s.agent_hours = round(sum(per_sess.values()) / 3600, 1)
    all_ts = sorted(float(m["timestamp"]) for m in msgs)
    s.wall_hours = round(_active(all_ts) / 3600, 1)

    per_day: dict[str, list[float]] = defaultdict(list)
    for t in all_ts:
        per_day[datetime.fromtimestamp(t, tz).strftime("%Y-%m-%d")].append(t)
    day_hours = {d: _active(v) / 3600 for d, v in per_day.items()}
    s.active_days = sum(1 for h in day_hours.values() if h >= 0.25)
    if day_hours:
        d, h = max(day_hours.items(), key=lambda x: x[1])
        s.busiest_day = datetime.strptime(d, "%Y-%m-%d").strftime("%A")
        s.busiest_day_hours = round(h, 1)

    # Peak concurrency: overlap of *active stretches* (a session idle for 5+ minutes isn't running).
    events = []
    for v in by_sess.values():
        seg_start = v[0]
        for a, b in zip(v, v[1:]):
            if b - a > IDLE:
                events += [(seg_start, 1), (a, -1)]
                seg_start = b
        events += [(seg_start, 1), (v[-1], -1)]
    cur = 0
    for t, d in sorted(events, key=lambda e: (e[0], -e[1])):
        cur += d
        if cur > s.peak_agents:
            s.peak_agents = cur
            dt = datetime.fromtimestamp(t, tz)
            s.peak_when = f"{dt:%a}, {_clock(dt)}"

    # Human asks: top-level sessions only, without injected harness text, deduped.
    asks: list[tuple[float, str]] = []
    seen: set[str] = set()
    for m in msgs:
        if m["role"] != "user" or is_sub(m["session_id"]) or not m["content"]:
            continue
        text = str(m["content"])
        if text.startswith(OOB):
            text = text.split("\n", 1)[-1]
        elif SYSTEM_ASK.match(text):
            continue
        key = text.strip()[:200]
        if key in seen:
            continue
        seen.add(key)
        asks.append((float(m["timestamp"]), text))
    s.asks = len(asks)
    for t, _ in asks:
        s.ask_hours[datetime.fromtimestamp(t, tz).hour] += 1
    if asks:
        s.peak_hour = max(range(24), key=lambda h: (s.ask_hours[h], h))
        night = sum(s.ask_hours[h] for h in (23, 0, 1, 2, 3))
        s.night_share = round(night / len(asks), 2)
        s.late_asks = sum(s.ask_hours[h] for h in (0, 1, 2, 3))
        # "Latest" = furthest past the evening, with the day rolling over at 5am.
        t, text = max(asks, key=lambda a: ((datetime.fromtimestamp(a[0], tz).hour * 60
                                             + datetime.fromtimestamp(a[0], tz).minute) - 300) % 1440)
        dt = datetime.fromtimestamp(t, tz)
        if dt.hour < 5 or dt.hour >= 23:
            s.latest_ask = f"{_clock(dt)}, {dt:%A}"
            if quotes:
                s.latest_ask_text = _safe_quote(text)
        lowered = [a[1].lower() for a in asks]
        best = None
        for word, pat, line in TICS:
            n = sum(1 for a in lowered if re.search(pat, a, re.M))
            if n >= 5 and n / len(asks) >= 0.12 and (best is None or n > best[1]):
                best = (word, n, line)
        if best:
            s.tic, s.tic_count, s.tic_line = best

    # Biggest job: a whole family of sessions counts as one job: the session you started, the
    # sessions it was compressed into, and every helper any of them spawned.
    def root(sid: str) -> str:
        seen_ids = set()
        while sess[sid]["parent"] in sess and sid not in seen_ids:
            seen_ids.add(sid)
            sid = sess[sid]["parent"]
        return sid

    families: dict[str, list[str]] = defaultdict(list)
    for sid in sess:
        families[root(sid)].append(sid)

    def family_name(members: list[str]) -> str:
        own = sorted((m for m in members if not is_sub(m)), key=lambda m: sess[m]["started_at"], reverse=True)
        for m in own + sorted(members, key=lambda m: -per_sess.get(m, 0)):
            name = screen_title(sess[m]["title"])
            if name:
                return name
        return ""

    best = None
    for members in families.values():
        secs = sum(per_sess.get(m, 0) for m in members)
        if family_name(members) and (best is None or secs > best[0]):
            best = (secs, members)
    if best:
        secs, members = best
        s.big_job = family_name(members)
        s.big_job_minutes = round(secs / 60)
        s.big_job_cost = round(sum(float(sess[m]["cost"] or 0) for m in members), 2)
        s.big_job_helpers = sum(1 for m in members if is_sub(m))

    titles = [r["title"] or "" for r in sess.values()]
    phases = [int(n) for t in titles for n in PHASE.findall(t)]
    s.max_phase = max(phases) if phases else 0
    s.redo_sessions = sum(1 for t in titles if REDO_TITLE.search(t))
    s.fix_share = round(sum(1 for t in titles if FIX_TITLE.search(t)) / len(titles), 2)

    for m in msgs:
        if m["role"] != "assistant" or not m["tool_calls"]:
            continue
        raw = m["tool_calls"]
        try:
            calls = json.loads(raw) if isinstance(raw, str) else raw
        except ValueError:
            continue
        for c in calls if isinstance(calls, list) else []:
            if not isinstance(c, dict):
                continue
            name = (c.get("function") or {}).get("name") or c.get("name") or ""
            if name == "vision_analyze":
                s.vision_checks += 1
            elif name.startswith("browser_"):
                s.browser_actions += 1
            elif name in ("write_file", "patch"):
                s.edits += 1

    kinds = _archetypes(s)
    s.archetype = kinds[0]
    s.archetype_line = ARCHETYPE_LINES[s.archetype]
    s.side_trait = next((k.replace("The ", "") for k in kinds[1:]), "")
    return s
