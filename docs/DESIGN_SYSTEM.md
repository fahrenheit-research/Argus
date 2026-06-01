# ARGUS — Terminal Design System v1

> The watchful agent. Pixel-art on the outside, production-grade on the inside.
> One sigil — ⟨◇⟩ — and three colors. Everything else flows from there.

This document is the single source of truth for every animation, layout,
color choice, sound design suggestion, and UI pattern in ARGUS. It expands
the original *Agent Argus Terminal & Telegram Animation Guide* into a full
operating system for the brand: how it renders in a terminal, in Telegram,
in the Mac desktop app, and across the multi-agent orchestration screens.

It's a working document. Every snippet here is real Python you can paste
into the codebase (`argus.theme`, `argus.splash`, `argus.banner`,
`argus.platforms.telegram`).

---

## Table of contents

1. [Brand fundamentals — sigil, palette, type, rhythm](#1-brand-fundamentals)
2. [Terminal palette (24-bit + 256-color + 16-color fallbacks)](#2-terminal-palette)
3. [Splash screens — boot, exit, idle](#3-splash-screens)
4. [Rich CLI animations](#4-rich-cli-animations)
5. [Telegram animations & layouts](#5-telegram)
6. [Agent spawning effects](#6-agent-spawning)
7. [Multi-agent orchestration screens (Constellation)](#7-constellation)
8. [Research mode UI](#8-research-mode)
9. [Reasoning mode UI](#9-reasoning-mode)
10. [Tool execution UI](#10-tool-execution)
11. [Progress bars — 12 styles, when to use each](#11-progress-bars)
12. [ASCII dashboards](#12-ascii-dashboards)
13. [Cyberpunk HUD layouts](#13-cyberpunk-huds)
14. [Agent cards](#14-agent-cards)
15. [Live token counters & meters](#15-token-counters)
16. [Streaming response animations](#16-streaming-responses)
17. [Retro pixel theme vs Modern AI-Ops theme](#17-themes)
18. [Loading sound design — chiptune system](#18-sound-design)
19. [Mac app splash specifications](#19-mac-app-splash)
20. [Cursor-style + Claude Code-style interfaces, ported](#20-other-cli-references)
21. [Anti-patterns — what NOT to ship](#21-anti-patterns)

---

## 1. Brand fundamentals

### The sigil

```
⟨ ◇ ⟩    ← display size (splash, /report, demos)
⟨ ◇ ⟩    ← standard (banner, screen headers)
 ⟨◇⟩     ← inline (prompt, bullet, log prefix)
```

- Brackets `⟨ ⟩` are **always Electric Magenta**.
- Diamond `◇` is **always Acid Gold**.
- Cyan never touches the sigil — cyan is for status lines, never for the mark itself.

### Palette

| Token | Hex | RGB | Role |
|---|---|---|---|
| Electric Magenta | `#FF38D1` | `255, 56, 209` | brackets, headings, CTAs, prompt arrow |
| Acid Gold | `#FFC247` | `255, 194, 71` | diamond, success, accent, "done" bursts |
| Ice Cyan | `#42E8F5` | `66, 232, 245` | info, status lines, links, tool-call italics |
| Beige | `#F5E6C8` | `245, 230, 200` | LLM response body text (warm, readable) |
| Background | `#050505` | `5, 5, 5` | terminal bg (never set; respect user) |
| Foreground | `#E6E6E6` | `230, 230, 230` | system prose |
| Dim | `#7A7A7A` | `122, 122, 122` | secondary copy, separators |
| Error | `#FF5C5C` | `255, 92, 92` | the only non-PRD color, for genuine failures |

### Type

- Monospace, always. Recommend Menlo 14pt for the Mac app's Terminal profile.
- Block-letter wordmark uses a 5-row pixel font (in `argus/banner.py` and `argus/splash.py`).
- No emoji in prose. The sigil ⟨◇⟩ is the only "free" emoji. Earn the rest (✓ done, ✗ failure, ⚡ tool firing).

### Rhythm

- Animations at **70–115ms per frame**. Anything faster reads as glitchy.
- Splash total ≤ 3 s. Idle spinner cycles every 600 ms. Tool status updates every ~2.4 s.
- Chimes ≤ 600 ms. No looping sounds, ever.

---

## 2. Terminal palette

### 24-bit ANSI (preferred — all modern terminals)

```python
# argus/theme.py — single source of visual truth
MAGENTA = "#FF38D1"
GOLD    = "#FFC247"
CYAN    = "#42E8F5"
BEIGE   = "#F5E6C8"
FG      = "#E6E6E6"
DIM     = "#7A7A7A"
ERR     = "#FF5C5C"
```

Raw ANSI helpers (used by `argus/splash.py`):

```python
def _rgb(r, g, b):   return f"\x1b[38;2;{r};{g};{b}m"
def _bg(r, g, b):    return f"\x1b[48;2;{r};{g};{b}m"
def _bold():         return "\x1b[1m"
def _reset():        return "\x1b[0m"
def _hide_cursor():  return "\x1b[?25l"
def _show_cursor():  return "\x1b[?25h"
def _clear_screen(): return "\x1b[2J\x1b[H"
def _move_to(r, c=1): return f"\x1b[{r};{c}H"
```

### 256-color fallback (older xterm, ssh from Windows)

```python
# When $COLORTERM != 'truecolor' and 256-color is supported:
MAGENTA_256 = 199    # closest match
GOLD_256    = 220
CYAN_256    = 51
BEIGE_256   = 230
```

### 16-color fallback (TTY, mainframe muscle memory)

```python
MAGENTA_16 = 95   # bright magenta
GOLD_16    = 93   # bright yellow
CYAN_16    = 96   # bright cyan
```

**Detection rule** (call once at startup, cache):

```python
import os, sys
def color_depth() -> int:
    if not sys.stdout.isatty():                return 1   # piped
    if os.environ.get("COLORTERM") == "truecolor": return 24
    term = os.environ.get("TERM", "").lower()
    if "256" in term or "kitty" in term:       return 8
    return 4
```

### The "Magenta never touches Cyan at small sizes" rule

These two are vibrant enough to vibrate when adjacent. Always separate
them with at least one Gold, Beige, or Dim character. The splash uses
Gold dashes (`— — —`) around any cyan text to enforce this.

---

## 3. Splash screens

### Boot splash (current — `argus/splash.py`)

Four phases, ~3 s total, with `\x1b[2J\x1b[H` clears at start and end so
nothing bleeds into the banner that follows:

```
Row 3-7   Hero:    ⟨◇⟩ + A R G U S        (cascade reveal, magenta→gold gradient)
Row 9     Subtitle: — — —  AI AGENTS INITIALIZING…  — — —   (cyan)
Row 11-12 Roster:  ▲ ARCHITECT  ◐ SCOUT  ⚙ ENGINEER  ◇ CARTOGRAPHER
                   ◉ AUDITOR  ✚ MEDIC  ✦ SYNTHESIZER       (wake-up one by one)
Row 15    Label:   — — —  LOADING AGENTS…  — — —
Row 16    Bar:     ▌▰▰▰▰▰▰▰▰▱▱▱▱▱▱▱▱▐   67 %               (magenta bracket frame)
Row 18    Status:  [ OBSERVING ] [ ANALYZING ] [ SYNTHESIZING ] [ EXECUTING ]
```

Each phase uses **absolute cursor positioning** (`\x1b[<row>;<col>H`) — never
`\x1b[<N>A` (cursor-up) which drifts when the terminal scrolls. Honors
`ARGUS_NO_SPLASH=1` and skips entirely in non-TTY contexts.

### Exit splash (current — `argus/banner.py:show_goodbye`)

```
G O O D B Y E   (gold → magenta vertical gradient)
A R G U S       (magenta → gold)
        ⟨◇⟩     (centered)
— Sentinel offline. Until next time. —
       fahrenheit research · f-r.co
```

Fires a chime simultaneously (see §18).

### Idle / breathing animation (proposed)

When ARGUS sits at the prompt for >5 s, the inline glyph in the prompt
breathes between three frames at 1.2 s intervals:

```
argus ⟨◇⟩ ▸     ← rest
argus ⟨◈⟩ ▸     ← inhale
argus ⟨◆⟩ ▸     ← exhale, brightened
```

Implementation: a `threading.Timer` that edits the prompt session's
right-prompt; cancel on any keypress.

---

## 4. Rich CLI animations

### The witty-thinking spinner (current — `argus/platforms/telegram.py`)

Two-layer animation:
1. **Glyph spinner** every 600 ms (`⟨◇⟩ → ⟨◈⟩ → ⟨◆⟩ → ⟨◈⟩`)
2. **Subtitle** every ~2.4 s, drawn from 40 phrases + tool-aware overrides

```
⟨◈⟩  consulting the constellation…
⟨◆⟩  ripgrepping the workspace…           ← when search_files fires
⟨◇⟩  reading the page…                    ← when web_extract fires
```

### Print-style typewriter for short bursts

```python
def typewrite(console: Console, text: str, *, cps: int = 60) -> None:
    """Type text out at `cps` characters per second."""
    delay = 1.0 / cps
    for ch in text:
        console.print(ch, end=""); console.file.flush(); time.sleep(delay)
    console.print()
```

Use sparingly — once per session at most. Boot wordmark cascade is a
typewriter at the letter level (5 letters in ~550 ms).

### Cursor save/restore for fixed-position UIs

```python
sys.stdout.write("\x1b[s")              # save cursor
# ... draw the static frame ...
sys.stdout.write("\x1b[u")              # restore — next print goes back to user line
```

Used by the boot bar (rows 14–18 are fixed; everything else scrolls normally).

### Rich `Live` for streaming panels

```python
from rich.live import Live
from rich.panel import Panel
from rich.text import Text

with Live(Panel("⟨◇⟩  Booting agents…", border_style="#FF38D1"), refresh_per_second=12) as live:
    for i, role in enumerate(roles):
        body = "\n".join(f"  {'✓' if j <= i else '○'}  {r}" for j, r in enumerate(roles))
        live.update(Panel(body, border_style="#FF38D1"))
        time.sleep(0.12)
```

---

## 5. Telegram animations & layouts

### Animated thinking placeholder

```
⟨◈⟩  _tuning the oscillators…_
   ↓ (after a few seconds, swaps subtitle)
⟨◆⟩  _drafting a sharper answer…_
   ↓ (when a tool fires)
⟨◇⟩  _ripgrepping the workspace…_
```

`parse_mode=MARKDOWN`, edited every 600 ms by the placeholder message.

### Streaming agent reply

Each render shows: any tool-call lines + the partial text. Edits are
throttled to `cfg.gateway.telegram.edit_throttle_ms` (default 400ms) so
Telegram's rate-limiter is happy:

```
⚡ `web_extract(url='stripe.com/docs/api')` ✓ 1.2s
⚡ `gmail_search(query='from:alice newer_than:7d')` ✓ 0.6s

Three things —

1. Alice's last message landed Tuesday at 14:02; subject "Q4 roadmap".
2. The Stripe doc you cited authenticates via `Authorization: Bearer <secret_key>`.
3. ...
```

### Reaction system

- 👀 on receive (thinking)
- ✓ on success
- ✗ on error

### Bot command menu (BotFather `setMyCommands`)

Registered at gateway startup so the `/` autocomplete shows ARGUS commands,
not generic ones:

```
/start         Show the welcome panel
/help          How ARGUS works
/status        Provider, model, toolsets, voice config
/report        Full status report (ARGUS + AgentBrain + Momento + Wire)
/clear         Clear this conversation (keeps memory)
/clearmemory   Wipe MEMORY.md + USER.md only (keeps keys & OAuth)
/voice         Voice reply mode: off | voice_only | all
/connect       How to link Gmail, Twitter, LinkedIn, etc.
```

### Voice reply UX

When user sent a voice note (`is_voice_input=True`), TTS reply fires
**after** the text answer:
- OGG/Opus → `send_voice` (round playable bubble)
- MP3 → `send_audio` (lameenc fallback when ffmpeg missing)
- Never WAV. Never raw file.

---

## 6. Agent spawning effects

When `constellation` or `delegate_task` fires, render a "spawn" reveal so
the user sees the team form. Each role appears as a 1-row cell:

```
  ▲ ARCHITECT    ◐ SCOUT       ⚙ ENGINEER    ◇ CARTOGRAPHER
  ◉ AUDITOR      ✚ MEDIC       ✦ SYNTHESIZER
```

Animation: print all cells dim. Then light them up one at a time, 115 ms
apart, in their assigned colors:

```python
ROSTER = [
    ("▲", "ARCHITECT",    "#FF38D1"),   # magenta — planning
    ("◐", "SCOUT",        "#42E8F5"),   # cyan    — recon
    ("⚙", "ENGINEER",     "#FFC247"),   # gold    — building
    ("◇", "CARTOGRAPHER", "#42E8F5"),   # cyan    — mapping
    ("◉", "AUDITOR",      "#FF38D1"),   # magenta — challenge
    ("✚", "MEDIC",        "#FFC247"),   # gold    — repair
    ("✦", "SYNTHESIZER",  "#42E8F5"),   # cyan    — compose
]
```

### Per-spawn micro-animation

When a single sub-agent kicks off, show a quick 3-frame pop in the chat:

```
   ◇  Scout spawning…
   ◈  Scout online.
   ◆  Scout ▸ "trawling the docs"
```

40 ms between frames, then settle into the standard tool-status line.

---

## 7. Multi-agent orchestration screens (Constellation)

Constellation runs are visible work. The user should always see WHO is
doing WHAT, AT WHAT STAGE, and HOW MUCH BUDGET is left.

### The Constellation HUD (proposed for `argus constellation` command)

```
⟨◇⟩  CONSTELLATION  ·  goal: "Compare three vector DBs"          53s · 12,340 tokens · 41% of budget
═══════════════════════════════════════════════════════════════════════════════════════════════

  PHASE 1 — DECOMPOSE                                                       ✓ done · 1.1s
    ▲ Architect          ▸ produced 4 sub-tasks                              

  PHASE 2 — EXECUTE                                                          ⚡ running
    ◐ Scout · pgvector   ▸ "trawling docs"          [█████████░░░░] 71%      4.8s
    ◐ Scout · weaviate   ▸ "fetching benchmarks"    [██████████░░░] 78%      4.2s
    ◐ Scout · qdrant     ▸ "reading rust impl"      [██████░░░░░░░] 47%      3.9s

  PHASE 3 — VERIFY                                                           ○ pending
    ◉ Auditor x3                                                              (will fire as
                                                                              each Scout completes)

  PHASE 4 — SYNTHESIZE                                                       ○ pending
    ✦ Synthesizer
```

Each row uses a different bar style (see §11) so the eye can find its row.

### Tournament voting view

When multiple Scouts disagree, the Auditor panel renders as a 3×N grid:

```
                    ◉ Correctness   ◉ Cost          ◉ Latency
─────────────────────────────────────────────────────────────
  pgvector             ✓ confirmed    ✓ confirmed    ◐ uncertain
  weaviate             ✗ refuted      ◐ uncertain    ✓ confirmed
  qdrant               ✓ confirmed    ✓ confirmed    ✓ confirmed   ◀ majority winner
```

### Self-healing event

When a sub-task fails and the Medic kicks in:

```
  ✚ Medic · diagnosing failure in Scout-qdrant…
    Diagnosis: search query was too broad (got 4 conflicting sources).
    Fix:        narrow to "qdrant filtered HNSW vs flat" and re-run.
    ✓ Healed → Scout-qdrant re-running with adjusted scope.
```

Magenta-bordered panel; lasts on screen even after the run completes
so the user knows healing happened.

---

## 8. Research mode UI

A "research run" = a Constellation focused on information gathering. The
HUD foregrounds **sources** and **confidence**:

```
⟨◇⟩  RESEARCH MODE  ·  "is Mistral Large competitive with GPT-4o for tool calling?"
─────────────────────────────────────────────────────────────────────────────

  SOURCES GATHERED (12)                                            CONFIDENCE
  ───────────────────                                              ──────────
  📄 mistral.ai/news/mistral-large-2          ✓ verified           ████████░░ 80%
  📄 artificialanalysis.ai/models/mistral-…   ✓ verified           █████████░ 90%
  📄 reddit.com/r/LocalLLaMA/comments/…       ✗ refuted (anecdote) ██░░░░░░░░ 20%
  📄 arxiv.org/abs/2410.xxxxx                 ◐ uncertain          ████░░░░░░ 40%

  CITATIONS USED IN FINAL ANSWER:  5 of 12
  ────────────────────────────────────────
  Lower-bound claim: Mistral Large ≈ GPT-4o on BFCL benchmark.    [src: 1, 2]
  Caveat: Mistral lags on multi-step function chaining.            [src: 5]
```

Color-by-confidence: green ≥ 80%, gold 50–79%, magenta < 50%.

---

## 9. Reasoning mode UI

When using a reasoning model (o-series, DeepSeek-R1, Claude with extended
thinking), surface the chain-of-thought collapsed by default:

```
⟨◇⟩  REASONING  ·  model: deepseek-reasoner                       ▼ expand

  ✦ Final answer:
    The optimal vector DB for your case is pgvector + IVFFlat.

  ▶ Thinking (847 tokens, 14.2s) — click ▼ to expand
```

Expanded:

```
  ▼ Thinking (847 tokens, 14.2s)
    ─────────────────────────────
    Step 1: User has Postgres already → strong preference for pgvector.
    Step 2: Query volume is 50/s → IVFFlat sufficient, HNSW overkill.
    Step 3: Recall requirement is 0.95 → IVFFlat with nprobe=10 hits it.
    Step 4: Verify against the QDrant comparison Scout returned earlier.
    Conclusion: pgvector + IVFFlat at nprobe=10.
```

Reasoning trace styled in `#7A7A7A` (dim), final answer in `#F5E6C8` (beige).

---

## 10. Tool execution UI

### Inline (chat REPL + Telegram)

```
⚡ `web_extract(url='stripe.com/docs/api')`…
   ↓ (when complete, in-place edit)
⚡ `web_extract(url='stripe.com/docs/api')` ✓ 1.2s · 3,450 chars
```

- `⚡` in cyan
- Tool name + args in backtick-styled (magenta) on cyan background
- `…` while running, replaced by `✓ <duration> · <size>` on done

### Tool failure

```
⚡ `web_extract(url='broken.example.com')` ✗ 3.0s
   ERROR: HTTPError: 503 Service Unavailable
   ↻ Medic retry queued.
```

Failure line in `#FF5C5C`. Retry line in `#FFC247` if Medic handles it.

### Approval-required commands

```
⚡ `run_command(command='rm -rf /tmp/old-cache')`  DENIED
   The user must type EXACTLY in this chat:
       /approve rm -rf /tmp/old-cache
   …then re-ask me to run it.
```

Magenta-bordered panel so it can't be missed.

---

## 11. Progress bars — 12 styles

Pick by **what the bar represents**, not by mood:

| # | Style | When to use | Example |
|---|---|---|---|
| 1 | **Smooth blocks** | Linear progress (file upload, install) | `█████████░░░░░░░░░░░  45%` |
| 2 | **Tall bars** | Vertical-space-constrained (status bar) | `▮▮▮▮▮▯▯▯▯▯  50%` |
| 3 | **Diamonds** | Anything brand-aligned (boot, swarm) | `◆◆◆◆◆◇◇◇◇◇  50%` |
| 4 | **Squares (filled / open)** | Roster wake-up (5-of-7 agents online) | `■■■■■□□  5/7` |
| 5 | **Wire / hatch** | Subtle bg progress (idle preloads) | `═══════════─────────`  |
| 6 | **Pattern shift** (current) | Active processing (multiple layers) | `▓▓▓░░░░ → ▒▓▓▓░░ → ░▒▓▓▓` |
| 7 | **Arrow chase** | Multi-stage agent runs | `[► ► ► · · ·]` |
| 8 | **Pixel scanline** | "Pulse" effect; ambient | `▏▎▍▌▋▊▉█▉▊▋▌▍▎▏` |
| 9 | **Comet / trail** | Long jobs with unknown ETA | `····●═══════════` |
| 10 | **Dotted segment** | Token streaming counter | `● ● ● ● ◌ ◌ ◌ ◌  4/8 chunks` |
| 11 | **Stepper** | Phase-based work (research → verify → synthesize) | `[●]─[●]─[○]─[ ]` |
| 12 | **Marquee** | Indeterminate (network call out) | `┃     ◀▶     ┃` (sliding) |

Each gets a colored fill that gradients magenta → gold for filled
positions, dim for empty. Code skeleton:

```python
def gradient_bar(progress: float, width: int, fill: str, empty: str) -> str:
    filled = int(width * progress)
    out = ""
    for i in range(width):
        ch = fill if i < filled else empty
        if i < filled:
            t = i / max(1, width - 1)
            r, g, b = interp(MAGENTA_RGB, GOLD_RGB, t)
            out += f"{_bold()}{_rgb(r,g,b)}{ch}{_reset()}"
        else:
            out += f"{_rgb(*DIM_RGB)}{ch}{_reset()}"
    return out
```

---

## 12. ASCII dashboards

### `argus brain` — memory dashboard

```
⟨◇⟩  MEMORY  ·  brain dashboard                                          /brain

  USER.md           ████████████░░░░░░░░░░░░░░░░░░  4.2 KB / 32 KB
  MEMORY.md         ██████░░░░░░░░░░░░░░░░░░░░░░░░  2.0 KB / 32 KB
  AgentBrain        ░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░  in-memory (ephemeral)
  AgentMomento      ██████████████░░░░░░░░░░░░░░░░  8 skills indexed
  ─────────────────────────────────────────────────────────────────────────
  RECENT ENTITIES   alice@he2.ai  ·  Stripe API  ·  Q4 roadmap
  TOP SKILLS        github-pr-review (12)  ·  morning-brief (8)
```

### `argus tools` — toggle screen

```
⟨◇⟩  TOOLSETS                                                            /tools

  [✓] memory          — read/write MEMORY.md + USER.md
  [✓] web             — web_search · web_fetch · web_extract · arxiv
  [✓] files           — read_file · write_file · patch · search_files
  [✓] code            — execute_code (sandboxed Python)
  [✓] mail            — gmail_send · gmail_search · gmail_read · …
  [ ] shell           — run_command (single-use approval required)
  [✓] vision          — vision_analyze
  [✓] voice           — text_to_speech (Supertonic default)
  [✓] todo            — session task list
  [✓] clarify         — ask user a question (CLI only)
  [✓] delegation      — delegate_task · orchestrate · constellation

  ↑↓ navigate · space toggle · enter save · esc cancel
```

### `argus doctor` — health checks

```
⟨◇⟩  DOCTOR  ·  12 checks                                                /doctor

  ✓  Python version           3.13.12
  ✓  uv + venv                ~/Library/Caches/argus/venv
  ✓  Default provider auth    Groq · key valid · llama-3.3-70b
  ✓  Embedding fallback       openai/text-embedding-3-small
  ✗  ffmpeg                   missing — TTS uses MP3/lameenc fallback
  ✓  TTS (supertonic)         model loaded · 99M params
  ✓  Telegram token           @my_argus_bot
  ✓  Telegram allow-list      1 user
  ✓  Telegram gateway         ✓ running (PID 47284)
  ✓  Disk space               54 GB free on $HOME
  ✓  Memory perms             ~/.argus/.env is 0600
  ✓  Network egress           api.groq.com, api.telegram.org reachable

  1 warning · 0 failures
```

---

## 13. Cyberpunk HUD layouts

For the `argus constellation` run-view and `argus brain --hud` mode,
go full HUD. Borrow from sci-fi UI work but stay readable.

### The "Combat HUD" (proposed)

```
╔════════════════════════════════════════════════════════════════════════════╗
║  ⟨◇⟩  ARGUS · CONSTELLATION CONTROL                          uptime 14h 22m ║
╠════════════════════════════════════════════════════════════════════════════╣
║                                                                            ║
║  ┌─ TOKENS ────────────┐  ┌─ ROLES ────────────┐  ┌─ AUDIT ──────────────┐ ║
║  │ ████████░░░░  41 %  │  │ ▲ ◐ ◐ ⚙ ◉ ✚ ✦      │  │ ✓ 7  ◐ 2  ✗ 1        │ ║
║  │ 12,340 / 30,000     │  │ 4 active · 3 done  │  │ confirmed/uncertain  │ ║
║  └─────────────────────┘  └────────────────────┘  └──────────────────────┘ ║
║                                                                            ║
║  ┌─ ACTIVITY ───────────────────────────────────────────────────────────┐  ║
║  │ 14:22:01  ✦ Synthesizer · drafting final answer                       │ ║
║  │ 14:21:58  ◉ Auditor-3   · ✓ confirmed pgvector recall claim          │ ║
║  │ 14:21:57  ◐ Scout-3     · finished (4.2s, 2,103 tokens)              │ ║
║  │ 14:21:51  ✚ Medic       · healed Scout-2 failure                      │ ║
║  │ 14:21:48  ◐ Scout-2     · ✗ HTTPError 503                            │ ║
║  └───────────────────────────────────────────────────────────────────────┘  ║
║                                                                            ║
║                                       press q to quit · enter for detail   ║
╚════════════════════════════════════════════════════════════════════════════╝
```

Box-drawing chars: `╔═╗║╚╝┌─┐│└┘`. Borders in magenta. Section headers
in gold. Body in beige. Activity log uses muted cyan timestamps so it
recedes visually.

### Rule: HUDs always start with a clear screen

`\x1b[2J\x1b[H` at the top of the render loop. Diff-only updates inside
each box via cursor positioning.

---

## 14. Agent cards

A compact card style for each role, used in the Constellation HUD and
the splash roster:

```
  ┌─ ◐ SCOUT-2 ────────────────────┐
  │ task: "trawl Mistral docs"     │
  │ status:    running             │
  │ elapsed:   3.9s                │
  │ tokens:    1,840 / 6,000       │
  │ tools:     web_extract (3)     │
  │ ─────────────────────────────  │
  │ "found 4 sources, ranking…"    │
  └────────────────────────────────┘
```

States:
- `pending` — dim border, dim icon
- `running` — magenta border, cyan icon pulse
- `done` — gold border, gold ✓ before name
- `failed` — red border, red ✗
- `healed` — gold border, gold ↻ before name

Three cards fit comfortably side-by-side at 120 cols; stack vertically
below that.

---

## 15. Live token counters

In the bottom-right corner of the chat REPL (right-prompt), show:

```
  ▕ in 2,341  ·  out 1,840  ·  ctx 28%  ·  $0.0042 ▏
```

- `in/out`: tokens this session
- `ctx %`: percentage of the model's context window used by the active conversation
- `$`: cost estimate (computed from provider price tables in `argus/data/providers.py`)

Color rules:
- ctx 0–60%: cyan (healthy)
- ctx 60–85%: gold (watch)
- ctx 85–100%: magenta (compress or `/new`)

Lookup is cheap — just maintain `state.tokens_in/out` and compute on
prompt redraw. Cost figures live in a per-provider price dict.

---

## 16. Streaming response animations

### Token-by-token typewriter (current)

```python
async for evt in run_turn(cfg, convo, user_text):
    if isinstance(evt, TextEvent):
        console.print(evt.text, end="", style="beige")
        console.file.flush()
```

Beige body, no syntax highlighting until the stream completes (avoids
flicker on partial code blocks).

### "Compose then reveal" for short answers

If the entire response is < 280 chars (one Telegram-message-equivalent),
buffer it, then reveal at a typewriter pace (60 cps) for impact. Anything
longer streams live.

### Code block special-casing

When the model emits a fenced code block, switch the streaming color to
`#42E8F5` (cyan) for the fence-open line, then dim for the body, and
re-syntax-highlight ONCE on close. Half-rendered Python with rainbow
colors is unreadable.

---

## 17. Themes

ARGUS ships two complete visual themes. User picks one via
`argus config set ui.theme retro|modern` (proposed setting).

### Theme: **Retro Pixel** (default)

- Pixel-art glyph and wordmark (current `argus/splash.py` + `argus/banner.py`)
- Block-letter ARGUS in magenta→gold gradient
- 8-bit chiptune chimes (current `argus/chime.py`)
- All status indicators use unicode `◇ ◆ ◈ ▲ ▼ ◐ ●`
- Borders: `═ ║ ╔ ╗ ╚ ╝` (double-line)
- Backgrounds always default to terminal bg (#050505 if it's a real dark theme)

### Theme: **Modern AI Operations Center**

- Sans-style: glyph rendered as a small filled square `■`
- Wordmark in Inter-style ASCII (lighter weight, more whitespace)
- Sounds: short white-noise sweeps instead of chiptunes (still ≤ 600 ms)
- Status icons: `● ◐ ○ ✓ ✗`
- Borders: `─ │ ┌ ┐ └ ┘` (single-line)
- Subtler animations — 150 ms frame rate instead of 70

Switching themes does not change keyboard shortcuts, slash commands, or
the underlying flow. Only visuals.

---

## 18. Loading sound design

### Existing chime library (`argus/chime.py`)

| Name | Notes | Mood | When |
|---|---|---|---|
| `sealed` | C5 → E5 → G5 → C6 held | confident close | clean `/exit` |
| `standdown` | A5 → E5 → A4 held | watchful descent | shutdown signal |
| `logout` | three B5 pulses + E6 cap | brisk | timeout exit |
| `fadeout` | E5 → G#4 long | melancholy | error exit |

All pure-Python (numpy square waves, soft envelope), ≤600 ms, 22 kHz mono,
played non-blocking via `afplay` / `aplay` / `paplay`.

### Recommended additions

| Name | Notes | When |
|---|---|---|
| `boot_seal` | C4 → C5 → G5 (rising fifth-octave) | startup splash, fires on first banner draw |
| `swarm_form` | tritone arpeggio (C-F#-C) | Constellation spawns |
| `audit_pass` | single G6 high pip | each ✓ from an Auditor |
| `audit_fail` | descending C5-G4-C4 cluster | each ✗ from an Auditor |
| `medic_heal` | two-note F5-C6 lift | Medic successfully repairs |
| `tool_done` | brief B5 click | each tool completion (off by default — opt-in via `ui.tool_chime=on`) |
| `message_in` | C6 pip | new Telegram message arrives (terminal mode only) |

All under 200 ms except the seals. Implement by adding entries to
`_CHIMES` in `argus/chime.py` and calling `play_exit_chime(chime="…")`
at the relevant lifecycle hook.

### Volume + accessibility

- `ARGUS_NO_CHIME=1` silences everything (already implemented).
- New: `ARGUS_CHIME_VOLUME=0.0..1.0` — scale the soft-clip multiplier in `_render_or_cache`.
- Always respect macOS system DND state when available (`defaults read com.apple.notificationcenterui doNotDisturb`).

---

## 19. Mac app splash specifications

### Icon (current — `desktop/assets/icon.png`)

- 1024×1024 PNG, ad-hoc signed via `codesign --sign -`
- Rounded square with magenta→cyan gradient border
- Pixel-art glyph centered: magenta brackets, gold diamond, cyan dashes
- Generated `.icns` covers all macOS sizes (16, 32, 64, 128, 256, 512, 1024 + @2x)

### App splash (first-launch installer)

When `Argus.app` opens for the first time, we spawn a Terminal window
running the installer. The installer prints its own splash:

```
  ╭──────────────────────────────────────────╮
  │  ⟨◇⟩  ARGUS Desktop Installer            │
  ╰──────────────────────────────────────────╯
   — the watchful agent that grows with you —

  [→]  Homebrew not found — installing…
  [✓]  Homebrew ready
  [→]  Python 3.11+ ready (python3.13)
  [→]  uv ready
  [→]  ffmpeg ready
  [→]  Syncing ARGUS dependencies (~3 min on first run)…
  [✓]  ARGUS self-test passed: argus 0.1.0
```

Brand colors via raw ANSI escapes (we can't assume the user's Terminal
profile is dark). Always cyan steps `[→]`, gold success `[✓]`, red errors `[✗]`.

### Subsequent launches

The Mac app launches a Terminal window with the **"Argus" Terminal
profile** (auto-installed by `install-terminal-profile.sh`):
- Window: 120 × 40 cells
- Font: Menlo 14pt
- Background: `#050505`
- Foreground: `#E6E6E6`
- Cursor: `#FF38D1` (magenta) bar, blinking off
- Then runs `exec argus` so Cmd-W closes the agent cleanly.

### Future: native SwiftUI splash

When we ship a real native window (xterm.js + WKWebView + node-pty), the
splash should be:
1. 600×400 borderless dark window, opacity 0 → 1 fade-in over 200ms
2. Centered pixel-art glyph + wordmark (same as splash.py but rendered
   to canvas instead of ANSI)
3. Auto-dismiss after 1.6s, the terminal view fades in beneath

---

## 20. Cursor-style and Claude Code-style interfaces, adapted

### Things ARGUS already does that match Cursor / Claude Code

- Streaming responses with throttled UI updates
- Tool-call surfacing (Claude Code shows `⏺ <tool>(args)`; ARGUS shows `⚡ <tool>(args)`)
- Slash-command palette
- Color-coded user vs. assistant turns (Cursor: blue/white; ARGUS: white/beige)
- File-attach via paste (`/attach <path>` or auto-detect)

### Things to borrow

- **Claude Code's `⏵ Bash(...)` collapsed view**: long tool output should
  collapse to a one-line summary by default, expand on click/keypress.
  In ARGUS: prefix collapsed lines with `▶`, expand on `e` keypress or
  show first 8 lines + `…` for the rest.

- **Cursor's command bar**: persistent bottom strip showing
  `⏎ send · ⌃C cancel · ⌃R retry · ⌘K menu`. Port to ARGUS as a
  prompt_toolkit bottom toolbar:
  ```
  ▕ ⏎ send  ·  ⌃C cancel  ·  /help all commands  ·  ⟨◇⟩ argus 0.1.0 ▏
  ```

- **Claude Code's todo list rendering**: indented checkboxes that update
  in place. ARGUS already has the `todo` tool — render output as:
  ```
  ⟨◇⟩  TODO
    ○ #1  Wire Mistral validator into setup
    ✓ #2  Add backup model prompt
    ○ #3  Write design system doc
  ```

- **Cursor's "preview diff" before apply**: when `write_file` / `patch`
  changes existing files, render a unified diff in cyan/red before the
  write commits. Already supported in shell tools; surface in chat UI.

### Things to deliberately NOT borrow

- Cursor's heavy chrome (sidebars, file tree) — ARGUS is a CLI. Keep the
  chrome to a single bottom strip.
- Claude Code's verbose tool output by default — ARGUS collapses by
  default to keep the chat scannable.

---

## 21. Anti-patterns

| Don't | Do |
|---|---|
| Use spinning ASCII (`/ - \ |`) for every status | Use the diamond rotation `◇◈◆◈` — it's ours |
| Render colors at >5 hues per screen | Stay at 3 brand colors + dim/err |
| Frame rates faster than 50 ms | 70–115 ms is the floor |
| Emoji as filler ("Loading... 🤔") | Earn each emoji; sigil ⟨◇⟩ is the only free one |
| Status messages like "Processing..." | Tool-aware verb-phrases: "ripgrepping the workspace" |
| Magenta and cyan touching at small sizes | Separate with gold dashes / dim spaces |
| Hardcoded widths | Read `os.get_terminal_size()` and center |
| Cursor-up overprinting (`\x1b[<N>A`) | Absolute positioning (`\x1b[r;cH`) — scroll-safe |
| Sounds longer than 600 ms | Brief is brand. No looping ever. |
| `print()` in tight animation loops | Buffered `sys.stdout.write()` + explicit flush |
| Breaking on non-TTY | Always check `isatty()` first; degrade gracefully |
| Forgetting `_show_cursor()` in a finally block | Always restore in `finally` — orphaned hidden cursors are a bug magnet |
| Reading the user's clock at run time inside animation | Compute frame timing from a monotonic start once, not via wall-clock per frame |

---

## Appendix A — Quick-start patterns

### Spawn a Constellation from your own code

```python
from argus.swarm import Constellation
from argus import config

cfg = config.load()
constellation = Constellation(
    goal="Compare pgvector, Weaviate, and Qdrant for our use case.",
    cfg=cfg,
    budget_tokens=30_000,
    timeout_seconds=240,
    max_parallelism=5,
)

# Streaming UI events:
async for evt in constellation.stream():
    handle(evt)              # RoleStarted, RoleFinished, ClaimRaised, ClaimVerdict, BudgetTick
result = constellation.result   # ConstellationResult with .answer, .confirmed_outputs, …
```

### Play a chime at a custom lifecycle hook

```python
from argus.chime import play_exit_chime
play_exit_chime(chime="sealed", blocking=False)   # non-blocking; safe in any context
```

### Render a centered banner of your own

```python
from argus.banner import wordmark
from rich.console import Console

Console().print(wordmark("DEPLOY"))   # block letters + magenta→gold gradient
```

### Force MP3 (Telegram) instead of OGG (when you don't have ffmpeg)

```python
from argus.voice import synthesize
result = await synthesize("Sentinel offline.", output_format="mp3")
# Goes through lameenc fallback — no ffmpeg required.
```

---

## Versioning

This document tracks the design system version, not the codebase version.

| Version | Date | Major changes |
|---|---|---|
| **v1** | 2026-05-31 | First full Design System extracted from the Animations Guide. Covers boot/exit splash, Constellation HUD, 12 progress bar styles, chime catalog, Mac app splash, theme system, anti-patterns. |

Successor versions land here when the visual contract changes — not
when a single screen gets tweaked. Keep this stable; the implementation
files are where the iteration happens.
