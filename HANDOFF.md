# Handoff: desktop UX problems in /wrapped (for Claude)

Branch `desktop-ux-fixes` = main + my (Hermes agent, Sonnet) edits. **Treat as unreviewed and possibly wrong; main is the safe baseline.** Pras is unhappy with the desktop experience. Please look at the whole thing, not just my patches.

## The original bug
`/wrapped` in the Hermes **desktop app** failed with `request timed out after 30s: slash.exec`. On local surfaces (desktop/CLI/TUI) the handler rendered synchronously; a render takes 2-10 min (blue theme ~8-10 min, CRT ~2-3 min), desktop slash commands time out at ~30s.

## What I changed (all in `__init__.py` + README)
1. **Cross-process render lock** (`_SharedLock`, flock on `plugin-data/render.lock`): two sessions each started a render at once and slowed each other.
2. **Same-day cache**: the slash command and `wrapped_make` return `~/.hermes/cache/videos/hermes-wrapped-{N}d-{YYYYMMDD}{-vertical}{-blue}.mp4` if it exists. Date is in the filename, so it re-renders tomorrow (and rolls over at UTC midnight, droplet clock is UTC).
3. **`_shrink()`**: blue theme's animated film grain made a 45s video 289 MB (52 Mbps at crf 18). Re-encode with `-crf 26 -maxrate 4M` after render when > 25 MB -> ~24 MB. CRT renders are already ~5 MB.
4. **Desktop slash reply**: on local surfaces the slash command no longer renders; it returns text telling the user to ask the agent to call `wrapped_make`. Messaging platforms keep the background render + `hermes send`.
5. README: Quick reference section (defaults: week, landscape, crt) and corrected "Background work" disclosure.

## Things I tried that do NOT work on desktop (don't retry blindly)
- `ctx.post_message(...)` from the slash handler -> `no_conversation` (no live gateway loop in the desktop backend).
- `ctx.inject_message(..., session_key=...)` -> `inject_message: no live gateway is available`; log showed `has_tui_message_injector == False` in the process that runs the plugin command. (`allow_gateway_injection: true` was set under `plugins.entries.hermes-wrapped` in config.yaml; that is not the issue.)
- Returning `MEDIA:/path` as slash output -> shows as plain text path, no video (only agent replies render MEDIA).

## Open problems
- **Desktop UX is still poor.** The user types `/wrapped`, gets a text instruction, then must prompt the agent to call `wrapped_make`. A fresh agent in a new desktop session did NOT call `wrapped_make` for "show wrapped month vertical blue" (it asked clarifying questions instead). Tool is enabled (`platform_toolsets.cli` has hermes-wrapped; check_fn passes; `hermes tools list` shows it). Is the plugin tool deferred/hidden for desktop sessions? Is there a better desktop surface (Desktop plugin SDK `desktop/plugin.js`) that can render a video pane natively? That is probably the right fix.
- Blue render time (8-10 min) is slow. Consider `draft` quality or fewer frames/lighter grain, and lower the encode cost at source rather than re-encoding after.
- No progress/"done" signal on desktop at all.
- `post_tool_call` hook + catalog PR still pending: validate passes (`hermes plugins validate`), PR to NousResearch/hermes-agent not opened. Hackathon deadline: 5PM EST Nov 11 (about 3:30 AM IST Nov 12).

## Not done / not verified
- Nothing was tested in a real desktop session after patch 4.
- `wrapped_make` long tool call inside a chat turn (5-10 min) is untested.
- tests/ in the repo were not run against these edits.
