# Changelog

## 0.1.1

- Desktop app: `/wrapped` now answers at once and the finished video plays in the same chat (handed over with `inject_message`; needs `allow_gateway_injection: true`). Fixes the 30-second `slash.exec` timeout and Desktop sessions (platform `local`) being treated as a messaging chat.
- One render at a time across processes, a video rendered today is reused, and oversized blue renders are re-encoded to about 20 MB.
- `wrapped_make` description tells the agent to call it for any 'wrapped' request and how to map week/month/year, vertical and blue.
- New `blue` theme from Hermes's website and X trailers: navy and cobalt, film grain, tall condensed type, italic serif for the title and archetype, `#03` mono labels. Pick it with `/wrapped blue`, `--theme blue` or the `theme` setting; `crt` stays the default.

## 0.1.0 (2026-11-08)

First release, for the Hermes Agent Plugin Hackathon.

- `/wrapped`: a 40-second story video of your week, month or year with Hermes, built from your session records with zero model calls.
- Scenes: hook, agent hours vs. yours, peak agents at once, peak hour clock, latest message, biggest job (a session plus every helper it spawned), verbal tic, receipt with upkeep share, archetype verdict.
- Landscape (16:9) and vertical (9:16): `/wrapped vertical`, `/wrapped year landscape`, or the `format` setting.
- `period` setting picks what a bare `/wrapped` covers (week, month or year).
- Soundtrack synthesized on your machine, with a mood picked from your week (night, dawn, upbeat, steady).
- CRT terminal look matching Hermes releases: bundled Silkscreen and VT323 fonts (OFL), text sized in Python from real glyph widths so nothing overflows or wraps badly.
- Timezone from the plugin setting, `HERMES_TIMEZONE`, or Hermes's config.
- Privacy: secrets, paths, IPs, emails and domains are stripped; `show_cost`, `show_titles` and `quotes` settings.
- `wrapped_make` agent tool, `hermes wrapped` terminal command, `hermes wrapped schedule` for a weekly delivery.
- `/wrapped session [id]`: one session as a short video.
