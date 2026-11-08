"""Hermes Wrapped: a weekly video of what your agent did for you, what it cost,
and how much of your time went into keeping it running.

Surfaces:
  /wrapped [week|month|year|session [id]] [vertical|landscape] [crt|blue]   slash command (renders in the background, sends the MP4 to the chat)
  wrapped_make                         agent tool ("show me my week with you")
  hermes wrapped ...                   terminal command (week, month, session, schedule, list, doctor)
  post_tool_call hook                  records exact step timing and failures while you work
"""

from __future__ import annotations

import contextvars
import re
import shlex
import json
import logging
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from . import render as _render
from . import store, timeline, wrapped
from . import story as _story
from .storyvideo import render_story

log = logging.getLogger("hermes_wrapped")

PERIODS = {"week": 7, "weekly": 7, "month": 30, "monthly": 30, "year": 365, "yearly": 365, "annual": 365,
           "day": 1, "today": 1}


def _db_path() -> str | None:
    """None inside Hermes (use the public SessionDB); the default state.db path otherwise."""
    try:
        import hermes_state  # noqa: F401  # type: ignore

        return None
    except ImportError:
        return timeline.default_db_path()


# ---------------------------------------------------------------- core actions

_CTX = None  # set in register(); gives access to plugin settings


def _cfg(key: str, default: Any) -> Any:
    try:
        v = _CTX.get_config(key, default) if _CTX is not None else default
    except Exception:
        v = default
    return default if v is None else v


def _bool(v: Any) -> bool:
    return v if isinstance(v, bool) else str(v).strip().lower() in ("1", "true", "yes", "on")


FORMAT_ALIASES = {"vertical": "portrait", "portrait": "portrait", "9:16": "portrait", "916": "portrait", "story": "portrait",
                  "landscape": "landscape", "wide": "landscape", "16:9": "landscape", "169": "landscape"}


THEME_ALIASES = {"crt": "crt", "terminal": "crt", "amber": "crt", "retro": "crt",
                 "blue": "blue", "cobalt": "blue", "film": "blue", "navy": "blue"}


def _theme(value: Any) -> str:
    return THEME_ALIASES.get(str(value or "").strip().lower(), "crt")


def _split_theme(words: list[str]) -> tuple[str | None, list[str]]:
    """Pull a theme word (blue, crt...) out of the arguments; it can go anywhere."""
    theme, rest = None, []
    for w in words:
        if w.lower() in THEME_ALIASES:
            theme = THEME_ALIASES[w.lower()]
        else:
            rest.append(w)
    return theme, rest


def _fmt(value: Any) -> str:
    return FORMAT_ALIASES.get(str(value or "").strip().lower(), "landscape")


QUALITIES = ("draft", "standard", "high")


def make_wrapped(days: int = 7, *, quality: str | None = None, fmt: str | None = None,
                 theme: str | None = None) -> tuple[Path, str]:
    db = _db_path()
    fmt = _fmt(fmt or _cfg("format", "landscape"))
    theme = _theme(theme or _cfg("theme", "crt"))
    period = wrapped.load_period(days, db)
    st = _story.load_story(days, db or str(store.hermes_home() / "state.db"), upkeep_pct=period.upkeep_pct,
                           quotes=_bool(_cfg("quotes", False)), tz_name=str(_cfg("timezone", "") or ""))
    stamp = time.strftime("%Y%m%d")
    tag = ("-vertical" if fmt == "portrait" else "") + ("-blue" if theme == "blue" else "")
    work = store.data_dir() / "renders" / f"wrapped-{days}d-{stamp}{tag}"
    q = quality or str(_cfg("quality", "standard"))  # an explicit --quality beats the setting
    q = q if q in QUALITIES else "standard"
    mp4 = render_story(st, work, quality=q, show_cost=_bool(_cfg("show_cost", True)),
                       fmt=fmt, music=_bool(_cfg("music", True)), show_titles=_bool(_cfg("show_titles", True)),
                       theme=theme)
    final = store.video_dir() / f"hermes-wrapped-{days}d-{stamp}{tag}.mp4"
    final.write_bytes(mp4.read_bytes())
    shutil.rmtree(work, ignore_errors=True)  # keep only the MP4
    return final, story_summary(st, period, show_cost=_bool(_cfg("show_cost", True)))


def story_summary(st: "_story.Story", period: "wrapped.Period", *, show_cost: bool = True) -> str:
    word = wrapped._period_word(period.days)
    if st.sessions == 0:
        return f"Your {word} with Hermes: no sessions yet. Nothing to wrap."
    lines = [f"Your {word} with Hermes: you are {st.archetype}" + (f", with a side of {st.side_trait}." if st.side_trait else ".")]
    bits = [f"{st.sessions} sessions", f"Hermes worked {st.agent_hours:g}h"]
    if st.wall_hours and st.agent_hours > st.wall_hours * 1.15:
        bits.append(f"you watched {st.wall_hours:g}h")
    if show_cost and st.cost:
        bits.append(f"${st.cost:,.2f}")
    lines.append(" · ".join(bits))
    if st.peak_agents >= 3:
        lines.append(f"Peak: {st.peak_agents} agents at once ({st.peak_when}).")
    lines.append(f"Working for you {100 - period.upkeep_pct}% · upkeep {period.upkeep_pct}%")
    if st.tz_guessed:
        lines.append(f"(Times are in {st.tz}, your server's clock. Set `timezone` in config.yaml, "
                     "e.g. `timezone: America/New_York`, for your local time.)")
    return "\n".join(lines)


def make_session_reel(session: str | None = None, *, quality: str = "standard") -> Path:
    db = _db_path()
    sid = timeline.resolve_session_id(db, session)
    if not sid:
        raise LookupError("no matching session with tool calls (try `hermes wrapped list`)")
    tl = timeline.load_timeline(sid, db, store.events_for(sid))
    work = store.data_dir() / "renders" / sid
    mp4 = _render.render(tl, work, quality=quality)
    final = store.video_dir() / f"hermes-session-{sid[:12]}.mp4"
    final.write_bytes(mp4.read_bytes())
    shutil.rmtree(work, ignore_errors=True)
    return final


def _hermes_bin() -> str:
    """The `hermes` CLI, even when ~/.local/bin isn't on the gateway's PATH."""
    found = shutil.which("hermes")
    if found:
        return found
    local = Path.home() / ".local" / "bin" / "hermes"
    return str(local) if local.exists() else "hermes"


def _send(target: str, caption: str, path: Path | None) -> None:
    """Deliver an MP4 with `hermes send` (works from a background thread; no agent turn, no tokens).

    Raises if delivery fails, so the caller can tell the user instead of failing silently.
    """
    r = subprocess.run(
        [_hermes_bin(), "send", "--json", "--to", target, f"{caption}\nMEDIA:{path}" if path else caption],
        capture_output=True, text=True, timeout=180, check=False,
    )
    log.info("hermes-wrapped: send to %s exit=%s out=%s err=%s",
             target.split(":")[0], r.returncode, r.stdout.strip()[-400:], r.stderr.strip()[-400:])
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout or f"hermes send exited {r.returncode}").strip()[-300:])


def _parse(args: list[str]) -> tuple[str, int, str | None, str | None]:
    """(mode, days, session_id, format). Format words (vertical, 9:16, landscape...) can go anywhere."""
    fmt = None
    rest = []
    for a in args:
        if a.lower() in FORMAT_ALIASES:
            fmt = FORMAT_ALIASES[a.lower()]
        else:
            rest.append(a)
    if not rest:
        return "wrapped", _default_days(), None, fmt
    a = rest[0].lower()
    if a in ("session", "reel"):
        return "session", 0, (rest[1] if len(rest) > 1 else None), fmt
    if a in PERIODS:
        return "wrapped", PERIODS[a], None, fmt
    if a.rstrip("d").isdigit():
        return "wrapped", max(1, min(365, int(a.rstrip("d")))), None, fmt
    return "wrapped", _default_days(), None, fmt


def _default_days() -> int:
    """What a bare /wrapped covers: the `period` setting (week, month or year). Week if unset."""
    return PERIODS.get(str(_cfg("period", "week") or "week").strip().lower(), 7)


# ---------------------------------------------------------------- hook

def _on_post_tool_call(**kw: Any) -> None:
    try:
        call_id, sid = kw.get("tool_call_id"), kw.get("session_id")
        if not call_id or not sid:
            return
        ms = kw.get("duration_ms")
        store.record_tool_event(
            tool_call_id=str(call_id),
            session_id=str(sid),
            tool=str(kw.get("tool_name") or ""),
            seconds=(float(ms) / 1000.0) if ms is not None else None,
            status=str(kw.get("status") or "ok"),
        )
    except Exception:  # an observer must never break the agent
        log.debug("hermes-wrapped: could not record tool event", exc_info=True)


# ---------------------------------------------------------------- slash command

_RENDER_LOCK = threading.Lock()  # one render at a time: each one runs a headless Chrome for a minute or two


def _wrapped_command(ctx):
    def handler(raw_args: str, *, session_id: str = "", platform: str = "", source: Any = None, **_: Any) -> str:
        theme, words = _split_theme((raw_args or "").split())
        mode, days, sid, fmt = _parse(words)
        if mode == "session" and not sid:
            sid = session_id or None
        chat = getattr(source, "chat_id", None) if source is not None else None
        thread = getattr(source, "thread_id", None) if source is not None else None
        plat = platform or (getattr(getattr(source, "platform", None), "value", "") if source is not None else "")
        target = None
        if plat and chat and plat not in ("cli", "tui", "desktop", "acp"):
            target = f"{plat}:{chat}" + (f":{thread}" if thread else "")

        def render() -> tuple[Path, str]:
            if mode == "session":
                return make_session_reel(sid), "This session, as a video"
            return make_wrapped(days, fmt=fmt, theme=theme)

        if not _RENDER_LOCK.acquire(blocking=False):
            return "A Wrapped is already rendering. It'll land in a minute; try again after that."

        if not target:
            # Local surfaces (CLI, TUI, desktop): render right here and answer with the file,
            # so the result can't get lost when no messaging gateway is running.
            try:
                path, caption = render()
                return f"{caption}\nMEDIA:{path}"
            except Exception as exc:
                return f"Hermes Wrapped couldn't render: {exc}"
            finally:
                _RENDER_LOCK.release()

        def work() -> None:
            try:
                path, caption = render()
                _send(target, caption, path)
            except Exception as exc:
                log.warning("hermes-wrapped: render or delivery failed: %s", exc)
                try:
                    _send(target, f"Hermes Wrapped couldn't render: {exc}", None)
                except Exception:
                    pass
            finally:
                _RENDER_LOCK.release()

        # Not a daemon: a gateway shutting down waits for the video instead of killing it mid-render.
        threading.Thread(target=contextvars.copy_context().run, args=(work,), name="hermes-wrapped").start()
        what = "this session" if mode == "session" else f"your {wrapped._period_word(days)}"
        return f"Wrapping up {what}. It takes a minute or two; the video will land here."

    return handler


# ---------------------------------------------------------------- agent tool

TOOL_SCHEMA = {
    "name": "wrapped_make",
    "description": (
        "Render Hermes Wrapped: a short MP4 recap of the past week or month (what Hermes did, what it cost, "
        "and how much time went into upkeep of Hermes itself), or of one session. Built from recorded "
        "session data, no model calls. Use when the user asks for their Wrapped, a recap, a weekly "
        "summary video, or 'how much have you done for me'. Returns a MEDIA: path; put it in your reply "
        "so the video is delivered, plus the text summary."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "scope": {"type": "string", "enum": ["week", "month", "year", "session"], "description": "Default: the user's period setting (week if unset)."},
            "session_id": {"type": "string", "description": "For scope=session: id or prefix; omit for the latest."},
            "format": {"type": "string", "enum": ["landscape", "portrait"], "description": "portrait = 9:16 for stories/reels. Default: the user's setting."},
            "theme": {"type": "string", "enum": ["crt", "blue"], "description": "crt = amber terminal, blue = navy and cobalt film look. Default: the user's setting."},
        },
        "required": [],
        "additionalProperties": False,
    },
}


def _tool_handler(args: dict[str, Any], **kw: Any) -> str:
    args = args or {}
    if not _RENDER_LOCK.acquire(timeout=300):
        return json.dumps({"ok": False, "error": "another Wrapped is still rendering; try again in a minute"})
    try:
        if args.get("scope") == "session":
            path = make_session_reel(args.get("session_id") or None)
            return json.dumps({"ok": True, "video": str(path), "deliver": f"MEDIA:{path}"})
        days = PERIODS.get(str(args.get("scope") or ""), 0) or _default_days()
        path, summary = make_wrapped(days, fmt=args.get("format"), theme=args.get("theme"))
        return json.dumps({"ok": True, "video": str(path), "deliver": f"MEDIA:{path}", "summary": summary})
    except Exception as exc:
        return json.dumps({"ok": False, "error": str(exc)})
    finally:
        _RENDER_LOCK.release()


def _tool_check() -> bool:
    return _render.hyperframes_available()[0]


# ---------------------------------------------------------------- CLI

SCRIPT_NAME = "hermes-wrapped-weekly.sh"


def _cli_setup(sub) -> None:
    sp = sub.add_subparsers(dest="wrapped_cmd")
    for name, helptext in (("week", "Your last 7 days (default)"), ("month", "Your last 30 days"), ("year", "Your last 365 days")):
        p = sp.add_parser(name, help=helptext)
        p.add_argument("--quality", default=None, choices=["draft", "standard", "high"])
        p.add_argument("--send", metavar="TARGET", help="Also deliver it, e.g. telegram or telegram:CHAT_ID")
        p.add_argument("--media-line", action="store_true", help="Print summary + MEDIA: line (for no-agent cron delivery)")
        p.add_argument("--vertical", action="store_true", help="9:16 for stories and reels (default: your `format` setting)")
        p.add_argument("--landscape", action="store_true", help="16:9")
        p.add_argument("--theme", default=None, choices=["crt", "blue"], help="Look of the video (default: your `theme` setting)")
    p = sp.add_parser("session", help="One session as a short video")
    p.add_argument("session", nargs="?", default="latest")
    p.add_argument("--quality", default="standard", choices=["draft", "standard", "high"])
    p.add_argument("--send", metavar="TARGET")
    s = sp.add_parser("schedule", help="Deliver your Wrapped every week via Hermes cron (no agent, no tokens)")
    s.add_argument("--deliver", default="telegram", help="Delivery target (default: telegram home channel)")
    s.add_argument("--when", default="0 18 * * 0", help="Cron schedule (default: Sundays 18:00)")
    sp.add_parser("list", help="Recent sessions")
    sp.add_parser("doctor", help="Check Node, npx and ffmpeg")
    sub.set_defaults(func=_cli)


def _cli(args) -> int:
    cmd = getattr(args, "wrapped_cmd", None) or ""
    if cmd == "doctor":
        ok, why = _render.hyperframes_available()
        print("ready" if ok else f"not ready: {why}")
        print(f"renderer: hyperframes@{_render.HYPERFRAMES_VERSION}")
        print(f"videos:   {store.video_dir()}")
        tz, guessed = _story.get_tz(str(_cfg("timezone", "") or ""))
        print(f"timezone: {tz}" + ("  (server clock, not configured: set `timezone` in config.yaml for your local time)" if guessed else ""))
        return 0 if ok else 1
    if cmd == "list":
        for s in timeline.list_sessions(_db_path(), limit=15):
            when = time.strftime("%b %d %H:%M", time.localtime(s["started_at"]))
            print(f"{s['id'][:20]:<22} {when}  {s['tool_call_count'] or 0:>3} steps  {s['title'] or ''}")
        return 0
    if cmd == "schedule":
        hermes = _hermes_bin()
        existing = subprocess.run([hermes, "cron", "list"], capture_output=True, text=True, timeout=60)
        if "Hermes Wrapped (weekly)" in (existing.stdout or ""):
            print("Already scheduled (see `hermes cron list`). Remove that job first to change it.")
            return 0
        scripts = store.hermes_home() / "scripts"
        scripts.mkdir(parents=True, exist_ok=True)
        script = scripts / SCRIPT_NAME
        # Absolute path: cron's PATH often lacks ~/.local/bin.
        script.write_text(
            "#!/bin/sh\n# Written by the hermes-wrapped plugin. Renders and prints your weekly Wrapped.\n"
            f"exec {shlex.quote(hermes)} wrapped week --media-line\n"
        )
        script.chmod(0o755)
        r = subprocess.run(
            [hermes, "cron", "create", "--name", "Hermes Wrapped (weekly)", "--no-agent",
             "--script", SCRIPT_NAME, "--deliver", args.deliver, args.when],
            capture_output=True, text=True, timeout=60,
        )
        print(r.stdout or r.stderr)
        return r.returncode
    if cmd == "session":
        path = make_session_reel(args.session, quality=args.quality)
        print(path)
        if args.send:
            _send(args.send, "This session, as a video", path)
        return 0
    days = PERIODS.get(cmd or "", 0) or _default_days()
    fmt = "portrait" if getattr(args, "vertical", False) else ("landscape" if getattr(args, "landscape", False) else None)
    path, summary = make_wrapped(days, quality=getattr(args, "quality", None), fmt=fmt, theme=getattr(args, "theme", None))
    if getattr(args, "media_line", False):
        print(f"{summary}\nMEDIA:{path}")
    else:
        print(summary)
        print(path)
    if getattr(args, "send", None):
        _send(args.send, summary, path)
    return 0


# ---------------------------------------------------------------- register

def register(ctx) -> None:
    global _CTX
    _CTX = ctx
    ctx.register_hook("post_tool_call", _on_post_tool_call)
    ctx.register_command(
        "wrapped",
        _wrapped_command(ctx),
        description="Your week with Hermes as a video: what it did, what it cost, how much was upkeep",
        args_hint="[week | month | year | session [id]] [vertical | landscape] [crt | blue]",
    )
    ctx.register_tool(
        name="wrapped_make",
        toolset="hermes-wrapped",
        schema=TOOL_SCHEMA,
        handler=_tool_handler,
        check_fn=_tool_check,
        description=TOOL_SCHEMA["description"],
        emoji="🎬",
    )
    ctx.register_cli_command(
        name="wrapped",
        help="Hermes Wrapped: your week, month or year with Hermes as a video",
        setup_fn=_cli_setup,
        handler_fn=_cli,
    )
