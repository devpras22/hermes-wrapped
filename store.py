"""Where Hermes Wrapped keeps its own data: live tool events and finished videos."""

from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path
from typing import Any

PLUGIN = "hermes-wrapped"
KEEP_EVENTS_DAYS = 30


def hermes_home() -> Path:
    try:
        from hermes_constants import get_hermes_home  # type: ignore

        return Path(get_hermes_home())
    except Exception:
        return Path(os.environ.get("HERMES_HOME") or os.path.expanduser("~/.hermes"))


def data_dir() -> Path:
    try:
        from plugins.plugin_storage import plugin_data_dir  # type: ignore

        return Path(plugin_data_dir(PLUGIN))
    except Exception:
        d = hermes_home() / "plugin-data" / PLUGIN
        d.mkdir(parents=True, exist_ok=True)
        return d


def video_dir() -> Path:
    """Finished MP4s go to Hermes's video cache, which every delivery mode trusts for MEDIA: tags."""
    try:
        from gateway.platforms.base import get_video_cache_dir  # type: ignore

        d = Path(get_video_cache_dir())
    except Exception:
        d = hermes_home() / "cache" / "videos"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _db() -> sqlite3.Connection:
    con = sqlite3.connect(str(data_dir() / "events.db"), timeout=5)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute(
        "CREATE TABLE IF NOT EXISTS tool_events ("
        " tool_call_id TEXT PRIMARY KEY, session_id TEXT, tool TEXT, seconds REAL, status TEXT,"
        " error INTEGER, at REAL)"
    )
    con.execute("CREATE INDEX IF NOT EXISTS idx_ev_session ON tool_events(session_id)")
    return con


_LAST_PRUNE = 0.0


def record_tool_event(*, tool_call_id: str, session_id: str, tool: str, seconds: float | None, status: str) -> None:
    con = _db()
    try:
        con.execute(
            "INSERT OR REPLACE INTO tool_events VALUES (?,?,?,?,?,?,?)",
            (tool_call_id, session_id, tool, seconds, status, 1 if status in ("error", "blocked") else 0, time.time()),
        )
        global _LAST_PRUNE
        if time.time() - _LAST_PRUNE > 86400:  # prune once a day, not on every tool call
            con.execute("DELETE FROM tool_events WHERE at < ?", (time.time() - KEEP_EVENTS_DAYS * 86400,))
            _LAST_PRUNE = time.time()
        con.commit()
    finally:
        con.close()


def events_for(session_id: str) -> list[dict[str, Any]]:
    path = data_dir() / "events.db"
    if not path.exists():
        return []
    con = _db()
    try:
        rows = con.execute(
            "SELECT tool_call_id, session_id, tool, seconds, error FROM tool_events WHERE session_id = ?",
            (session_id,),
        ).fetchall()
    finally:
        con.close()
    return [
        {"tool_call_id": r[0], "session_id": r[1], "tool": r[2], "seconds": r[3], "error": bool(r[4])} for r in rows
    ]
