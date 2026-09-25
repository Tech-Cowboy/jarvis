# JARVIS

A classic Python voice assistant, rebuilt with today's open-source parts.

```
 "hey Jarvis"        speech                 text                  tools                 voice
 ────────────►  ──────────────►  ───────────────────►  ─────────────────►  ──────────────────►
 openWakeWord    faster-whisper    LLM with tool calling   skills (open apps,   ElevenLabs
 (free model)    (local, offline)  Anthropic / OpenAI /    web search, sites,   (or macOS `say`)
                                   Ollama / keyword mode   files, timers, ...)
```

Say **"hey Jarvis, open Spotify"**, **"hey Jarvis, what's the weather in Daly City"**, **"hey Jarvis, set a timer for
ten minutes"** and it does it, out loud, in your ElevenLabs voice. Everything runs on your Mac except the two paid APIs you
choose to plug in (the LLM and the voice), and both have free fallbacks.

## Lineage

This follows the classic open-source Python JARVIS template that hundreds of GitHub repos and tutorials share:
`speech_recognition` listens, a command router decides, `webbrowser` / `os` act, `pyttsx3` talks. The structure is the same
here (listen, recognize, route, act, speak) with each stage swapped for a modern, still free, open-source component:

| Stage | Classic template | This repo | License |
|---|---|---|---|
| Wake word | none (always listening) | [openWakeWord](https://github.com/dscripka/openWakeWord) pre-trained `hey_jarvis` | Apache-2.0 code, CC BY-NC-SA 4.0 model |
| Speech to text | Google Web Speech via `SpeechRecognition` | [faster-whisper](https://github.com/SYSTRAN/faster-whisper) locally (Google SR still available) | MIT |
| Brain | `if "open" in command:` keyword matching | LLM tool calling (Anthropic, OpenAI or Ollama), keyword mode kept as the free fallback | MIT SDKs |
| Skills | inline functions | `@skill` registry, one module per domain | this repo, MIT |
| Voice | `pyttsx3` | [ElevenLabs](https://elevenlabs.io) streamed PCM, macOS `say` as fallback | MIT SDK |
| Web search | `webbrowser.open("google.com/search?q=")` | [ddgs](https://github.com/deedy5/ddgs) results summarized by the brain, plus the classic "open it in the browser" | MIT |

## Requirements

- macOS on Apple Silicon (tested), Python 3.11 to 3.13 (3.12 recommended; the setup script uses `uv` to fetch it).
  Linux mostly works; Windows needs small changes in `jarvis/skills/apps.py` and `system.py`.
- A microphone. The first run asks for Microphone permission for your terminal app.
- Optional keys, each with a free fallback:
  - `ELEVENLABS_API_KEY` for the voice (fallback: macOS `say`).
  - `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, or a running [Ollama](https://ollama.com) for the brain (fallback: keyword mode).

## Quick start (macOS)

```bash
git clone https://github.com/Tech-Cowboy/jarvis.git
cd jarvis
./scripts/setup_mac.sh        # venv, dependencies, model downloads, doctor
cp .env.example .env          # then put your keys in .env (the script does this if .env is missing)
source .venv/bin/activate
jarvis doctor                 # every stage reports OK / WARN / FAIL with the fix
jarvis                        # voice mode: say "hey Jarvis"
```

Manual install, if you prefer:

```bash
uv venv --python 3.12 .venv && source .venv/bin/activate
uv pip install -e ".[all,dev]"       # or: pip install -e ".[all,dev]"
jarvis download-models               # faster-whisper base.en (~150 MB) and the hey_jarvis wake model (~6 MB)
```

## Commands

| Command | What it does |
|---|---|
| `jarvis` / `jarvis run` | Voice mode: wake word, listen, think, speak. Ctrl-C to stop. |
| `jarvis chat` | Text mode: type requests, read replies. `--speak` also says them aloud. |
| `jarvis ask "open safari"` | One request, one reply, exit. |
| `jarvis say "Good evening, sir."` | Test the configured voice. |
| `jarvis listen` | Record one utterance, print the transcript (tests the mic and Whisper). |
| `jarvis doctor [--online]` | Check every stage; `--online` also calls the LLM and ElevenLabs APIs. |
| `jarvis devices` | List microphones (set `MIC_DEVICE` in `.env`). |
| `jarvis voices` | List the ElevenLabs voices on your account. |
| `jarvis skills` | List the tools the brain can call. |
| `jarvis download-models` | Pre-download the Whisper and wake word models. |

Global flags override `.env` for one run: `--llm`, `--tts`, `--stt`, `--wake`, `-v` (debug logging).
Examples: `jarvis --wake push_to_talk` (press Enter to talk), `jarvis --llm keyword chat` (no API at all),
`jarvis --tts say` (skip ElevenLabs), `jarvis --stt google` (no Whisper download).

## What you can say

- **Apps**: "open Safari", "launch VS Code", "quit Spotify", "what apps are running", "is Figma installed"
- **Web**: "search for Buck Brannaman clinics near me", "what's the latest news about the Fed", "open YouTube",
  "play cowboy dressage on YouTube", "go to github.com", "who is Tom Dorrance" (Wikipedia)
- **Machine**: "what time is it", "set the volume to 30", "mute", "battery", "take a screenshot", "system stats",
  "open my Downloads folder", "find the file called budget 2026", "sleep the display"
- **Memory and timers**: "remember that the gate code is 4412", "what did I ask you to remember", "forget the gate code",
  "set a timer for 12 minutes for the eggs", "how long is left on the timer", "cancel the timers"
- **Session**: "new conversation", "goodbye" / "stop listening"

With an LLM brain the phrasing is free-form and it will chain tools ("find the horse-trailer listing you found earlier
and open it"). In keyword mode the phrases above are matched by pattern, like the original JARVIS scripts.

## Configuration (`.env`)

Copy `.env.example` to `.env`. Keys are read from the environment only; nothing here writes them anywhere.

| Variable | Default | Notes |
|---|---|---|
| `LLM_PROVIDER` | `auto` | `auto` picks Anthropic, then OpenAI, then a running Ollama, then `keyword`. |
| `ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL` | `claude-haiku-4-5-20251001` | Haiku is the fastest for voice; any Claude model with tool use works. |
| `OPENAI_API_KEY`, `OPENAI_MODEL` | `gpt-6-luna` | Any Chat Completions model with function calling. |
| `OLLAMA_MODEL`, `OLLAMA_HOST` | `qwen3:4b` | Needs a tool-capable model (`ollama pull qwen3:4b`). |
| `TTS_PROVIDER` | `auto` | `elevenlabs` when a key is set, else `say` on macOS, else `console`. |
| `ELEVENLABS_API_KEY` | | Your ElevenLabs key. |
| `ELEVENLABS_VOICE_ID` / `ELEVENLABS_VOICE_NAME` | `onwK4e9ZLuTAKqWW03F9` (Daniel) | `jarvis voices` lists yours; a name resolves to an ID at startup. |
| `ELEVENLABS_MODEL` | `eleven_flash_v2_5` | Lowest latency. `eleven_multilingual_v2` for the richest voice. |
| `ELEVENLABS_OUTPUT_FORMAT` | `pcm_24000` | Keep a `pcm_*` format; audio is streamed straight to the speaker, no ffmpeg. |
| `ELEVENLABS_STABILITY` / `_SIMILARITY` / `_STYLE` / `_SPEED` | `0.5 / 0.75 / 0.0 / 1.0` | Voice settings. |
| `TTS_CACHE` | `true` | Caches short phrases (the wake acknowledgement, confirmations) in `~/.jarvis/tts-cache` to save credits. |
| `STT_PROVIDER` | `auto` | `whisper` if faster-whisper is installed, else `google`. |
| `WHISPER_MODEL` | `base.en` | `tiny.en` (fastest), `base.en`, `small.en` (more accurate), `medium.en`, `large-v3`. |
| `WAKE_MODE` | `auto` | `wakeword` (openWakeWord), `name` (always listening, say "Jarvis" in the sentence), `push_to_talk`. |
| `WAKE_THRESHOLD` | `0.5` | Raise toward 0.7 if it triggers on its own, lower toward 0.35 if it misses you. |
| `WAKE_ACK`, `WAKE_ACK_TEXT` | `phrase`, `Yes, sir?` | Or `chime` (a two-tone beep) or `none`. |
| `MIC_DEVICE` | system default | Index or name substring from `jarvis devices`. |
| `MIN_SPEECH_RMS`, `SILENCE_SECONDS`, `MAX_UTTERANCE_SECONDS`, `LISTEN_TIMEOUT_SECONDS` | `0.010, 1.2, 15, 8` | Voice activity tuning. The mic calibrates to room noise at startup. |
| `ASSISTANT_NAME`, `USER_NAME`, `HONORIFIC` | `Jarvis`, empty, `sir` | Persona. |
| `HISTORY_TURNS`, `MAX_TOOL_ROUNDS`, `MAX_TOKENS` | `8, 6, 400` | Conversation memory, tool chaining depth, reply length. |
| `JARVIS_DATA_DIR` | `~/.jarvis` | Notes and the voice cache. |
| `LOG_LEVEL` | `WARNING` | `INFO` shows tool calls and timings; `-v` on the command line is `DEBUG`. |

`.env` is read from the current directory and from `~/.config/jarvis/.env` (so `jarvis` works from any folder).

## Adding a skill

A skill is a function. The docstring becomes the tool description, the annotations become the schema, and the return
string is what the assistant works from. Drop a module in `jarvis/skills/`, add it to `DEFAULT_SKILL_MODULES` in
`jarvis/skills/__init__.py`, and both the LLM brain and `jarvis skills` pick it up.

```python
# jarvis/skills/lights.py
from typing import Annotated
from jarvis.skills.registry import skill

@skill()
def set_barn_lights(state: Annotated[str, "'on' or 'off'"], zone: Annotated[str, "Which lights, e.g. 'arena'"] = "all") -> str:
    """Turn the barn lights on or off."""
    ...  # call your hub here
    return f"Barn lights {state} in the {zone}."
```

Add `ctx: SkillContext = None` as a parameter to reach settings, `ctx.speak(text)` and `ctx.stop_event`. Keyword mode
only knows the built-in patterns; new skills are reachable through an LLM brain (or add a rule in
`jarvis/brain/keyword_brain.py`).

## How it is put together

```
jarvis/
  cli.py            commands (run, chat, ask, say, listen, doctor, devices, voices, skills, download-models)
  config.py         Settings from .env / environment; provider auto-selection
  assistant.py      the loop: wake -> record -> transcribe -> router -> speak
  doctor.py         setup checks
  audio/            mic.py (sounddevice capture + energy VAD), player.py (streamed PCM playback), chime.py
  wake/             oww.py (openWakeWord), simple.py (push-to-talk, always-listening)
  stt/              whisper_local.py (faster-whisper), google_sr.py (SpeechRecognition)
  tts/              elevenlabs_tts.py (streaming + phrase cache), macos_say.py, console.py
  brain/            base.py (neutral Message/ToolSpec types), router.py (tool loop), prompts.py,
                    anthropic_llm.py, openai_llm.py, ollama_llm.py, keyword_brain.py, factory.py
  skills/           registry.py (@skill), apps.py, web.py, system.py, files.py, notes.py, timers.py, control.py
tests/              93 tests, no network, no audio hardware needed:  pytest
scripts/            setup_mac.sh
```

The brain is provider-neutral: the router keeps one history in its own `Message` type and each adapter converts to the
vendor's wire format, so adding a provider is one file. Every skill is exposed to all three LLMs from the same JSON
schema.

## Troubleshooting

- **"No usable input device" or it never hears you**: System Settings > Privacy & Security > Microphone, allow your
  terminal (Terminal, iTerm, VS Code). Then `jarvis devices` and `jarvis listen`.
- **Wake word never triggers**: `jarvis -v` prints scores; try `WAKE_THRESHOLD=0.35`, speak a little slower, or use
  `--wake push_to_talk` to rule out the mic. If it triggers by itself, raise the threshold.
- **It transcribes its own voice**: the mic is flushed after each reply; if your speakers are loud, use headphones or
  `WAKE_ACK=chime`.
- **Slow first response**: the Whisper model loads once at startup (a few seconds); the first ElevenLabs reply takes a
  network round trip, cached phrases are instant. `tiny.en` is faster than `base.en`.
- **ElevenLabs errors**: `jarvis doctor --online` verifies the key and lists voices. Free-tier accounts have a monthly
  character quota; `TTS_CACHE` keeps repeated phrases free.
- **No LLM key**: everything still works in keyword mode (`jarvis --llm keyword`), which is the classic experience.
- **Python 3.14**: some audio wheels lag new Python releases; the setup script pins 3.12 through `uv`.

## Costs and privacy

Wake word detection, speech-to-text and the skills run on your machine. Only the text of what you said (plus tool
results) goes to the LLM provider you chose, and only the reply text goes to ElevenLabs. Ollama plus `say` is fully
offline. Notes live in `~/.jarvis/notes.json`.

## License

MIT for this repository (see `LICENSE`). openWakeWord's pre-trained `hey_jarvis` model is CC BY-NC-SA 4.0
(non-commercial); train your own model with openWakeWord for commercial use.
