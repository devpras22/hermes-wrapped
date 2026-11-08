"""Offline tests: no Hermes install, no rendering. Run: python3 -m pytest tests -q"""

import json
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _load import load  # noqa: E402

load()
from hermes_wrapped import redact as R  # noqa: E402
from hermes_wrapped import render as V  # noqa: E402
from hermes_wrapped import timeline as T  # noqa: E402
from hermes_wrapped import wrapped as W  # noqa: E402


def make_db(tmp_path: Path) -> str:
    db = tmp_path / "state.db"
    con = sqlite3.connect(db)
    con.executescript(
        """
        CREATE TABLE sessions (id TEXT PRIMARY KEY, source TEXT, model TEXT, title TEXT, display_name TEXT,
          started_at REAL, ended_at REAL, message_count INT, tool_call_count INT, input_tokens INT,
          output_tokens INT, cache_read_tokens INT, estimated_cost_usd REAL, actual_cost_usd REAL,
          archived INT DEFAULT 0, hidden INT DEFAULT 0);
        CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, role TEXT, content TEXT,
          tool_calls TEXT, tool_call_id TEXT, tool_name TEXT, timestamp REAL, active INT DEFAULT 1);
        """
    )
    t = 1_790_000_000.0
    con.execute(
        "INSERT INTO sessions VALUES ('s1','telegram','claude-sonnet-5-5','Fix the deploy',NULL,?,?,6,2,1000,500,0,0.12,NULL,0,0)",
        (t, t + 100),
    )
    calls = [
        {"id": "c1", "function": {"name": "terminal", "arguments": json.dumps({"command": "npm run build"})}},
        {"id": "c2", "function": {"name": "write_file", "arguments": json.dumps({"path": "/Users/alice/app/vercel.json"})}},
    ]
    rows = [
        ("user", "the deploy is broken, fix it", None, None, None, t),
        ("assistant", "", json.dumps(calls), None, None, t + 5),
        ("tool", '{"output": "ok", "exit_code": 0}', None, "c1", "terminal", t + 9),
        ("tool", '{"error": "permission denied"}', None, "c2", "write_file", t + 10),
        ("assistant", "**Fixed.** The build passes now.", None, None, None, t + 30),
    ]
    con.executemany(
        "INSERT INTO messages (session_id, role, content, tool_calls, tool_call_id, tool_name, timestamp) VALUES ('s1',?,?,?,?,?,?)",
        rows,
    )
    con.commit()
    con.close()
    return str(db)


def test_redact_secrets_and_home():
    s = R.redact("export OPENAI_API_KEY=sk-proj-abcdefghijklmnop1234 at /Users/alice/x and me@ex.com")
    assert "sk-proj-abcdef" not in s
    assert "/Users/alice" not in s and "~/x" in s
    assert "me@ex.com" not in s


def test_timeline_from_db(tmp_path):
    db = make_db(tmp_path)
    tl = T.load_timeline("s1", db)
    assert [s.tool for s in tl.steps] == ["terminal", "write_file"]
    assert tl.steps[0].label == "npm run build"
    assert tl.steps[0].seconds == 4
    assert tl.steps[1].error is True
    assert tl.files_touched == ["~/app/vercel.json"]
    assert tl.ask == "the deploy is broken, fix it"
    assert tl.answer == "Fixed. The build passes now."
    assert tl.cost_usd == 0.12


def test_live_events_override(tmp_path):
    db = make_db(tmp_path)
    tl = T.load_timeline("s1", db, [{"tool_call_id": "c1", "tool": "terminal", "seconds": 2.5, "error": True}])
    assert tl.steps[0].seconds == 2.5 and tl.steps[0].error


def test_highlights_keep_order_and_errors():
    steps = [T.Step(i, "terminal", "TERMINAL", ">_", f"cmd {i}", float(i), 1.0) for i in range(1, 30)]
    steps[10].error = True
    pick = T.pick_highlights(steps, 7)
    assert len(pick) == 7
    assert pick[0].index == 1 and pick[-1].index == 29
    assert any(s.error for s in pick)
    assert [s.index for s in pick] == sorted(s.index for s in pick)


def test_compose_is_deterministic_and_escaped(tmp_path):
    db = make_db(tmp_path)
    tl = T.load_timeline("s1", db)
    tl.title = "<script>alert(1)</script>"
    a, b = V.compose_html(tl), V.compose_html(tl)
    assert a == b
    assert "<script>alert(1)</script>" not in a
    assert 'data-composition-id="root"' in a and "window.__timelines['root']" in a


def test_wrapped_period_and_trend(tmp_path):
    db = make_db(tmp_path)
    p = W.load_period(days=36500, db_path=db, now=time.time())
    assert p.sessions == 1 and p.steps == 2 and p.previous is not None
    html = W.compose_wrapped_html(p)
    assert "WORKING FOR YOU" in html and "HERMES WRAPPED" in html
    assert "First" in html  # no previous period on record
    assert "sessions" in W.text_summary(p)


def test_upkeep_classifier():
    mk = lambda title, labels, cwd="": T.Timeline("x", title, "cli", "m", 0, None, "", "",
        [T.Step(i, "terminal", "TERMINAL", ">_", l, 0, 1) for i, l in enumerate(labels)], cwd=cwd)
    assert W.upkeep_reason(mk("Change model to luna", ["ls"]))
    assert W.upkeep_reason(mk("misc", ["hermes config set x", "cat ~/.hermes/config.yaml", "ls"]))
    assert W.upkeep_reason(mk("anything", ["ls"], cwd="~/.hermes"))
    assert W.upkeep_reason(mk("Fix the login bug in my app", ["npm test", "git diff"])) is None
    assert W.upkeep_reason(mk("Plan a trip to Goa", ["web search goa"])) is None


def test_story_scenes_and_archetype():
    from hermes_wrapped.story import Story, _archetypes
    from hermes_wrapped.storyvideo import plan_scenes
    s = Story(start=0, end=7 * 86400, tz="UTC", sessions=64, delegated=57, asks=178, cost=241.5, lifetime_cost=261.9,
              agent_hours=33.2, wall_hours=18.4, gap_days=11, peak_agents=6, peak_when="Tue, 10:17pm",
              peak_hour=23, night_share=0.29, late_asks=14, latest_ask="1:58am, Thursday", busiest_day_hours=7.6,
              active_days=6, big_job="Launch Site", big_job_minutes=151, big_job_cost=10.25, tic="right?",
              tic_count=58, tic_line="Hermes said yes. Mostly.", upkeep_pct=14, archetype="The Night Shift Manager",
              archetype_line="x", side_trait="Delegator", period_label="week of Nov 1")
    s.ask_hours[23] = 25
    assert _archetypes(s)[:2] == ["The Night Shift Manager", "The Delegator"]
    names = [sc["name"] for sc in plan_scenes(s)]
    assert names == ["title", "hook", "scale", "crew", "clock", "late", "job", "tic", "receipt", "verdict"]
    hidden = plan_scenes(s, show_cost=False)
    assert all("$" not in str(v) for sc in hidden for v in sc.values())


def test_quiet_week_and_music(tmp_path=None):
    import tempfile, wave
    from pathlib import Path
    from hermes_wrapped.story import Story
    from hermes_wrapped.storyvideo import plan_scenes
    from hermes_wrapped.music import make_music
    s = Story(start=0, end=1, tz="UTC", archetype="The Steady Hand", archetype_line="x")
    assert [sc["name"] for sc in plan_scenes(s)] == ["title", "hook", "verdict"]
    d = Path(tempfile.mkdtemp())
    p = make_music(d / "m.wav", 3.0, seed="t", duck=[(1.0, 2.0)])
    with wave.open(str(p)) as w:
        assert abs(w.getnframes() / w.getframerate() - 3.0) < 0.01


def test_screen_title_strips_private_bits():
    from hermes_wrapped.story import screen_title, _safe_quote
    assert screen_title("Subagent: fix /home/pras/app on devpras.com") == "fix on"
    assert _safe_quote("Are you sure no background task is running") != ""
    assert _safe_quote("ask Riley about the invoice") == ""


def test_portrait_and_format_parsing():
    import hermes_wrapped as P
    from hermes_wrapped.story import Story
    from hermes_wrapped.storyvideo import compose_story_html, plan_scenes, _timed
    s = Story(start=0, end=1, tz="UTC", sessions=3, asks=6, agent_hours=2.0, wall_hours=1.0, active_days=2,
              latest_ask="1:58am, Thursday", archetype="The Steady Hand", archetype_line="x", period_label="week of Nov 1")
    sc = plan_scenes(s)
    total = _timed(sc)
    tall = compose_story_html(s, sc, total, [], fmt="portrait")
    assert 'data-width="1080" data-height="1920"' in tall and ">1:58AM<" in tall and ">THURSDAY<" in tall
    wide = compose_story_html(s, sc, total, [], fmt="landscape")
    assert 'data-width="1920" data-height="1080"' in wide
    assert P._parse(["vertical"]) == ("wrapped", 7, None, "portrait")
    assert P._parse(["month", "9:16"]) == ("wrapped", 30, None, "portrait")
    assert P._parse([]) == ("wrapped", 7, None, None)


def test_year_and_default_period():
    import hermes_wrapped as P
    from hermes_wrapped.story import Story, period_word
    from hermes_wrapped.storyvideo import plan_scenes
    assert P._parse(["year", "landscape"]) == ("wrapped", 365, None, "landscape")
    assert P._parse(["vertical", "yearly"]) == ("wrapped", 365, None, "portrait")

    class Ctx:
        def get_config(self, key, default):
            return {"period": "month"}.get(key, default)
    old, P._CTX = P._CTX, Ctx()
    try:
        assert P._parse([]) == ("wrapped", 30, None, None)
        assert P._parse(["vertical"]) == ("wrapped", 30, None, "portrait")
        assert P._parse(["week"]) == ("wrapped", 7, None, None)
    finally:
        P._CTX = old
    assert period_word(365) == "year"
    s = Story(start=0, end=1, tz="UTC", sessions=3, asks=6, agent_hours=2.0, wall_hours=1.0, active_days=2,
              archetype="The Sprinter", archetype_line="Quiet week, loud days.",
              period_label="Nov 2025 – Nov 2026", period_word="year")
    sc = {x["name"]: x for x in plan_scenes(s)}
    assert sc["title"]["sub"] == "your year with Hermes"
    assert "Quiet year, loud days." in str(sc["verdict"])


def test_moods_follow_the_week():
    from hermes_wrapped.music import mood_for
    assert mood_for("The Night Shift Manager", 23, 0.3) == "night"
    assert mood_for("The Steady Hand", 7, 0.0) == "dawn"
    assert mood_for("The Sprinter", 14, 0.05) == "upbeat"
    assert mood_for("The Steady Hand", 15, 0.05) == "steady"


def test_month_wording_and_privacy_filters():
    from hermes_wrapped.story import Story, screen_title
    from hermes_wrapped.storyvideo import plan_scenes
    from hermes_wrapped.redact import redact
    s = Story(start=0, end=1, tz="UTC", sessions=1, asks=0, agent_hours=1.0, wall_hours=1.0, active_days=1,
              cost=3.0, lifetime_cost=10.0, archetype="The Steady Hand", archetype_line="x",
              period_label="Oct 01 – Oct 31", period_word="month", big_job="Secret Client", big_job_minutes=30)
    text = str(plan_scenes(s))
    assert "week" not in text and "This month" in text and "1 sessions" not in text
    assert "Secret Client" not in str(plan_scenes(s, show_titles=False))
    assert "10.0.0.5" not in redact("ssh root@10.0.0.5") and "hunter2" not in redact("password hunter2")
    assert "/opt/acme" not in redact("cat /opt/acme/.env")
    assert screen_title("Fix api.internal.example.de") == "Fix"


def test_peak_concurrency_ignores_idle_sessions(tmp_path):
    import sqlite3, time as _t
    from hermes_wrapped.story import load_story
    db = tmp_path / "state.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE sessions (id TEXT, title TEXT, source TEXT, parent_session_id TEXT, started_at REAL,"
                " estimated_cost_usd REAL, actual_cost_usd REAL, archived INT, hidden INT, message_count INT)")
    con.execute("CREATE TABLE messages (session_id TEXT, role TEXT, content TEXT, tool_calls TEXT, timestamp REAL, active INT)")
    now = _t.time()
    # A: active briefly at the start and again at the end (idle in between). B: active in the middle.
    con.execute("INSERT INTO sessions VALUES ('A','Alpha','cli',NULL,?,1,NULL,0,0,4)", (now - 7200,))
    con.execute("INSERT INTO sessions VALUES ('B','Beta','cli',NULL,?,1,NULL,0,0,2)", (now - 3700,))
    con.execute("INSERT INTO sessions VALUES ('H','Hidden','cli',NULL,?,99,NULL,0,1,2)", (now - 3000,))
    for sid, ts in (("A", now - 7200), ("A", now - 7100), ("A", now - 100), ("A", now - 50),
                    ("B", now - 3700), ("B", now - 3600), ("H", now - 3000), ("H", now - 2990)):
        con.execute("INSERT INTO messages VALUES (?,?,?,?,?,1)", (sid, "assistant", None, None, ts))
    con.commit(); con.close()
    st = load_story(7, str(db), now=now)
    assert st.peak_agents == 1 and st.sessions == 2 and st.cost == 2


def test_text_fits_its_box_in_both_formats():
    from hermes_wrapped import fontfit as F
    for text, face, mx, box, lines in [("OFFICESPACE", "px", 140, 912, 3), ("1:58AM, THURSDAY", "px", 170, 1580, 2),
                                       ("THE NIGHT SHIFT MANAGER", "px", 124, 800, 3),
                                       ("64 sessions · 33h of work · $242", "term", 68, 912, 1)]:
        size = F.fit(text, face, mx, box, lines)
        got = F.wrap(text, face, size, box)
        assert got is not None and len(got) <= lines, (text, size)
        assert all(F.width(line, face, size) <= box for line in got)
    assert F.fit("HERMES", "px", 210, 1580) == 210      # short words keep their full size


def test_blue_theme_words_and_page():
    import hermes_wrapped as P
    from hermes_wrapped.story import Story
    from hermes_wrapped.storyvideo import compose_story_html, plan_scenes, _timed
    assert P._split_theme(["month", "blue", "vertical"]) == ("blue", ["month", "vertical"])
    assert P._split_theme(["year"]) == (None, ["year"])
    assert P._theme("nonsense") == "crt"
    s = Story(start=0, end=1, tz="UTC", sessions=3, asks=6, agent_hours=2.0, wall_hours=1.0, active_days=2,
              archetype="The Steady Hand", archetype_line="x", period_label="week of Nov 1")
    sc = plan_scenes(s)
    page = compose_story_html(s, sc, _timed(sc), [], fmt="portrait", theme="blue")
    assert 'class="theme-blue"' in page and "Instrument Serif" in page and ">Wrapped<" not in page  # letters are split
    assert "#03 AGENT TIME" in page or "#02" in page
    crt = compose_story_html(s, sc, _timed(sc), [], fmt="portrait")
    assert 'class="theme-crt"' in crt and "Instrument Serif" not in crt
