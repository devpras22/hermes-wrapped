"""Turn one Hermes session into a plain, render-ready timeline.

No model calls: everything comes from what Hermes already recorded (the session row,
the messages and their tool calls) plus anything the live hooks captured.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
from dataclasses import asdict, dataclass, field
from typing import Any

from .redact import redact, short

# Tool name -> (category label shown in the video, short glyph)
CATEGORIES: dict[str, tuple[str, str]] = {
    "terminal": ("TERMINAL", ">_"),
    "execute_code": ("CODE", "{ }"),
    "process": ("PROCESS", ">_"),
    "write_file": ("EDIT", "+"),
    "patch": ("EDIT", "±"),
    "read_file": ("READ", "≡"),
    "search_files": ("SEARCH", "⌕"),
    "web_search": ("WEB", "◎"),
    "web_extract": ("WEB", "◎"),
    "browser_navigate": ("BROWSER", "▢"),
    "browser_click": ("BROWSER", "▢"),
    "browser_type": ("BROWSER", "▢"),
    "browser_snapshot": ("BROWSER", "▢"),
    "vision_analyze": ("VISION", "◉"),
    "image_generate": ("IMAGE", "◆"),
    "memory": ("MEMORY", "✦"),
    "session_search": ("MEMORY", "✦"),
    "skill_view": ("SKILL", "◇"),
    "skills_list": ("SKILL", "◇"),
    "skill_manage": ("SKILL", "◇"),
    "delegate_task": ("DELEGATE", "⇉"),
    "todo": ("PLAN", "☐"),
    "send_message": ("MESSAGE", "✉"),
    "clarify": ("ASK", "?"),
    "cronjob": ("CRON", "◷"),
}

WRITE_TOOLS = {"write_file", "patch"}
IDLE_GAP = 300.0  # seconds; longer pauses count as the user being away


def _plain(text: str) -> str:
    """Drop markdown emphasis/code marks so text reads cleanly on screen."""
    text = re.sub(r"`{1,3}", "", text or "")
    text = re.sub(r"\*\*|__|(?<!\w)[*_](?!\s)|(?<!\s)[*_](?!\w)", "", text)
    text = re.sub(r"^#+\s*", "", text, flags=re.M)
    return text


@dataclass
class Step:
    index: int
    tool: str
    category: str
    glyph: str
    label: str
    started_at: float
    seconds: float | None
    error: bool = False
    file: str | None = None
    call_id: str | None = None


@dataclass
class Timeline:
    session_id: str
    title: str
    source: str
    model: str
    started_at: float
    ended_at: float | None
    ask: str
    answer: str
    steps: list[Step] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float | None = None
    files_touched: list[str] = field(default_factory=list)
    active_seconds: float = 0.0
    cwd: str = ""

    @property
    def duration_seconds(self) -> float:
        """Time the agent was actually working (idle gaps over 5 minutes are not counted)."""
        return self.active_seconds

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["duration_seconds"] = self.duration_seconds
        d["error_count"] = sum(1 for s in self.steps if s.error)
        return d


def _category(tool: str) -> tuple[str, str]:
    if tool in CATEGORIES:
        return CATEGORIES[tool]
    if tool.startswith("browser_"):
        return ("BROWSER", "▢")
    if tool.startswith("mcp_") or "__" in tool:
        return ("TOOL", "◈")
    return (tool.upper().replace("_", " ")[:12], "◈")


def _label(tool: str, args: dict[str, Any]) -> tuple[str, str | None]:
    """Short human label for a tool call, plus the file it touched (if any)."""
    path = args.get("path") or args.get("file_path") or args.get("file")
    if tool in ("terminal", "process"):
        return short(redact(str(args.get("command", ""))), 64), None
    if tool == "execute_code":
        code = str(args.get("code", "")).strip().splitlines()
        return short(redact(code[0] if code else "python"), 64), None
    if tool in ("write_file", "patch", "read_file") and path:
        p = redact(str(path))
        return os.path.basename(p) or p, p
    if tool == "search_files":
        return short(redact(f"{args.get('pattern', '')} in {args.get('path', '.')}"), 64), None
    if tool in ("web_search",):
        return short(redact(str(args.get("query", ""))), 64), None
    if tool in ("web_extract", "browser_navigate"):
        url = args.get("url") or (args.get("urls") or [""])[0]
        return short(redact(str(url)), 64), None
    if tool == "delegate_task":
        return short(redact(str(args.get("goal") or args.get("task") or "subagent")), 64), None
    if tool in ("skill_view", "skill_manage"):
        return str(args.get("name", "skill")), None
    if tool == "memory":
        return short(redact(f"{args.get('action', '')} {args.get('target', '')}".strip()), 64), None
    if tool == "session_search":
        return short(redact(str(args.get("query", "past sessions"))), 64), None
    for key in ("query", "name", "url", "command", "target"):
        if args.get(key):
            return short(redact(str(args[key])), 64), None
    return tool.replace("_", " "), None


def _is_error(content: str | None) -> bool:
    if not content:
        return False
    head = content[:600].lower()
    if '"error"' in head and '"error": null' not in head:
        return True
    for marker in ('"exit_code": 1', '"exit_code": 2', '"exit_code": 127', "traceback (most recent", '"success": false'):
        if marker in head:
            return True
    return False


def _clean_ask(text: str) -> str:
    """The user's request without injected harness preambles."""
    text = (text or "").strip()
    # Platforms sometimes prepend bracketed context blocks; keep the last paragraph then.
    if text.startswith("[") and len(text) > 600:
        paras = [p.strip() for p in text.split("\n\n") if p.strip()]
        if paras:
            text = paras[-1]
    return short(redact(" ".join(_plain(text).split())), 220)


def hermes_home() -> str:
    """The active profile's Hermes home (multi-profile safe); falls back to $HERMES_HOME / ~/.hermes standalone."""
    try:
        from hermes_constants import get_hermes_home  # type: ignore

        return str(get_hermes_home())
    except Exception:
        return os.environ.get("HERMES_HOME") or os.path.expanduser("~/.hermes")


def default_db_path() -> str:
    return os.path.join(hermes_home(), "state.db")


def _connect(db_path: str) -> sqlite3.Connection:
    uri = f"file:{db_path}?mode=ro"
    con = sqlite3.connect(uri, uri=True, timeout=5)
    con.row_factory = sqlite3.Row
    return con


def list_sessions(db_path: str | None = None, limit: int = 10, since: float | None = None) -> list[dict[str, Any]]:
    con = _connect(db_path or default_db_path())
    try:
        q = (
            "SELECT id, title, source, model, started_at, ended_at, tool_call_count, message_count "
            "FROM sessions WHERE archived = 0 AND hidden = 0"
        )
        params: list[Any] = []
        if since is not None:
            q += " AND started_at >= ?"
            params.append(since)
        q += " ORDER BY started_at DESC LIMIT ?"
        params.append(limit)
        return [dict(r) for r in con.execute(q, params)]
    finally:
        con.close()


def resolve_session_id(db_path: str | None, wanted: str | None) -> str | None:
    """`wanted` may be a full id, a unique prefix, or None/'latest' for the newest session with tool calls."""
    con = _connect(db_path or default_db_path())
    try:
        if not wanted or wanted == "latest":
            row = con.execute(
                "SELECT id FROM sessions WHERE archived = 0 AND tool_call_count > 0 ORDER BY started_at DESC LIMIT 1"
            ).fetchone()
            return row["id"] if row else None
        rows = con.execute("SELECT id FROM sessions WHERE id = ? OR id LIKE ? LIMIT 2", (wanted, wanted + "%")).fetchall()
        return rows[0]["id"] if len(rows) == 1 else None
    finally:
        con.close()


def _fetch(session_id: str, db_path: str | None, include_ancestors: bool = True) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Session row + its active messages, oldest first.

    Inside Hermes this uses the public, read-only ``hermes_state.SessionDB`` (it follows compression
    lineage). Outside Hermes (tests, plain scripts) it falls back to a read-only SQLite connection.
    """
    if db_path is None:
        try:
            from hermes_state import SessionDB  # type: ignore

            # Context manager closes the read pool, so CLI runs exit promptly.
            with SessionDB(read_only=True) as db:
                s = db.get_session(session_id)
                if s is None:
                    raise LookupError(f"no session {session_id}")
                return dict(s), [dict(m) for m in db.get_messages(session_id, include_ancestors=include_ancestors)]
        except ImportError:
            pass
    con = _connect(db_path or default_db_path())
    try:
        row = con.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if row is None:
            raise LookupError(f"no session {session_id}")
        msgs = con.execute(
            "SELECT role, content, tool_calls, tool_call_id, tool_name, timestamp FROM messages "
            "WHERE session_id = ? AND active = 1 ORDER BY id",
            (session_id,),
        ).fetchall()
        return dict(row), [dict(m) for m in msgs]
    finally:
        con.close()


def load_timeline(
    session_id: str,
    db_path: str | None = None,
    live_events: list[dict[str, Any]] | None = None,
    *,
    include_ancestors: bool = True,
) -> Timeline:
    """include_ancestors=False when totalling many sessions, so compressed lineages aren't counted twice."""
    s, msgs = _fetch(session_id, db_path, include_ancestors)

    results: dict[str, Any] = {}
    for m in msgs:
        if m["role"] == "tool" and m["tool_call_id"]:
            results[m["tool_call_id"]] = m

    steps: list[Step] = []
    ask, answer = "", ""
    user_before_tools = ""
    for m in msgs:
        role = m["role"]
        if role == "user" and not steps:
            user_before_tools = m["content"] or user_before_tools
        if role == "assistant" and m["tool_calls"]:
            raw = m["tool_calls"]
            # SessionDB returns tool_calls already decoded; raw SQLite rows hold a JSON string.
            if isinstance(raw, str):
                try:
                    raw = json.loads(raw)
                except ValueError:
                    raw = []
            calls = [c for c in (raw if isinstance(raw, list) else []) if isinstance(c, dict)]
            for c in calls:
                fn = c.get("function") or {}
                tool = fn.get("name") or c.get("name") or "tool"
                args = fn.get("arguments") or {}
                if isinstance(args, str):
                    try:
                        args = json.loads(args or "{}")
                    except ValueError:
                        args = {}
                if not isinstance(args, dict):
                    args = {}
                cat, glyph = _category(tool)
                label, path = _label(tool, args)
                res = results.get(c.get("id")) or results.get(c.get("call_id"))
                secs = None
                err = False
                if res is not None:
                    secs = max(0.0, float(res["timestamp"]) - float(m["timestamp"]))
                    err = _is_error(res["content"])
                steps.append(
                    Step(
                        index=len(steps) + 1,
                        tool=tool,
                        category=cat,
                        glyph=glyph,
                        label=label,
                        started_at=float(m["timestamp"]),
                        seconds=secs,
                        error=err,
                        file=path if tool in WRITE_TOOLS else None,
                        call_id=c.get("id") or c.get("call_id"),
                    )
                )
        elif role == "assistant" and (m["content"] or "").strip():
            answer = m["content"]

    # Live hook data (exact durations / errors) wins over the inferred values.
    if live_events:
        by_call = {e.get("tool_call_id"): e for e in live_events if e.get("tool_call_id")}
        for st in steps:
            ev = by_call.get(st.call_id)
            if ev and ev.get("tool") == st.tool:
                if ev.get("seconds") is not None:
                    st.seconds = float(ev["seconds"])
                st.error = bool(ev.get("error", st.error))

    stamps = [float(m["timestamp"]) for m in msgs]
    active = sum(min(b - a, IDLE_GAP) for a, b in zip(stamps, stamps[1:]) if b > a)
    ask = _clean_ask(user_before_tools)
    files = []
    for st in steps:
        if st.file and st.file not in files:
            files.append(st.file)

    cost = s["actual_cost_usd"] if s["actual_cost_usd"] is not None else s["estimated_cost_usd"]
    return Timeline(
        session_id=session_id,
        title=short(redact(s.get("title") or s.get("display_name") or ask or "Untitled session"), 70),
        source=(s["source"] or "cli"),
        model=(s["model"] or "unknown model"),
        started_at=float(s["started_at"]),
        ended_at=float(s["ended_at"]) if s["ended_at"] else None,
        ask=ask,
        answer=short(redact(" ".join(_plain(answer).split())), 260),
        steps=steps,
        input_tokens=int(s["input_tokens"] or 0) + int(s["cache_read_tokens"] or 0),
        output_tokens=int(s["output_tokens"] or 0),
        cost_usd=float(cost) if cost is not None else None,
        files_touched=files,
        active_seconds=active,
        cwd=redact(s.get("cwd") or ""),
    )


def pick_highlights(steps: list[Step], max_steps: int = 7) -> list[Step]:
    """Choose the steps worth showing on screen, kept in order.

    Always the first and last step, then errors, file edits, one of each tool kind, then the slowest.
    """
    if len(steps) <= max_steps:
        return list(steps)
    chosen: dict[int, Step] = {steps[0].index: steps[0], steps[-1].index: steps[-1]}

    def add(cands: list[Step]) -> None:
        for st in cands:
            if len(chosen) >= max_steps:
                return
            chosen.setdefault(st.index, st)

    add([s for s in steps if s.error][:2])
    add([s for s in steps if s.tool in WRITE_TOOLS])
    seen: set[str] = {s.category for s in chosen.values()}
    kinds = []
    for s in steps:
        if s.category not in seen:
            seen.add(s.category)
            kinds.append(s)
    add(kinds)
    add(sorted(steps, key=lambda s: -(s.seconds or 0)))
    return sorted(chosen.values(), key=lambda s: s.index)
