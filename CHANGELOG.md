# Changelog

All notable changes to ARGUS. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Changed (mobile app)
- **Full UI redesign** in Claude/ChatGPT style — soft conversation-first.
  - Larger rounded message bubbles with proper padding
  - Animated typing-dots indicator while streaming
  - Per-message timestamps
  - Empty states with personality (large sigil + helpful copy)
  - Bigger voice orb (220px) with concentric expanding rings
  - File cards with colored type-specific icon backgrounds
  - iOS-style toggle switches in settings
  - Bottom tab bar uses outlined icons when inactive, filled when active
- **New brand assets** programmatically generated:
  - 1024×1024 icon: chevron brackets `⟨ ◇ ⟩` with hollow gold diamond
  - 1284×2778 splash: sigil + ARGUS wordmark + 4×4 agent grid + progress bar
  - Adaptive icon trio for Android 13+ (foreground/background/monochrome)
- **expo-splash-screen** (SDK 56) properly configured with fade-out animation.

### Added
- **Auto-update fahrenheit-research embedded modules**. `argus update` now
  checks each of `agentwire`, `agentbrain`, `agentmomento` against their
  upstream HEAD SHA via `git ls-remote`, re-vendors any that drifted,
  preserves our integration `__init__.py`, and writes a `.vendor.json`
  marker with the new SHA. Existing test suite must pass after — otherwise
  the whole update auto-rolls back.
- **Version manifest** at `~/.argus/version.json` — tracks last 20 updates
  with backup IDs and module SHAs.
- **Branded update animation** — magenta-diamond spinner, 5-step progress
  ("Step 1/5 — checking your ARGUS repo…").
- `bootstrap.sh` now installs `ffmpeg` via Homebrew when missing (silenced
  the "ffmpeg missing" warning in `argus doctor`).
- React error boundary in the Android app — any render error surfaces a
  diagnostic screen instead of a blank crash.
- **`argus update` + `argus rollback`** — safe in-place updates with auto-rollback.
  - Snapshots source tree to `~/.argus/backups/<timestamp>/` before pulling.
  - Runs the test suite after `uv sync`; rolls back automatically if anything breaks.
  - `argus rollback` restores the most recent snapshot; `--pick` lets you choose.
  - Safety-net snapshot taken before every rollback so the undo is reversible.
  - Docs: [docs/update.md](docs/update.md).
- **Natural-language cron** — `argus cron add` now accepts phrases like
  `"every weekday at 9am"`, `"every 30 minutes"`, `"daily at 7pm"`.
  - Conversational follow-ups when input is ambiguous ("what time of day?").
  - Detects local timezone automatically via `tzlocal`.
  - Previews the next 3 fire times in your local TZ before saving.
  - Raw cron expressions still work for power users.
- **ARGUS Vault** (`argus vault`) — local-first SQLite + vector memory store.
  - `remember` / `recall` / `forget` / `list` / `stats` subcommands.
  - Default embedder: SentenceTransformers all-MiniLM-L6-v2 (local, 86 MB, 384-dim).
  - Optional: OpenAI text-embedding-3-small/large via `ARGUS_VAULT_EMBED`.
  - Vector index: `sqlite-vec` native extension with numpy fallback.
  - Single SQLite file at `~/.argus/vault.db` — back up by copying one file.
- **ARGUS Cron** (`argus cron`) — persistent scheduled jobs with swarm awareness.
  - `add` / `list` / `remove` / `enable` / `disable` / `run` / `logs` / `daemon` / `install-service`.
  - Multi-task prompts auto-route through Constellation swarm.
  - Per-job swarm mode: `auto`, `always`, `never`.
  - launchd plist (macOS) or systemd user unit (Linux) generators.
  - Storage: `~/.argus/cron.db` (SQLite). Job history kept for `argus cron logs`.
- **HTTP+WebSocket API** (`argus serve`) — FastAPI bridge for the mobile app.
  - REST: `/chat`, `/health`, `/sessions`, `/memory`, `/tools`, `/files`, `/upload`.
  - WebSocket: `/chat/stream` for token-by-token streaming.
  - Swagger docs at `/docs`.
- **Android mobile app** — React Native (Expo) client.
  - 7 screens: Splash, Setup, Chat, Voice, Files, Sessions, Memory, Settings.
  - Brand-themed (Electric Magenta / Acid Gold / Ice Cyan).
  - Streaming chat over WebSocket; voice mode via on-device STT + TTS.
  - APK build via EAS or local Gradle.
  - App source lives under `android/` (excluded from CLI git repo).
- Auto-linking of URLs and email addresses in CLI input and rendered output
  (Ice Cyan, underlined, OSC 8 clickable in modern terminals).

### Changed
- Voice HUD popup: status badge now pinned at top — never scrolls away.
  Bigger labels: `● LISTENING — speak now`, `▸ ARGUS TALKING`, `◆ THINKING…`.
- Voice interrupt detection: two-phase listener — strict threshold (4500 RMS)
  during TTS playback to ignore speaker echo, relaxed (2200 RMS) between
  sentences. First 800 ms of any reply is grace-protected.
- Voice popup close → immediately kills the in-flight TTS subprocess
  (was up to 200 ms latency before).
- Cron-fired prompts and voice-fired prompts both register all default tools
  before invoking the agent loop (previously voice mode missed web search).
- PDF generation: WeasyPrint pre-check via ctypes — silent fallback to
  ReportLab if libgobject/Pango missing (no more noisy stderr banner).

### Fixed
- **Android APK crash on launch** — added missing `babel.config.js` with the
  `react-native-reanimated/plugin`, `metro.config.js`, `expo-system-ui`, and
  `react-native-worklets`. Without these the bundle crashed on the first
  animated value (SplashScreen breathing glyph).
- **Voice reply cut off after one sentence** — removed the mic-based
  interrupt monitor entirely. Speaker echo unavoidably tripped it; result
  was the user hearing only the first sentence. Tradeoff: barge-in via
  spoken "Argus stop" no longer works mid-sentence — use the HUD close
  button or `End Argus` instead. (To bring barge-in back, the right solution
  is acoustic echo cancellation via CoreAudio's voice-processing audio unit
  — anything less is unreliable.)
- **HTML/URL noise read aloud** — `_clean_for_tts` now strips `<script>`,
  `<style>`, bare HTML tags, and `https://...` URLs before sending to the
  TTS engine. Fixes the "ARGUS read out a Google Fonts URL" bug.
- **HUD popup showing raw HTML in transcript** — agent output passed through
  `_clean_for_tts` before being broadcast to the HUD socket.
- **Voice tool calls invisible in logs** — `_agent_turn_streaming` now
  counts and logs ToolCallEvents so failures are diagnosable.
- `argus talk` is now a proper CLI command (was previously only a chat-trigger phrase).
- `_URLLexer` in chat REPL no longer crashes on bare `re` reference (was
  imported as `_re` at module level).
- `argus` shim no longer wipes the `voice`/`voice-wake`/`office`/`vault`/`cron`
  extras on every invocation (was calling `uv run` which re-syncs).
- Spammy `socket.send() raised exception` warning suppressed via asyncio
  log filter — that path is already handled cleanly via EOF reader.
- Binary file `read_file` calls now return a helpful "BINARY FILE" message
  instead of crashing with `UnicodeDecodeError`.

## [0.1.0] - 2026-05-31

Initial public release.

### Highlights
- Multi-provider agent loop: Groq, OpenAI, Anthropic, Mistral, OpenRouter.
- Telegram gateway.
- Tool suite: web search, web research, scribe (DOCX/PDF), ledger (XLSX/CSV),
  Gmail, file ops, shell, delegation, vision, voice synthesis.
- Constellation multi-agent swarm with 14 roles (Architect, Scout, Engineer,
  Cartographer, Auditor, Medic, Synthesizer, Scribe, Ledger, Atelier,
  Herald, Analyst, Sentinel, Momento).
- AgentBrain (orchestration), AgentWire (envelope schema), AgentMomento
  (skill memory).
- Hands-free voice mode with British male Jarvis voice, wake-word detection
  (`hey_argus` / `hey_jarvis`), faster-whisper STT, interruptible TTS.
- Voice HUD popup window with brand-colored waveform animations.
- Custom-trained wake-word support (notebook in `desktop/wake_word_training/`).
- Branded CLI: Sentinel Glyph `⟨ ◇ ⟩`, gradient block banner, slash commands.
