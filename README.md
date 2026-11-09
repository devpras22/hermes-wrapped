# Hermes Wrapped

Your week with Hermes as a 40-second video with its own soundtrack, delivered every Sunday. Spotify Wrapped for your agent.

It tells the story of your week from your own records:

- **The hook:** "11 days away. Then you came back swinging."
- **Hermes worked 33 hours. You watched 18.** Agent-hours against your hours, because helpers run in parallel.
- **You didn't use an agent. You ran a team.** Peak number of agents running at once, and when.
- **Your peak hour** on a 24-hour clock, and how much of your week happened after 11pm.
- **Your latest message** of the week ("1:58am, Thursday").
- **Your biggest job**, its minutes and its cost.
- **Your verbal tic** ("You said 'right?' 58 times.") from a fixed, safe list of phrases.
- **The receipt:** what the week cost, and how much went into upkeep (setting up, configuring and fixing Hermes) versus Hermes working for you.
- **Your archetype:** The Night Shift Manager, The 2am Debugger, The Delegator, The Config Tinkerer, The Marathoner, The Perfectionist, The Sprinter or The Steady Hand, picked by plain rules over your numbers.

Scenes with nothing interesting to say are skipped, so a quiet week makes a shorter video.

**Zero tokens, every time.** Every number, the archetype and every line are computed by code from your records: same week, same video, no model calls. The soundtrack is synthesized on your machine for each week (no bundled audio, no downloads). Landscape (16:9) or vertical (9:16) for stories and reels, in two looks taken from Hermes's own releases: `crt` (a glowing amber terminal with pixel type) and `blue` (navy and cobalt with film grain and an italic serif).

## Why a plugin, not a prompt

| | Skill + prompt | Hermes Wrapped |
|---|---|---|
| Data | The model can only see the current chat | Every session in the period, read from Hermes's session store, plus a `post_tool_call` hook that logs exact step timing and failures |
| Accuracy | Numbers are estimated or invented | Counted, never generated |
| Cost | Tokens every time, across dozens of sessions | Zero tokens: the story, archetype and soundtrack are made by code |
| Privacy | Hope the model leaves keys out | API keys, tokens, JWTs, emails and home paths are stripped before rendering |
| Every week | You have to remember to ask | `hermes wrapped schedule` adds a no-agent cron job that delivers it to Telegram (or anywhere) every Sunday |
| Same data, same video | No | Yes |

## Quick reference

**A bare `/wrapped` gives you:** your last 7 days, landscape (16:9), CRT amber-terminal theme. Change those defaults with the `period`, `format` and `theme` settings below.

| You type | You get |
|---|---|
| `/wrapped` | week, landscape, CRT |
| `/wrapped month` / `/wrapped year` | last 30 / 365 days (also `7d`, `14d`... any 1-365) |
| `/wrapped vertical` | 9:16 for stories and reels (`landscape` for 16:9) |
| `/wrapped blue` | navy and cobalt theme with film grain (`crt` for amber) |
| `/wrapped month vertical blue` | any combination; words can go in any order |
| `/wrapped session [id]` | one session as a short video (latest if no id) |

**Choosing a theme:** say `crt` or `blue` in the command for that video, or set `theme` in `config.yaml` to change your default.

**What to expect:** a first render takes a few minutes (it draws every frame in headless Chrome). The first `/wrapped` replies "Rendering..."; run the same command again and the video appears instantly. The blue theme and the year period are the slowest; use `quality: draft` for faster previews.

## Install

```bash
hermes plugins install devpras22/hermes-wrapped
hermes wrapped doctor        # checks Node 22+, npx and ffmpeg
hermes wrapped schedule      # every Sunday 18:00 to your Telegram home channel
```

Needs **Node.js 22+, npx and ffmpeg** on the machine running Hermes. The first render downloads the pinned HyperFrames CLI and a headless Chrome (about 150 MB, once).

## Use

In any chat:

```
/wrapped                  # your default (last 7 days unless you change `period`)
/wrapped month            # your last 30 days
/wrapped year             # your last 365 days
/wrapped month vertical   # any period, 9:16 for stories and reels
/wrapped year landscape   # or 16:9, whatever your default is
/wrapped blue             # the navy and cobalt look (or `crt`, the default)
/wrapped session [id]     # one session as a short video
```

Or ask: "show me my week with you". The agent calls `wrapped_make` and sends the video.

From the terminal:

```bash
hermes wrapped week [--vertical] [--send telegram]
hermes wrapped month
hermes wrapped year --vertical --theme blue
hermes wrapped session <id>
hermes wrapped schedule --deliver telegram --when "0 18 * * 0"
hermes wrapped list
```

Videos are saved to `~/.hermes/cache/videos/`.

## How "upkeep" is decided

A session counts as upkeep when it ran inside `~/.hermes`, its title is about Hermes itself (Hermes, gateway, plugins, profiles, providers, config, API keys, changing models), at least 40% of its steps touched Hermes (`~/.hermes`, `config.yaml`, `hermes config|model|auth|plugins|…`), or its title is about setup or fixing and at least 20% of its steps touched Hermes. The video shows the reason next to each upkeep session, so you can see exactly why it counted.

## How it works

1. `timeline.py` reads sessions through Hermes's public read-only `hermes_state.SessionDB`: requests, tool calls and results, timings, tokens and cost.
2. `wrapped.py` totals the period, compares it with the period before, and classifies upkeep.
3. `redact.py` strips secrets and private paths.
4. `render.py` writes a [HyperFrames](https://github.com/heygen-com/hyperframes) composition (HTML + GSAP) and renders it with a pinned `hyperframes` CLI.

## Settings

Under `plugins.entries.hermes-wrapped.settings` in `config.yaml`:

| Setting | Default | What it does |
|---|---|---|
| `show_cost` | `true` | Show what the week cost. Turn off before posting publicly if you prefer. |
| `music` | `true` | Add the generated soundtrack. |
| `theme` | `crt` | `crt` (amber terminal) or `blue` (navy, cobalt, film grain). Or ask per video: `/wrapped blue`. |
| `period` | `week` | What a bare `/wrapped` covers: `week`, `month` or `year`. |
| `format` | `landscape` | `landscape` (16:9) or `portrait` (9:16). Or ask per video: `/wrapped vertical`. |
| `timezone` | `""` | IANA name such as `America/New_York` for the clock scenes. Empty uses Hermes's own `timezone` setting. |
| `show_titles` | `true` | Show your biggest project's name. Turn off to show "Your biggest project" instead. |
| `quotes` | `false` | Show your latest late-night message, only if it is short, clean and names nobody. |
| `quality` | `standard` | `draft`, `standard` or `high`. |

Times use the plugin's `timezone` setting, then Hermes's own (`timezone` in `config.yaml` or `HERMES_TIMEZONE`), then the server clock. When it falls back to a UTC server clock, the caption says so and `hermes wrapped doctor` tells you how to fix it.

## Disclosures

- **Reads outside plugin data:** your session history (`state.db`), read-only, through `hermes_state.SessionDB` and a read-only SQLite connection. Nothing leaves your machine unless you deliver a video.
- **Shell commands / subprocesses:** `npx --yes hyperframes@0.8.217 render` (pinned) to render, `hermes send` to deliver. `hermes wrapped schedule` runs `hermes cron create`.
- **Third-party network calls:** the first render downloads the HyperFrames CLI from npm (`hyperframes@0.8.217` is pinned; its own dependencies are resolved by npm) and a headless Chrome build chosen by HyperFrames. The fonts (Silkscreen, VT323, Pathway Gothic One, Instrument Serif and IBM Plex Mono, all SIL Open Font License) ship in `assets/fonts`, so nothing is fetched for type. No model or TTS calls. No telemetry.
- **Writes:** tool-timing events to `~/.hermes/plugin-data/hermes-wrapped/events.db` (kept 30 days), a temporary render folder under the same directory (deleted after each render), MP4s to `~/.hermes/cache/videos/`, and, only if you run `hermes wrapped schedule`, one script at `~/.hermes/scripts/hermes-wrapped-weekly.sh`.
- **Background work:** `/wrapped` always renders in a background thread so the chat is never blocked (desktop slash commands time out after about 30 seconds). In messaging apps the video is sent with `hermes send` when it is done. In the CLI, TUI and desktop app a slash command cannot show a video, so `/wrapped` tells you to ask in chat ("show wrapped month vertical blue") and the agent calls `wrapped_make`, which renders (or returns today's saved video) and replies with the video. A cross-process file lock (`render.lock`) means only one render runs at a time, however many sessions ask.
- **Credentials:** none.
- **Privacy on screen:** in the weekly video, the biggest project's name is shown only after secrets, paths, IPs, logins and domains are removed (turn it off with `show_titles: false`), and your messages are never shown unless `quotes` is on. `/wrapped session` is different: it shows that session's first request, its step labels and the final answer, with secrets and home paths stripped.

## Related

For polished demo videos of a single session, with a storyboard editor, use [Showreel](https://github.com/Duffey234/hermes-showreel). Wrapped's `/wrapped session` is a quick, no-frills version; Wrapped is about the week.

## Credits

Rendering by HyperFrames (HeyGen, Apache-2.0). Animation by GSAP 3.14.2, bundled in `assets/` under the [GSAP standard license](https://gsap.com/standard-license); see `NOTICE`.

MIT licensed, except `assets/gsap.min.js` (see `LICENSE` and `NOTICE`).
