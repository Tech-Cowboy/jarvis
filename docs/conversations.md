# Conversations: how the ElevenLabs agent and the Mac work together

`daxton convai setup` makes Daxton a real-time conversational agent. This note is the map of what runs where,
what the agent can do, and what to check when something is off.

## The shape of it

```
you (Mac mic / phone) ──speech──► ElevenLabs Conversational AI ──► Claude (the brain)
                                    │  speech recognition, turn-taking,          │
                                    │  interruptions, the Daxton voice           │ decides to use a tool
                                    ◄──── client tool call ────────────────────┘
                                    ▼
                         the client holding the conversation
                         (daxton/convai/local.py on the Mac, or the dashboard page)
                                    ▼
                         Daxton's skill registry on the Mac: open_app, web_search, read_file,
                         run_command, delegate_task, remember, ...  ──► result back to the agent
```

- The **agent** (name "Daxton AI (personal)") lives on your ElevenLabs account, separate from any phone agent.
  `daxton convai setup` creates it, or updates it when the skills, the prompt or the settings change, and keeps
  the ids in `~/.daxton/convai.json`. Authentication is required: nobody can connect to the agent without a
  short-lived token that only the Mac (holding the API key) can mint.
- **Tools are client tools.** Every skill in `daxton skills` is registered with the agent by name and JSON
  schema. When the agent calls one, the call goes to whichever client is in the conversation, which runs the
  skill on the Mac and returns the text result. The Mac never opens a webhook to the internet for this.
- **At the Mac** (`daxton`, `daxton ui`, or `daxton convai talk`): saying the name opens a session on the Mac's
  microphone and speakers through the ElevenLabs Python SDK with a sounddevice audio interface
  (`daxton/convai/local.py`). The words that woke Daxton are sent as the first user message, so "Daxton, what's
  the weather" is answered without repeating yourself. The session ends when you say goodbye (the agent calls
  `end_session`, which in a conversation ends the session rather than the assistant), after `CONVAI_SILENCE_END`
  seconds of silence, at `CONVAI_MAX_MINUTES`, or from the dashboard's End button; then the Mac goes back to
  listening for the name.
- **In the portal** (https://daxton.chat or the local dashboard): the **Converse** button loads the ElevenLabs
  browser SDK, fetches a token from `/api/convai/session` (behind Daxton's login), and runs the conversation over
  WebRTC in the page, with the browser's echo cancellation, so you can interrupt freely. Tool calls are forwarded
  to `POST /api/command {"cmd": "run_skill"}` on the Mac. Transcript lines are posted to `/api/convai/event` so
  the HUD log and the memory see them.
- **Memory.** Each conversation is saved to `~/.daxton/conversations/<date>-<client>.md`. The next session opens
  with dynamic variables: the time, your name, recent notes (`remember`), background tasks, and the tail of the
  last conversation. The agent's prompt references them as `{{now}}`, `{{recent_notes}}` and so on.

## Echo, barge-in, and the two microphones

A Mac with speakers hears itself. ElevenLabs detects interruptions from the audio it receives, so without echo
cancellation the agent would keep stopping for its own voice. Therefore, at the Mac, `CONVAI_BARGE_IN=false`
(the default) replaces the microphone with silence while Daxton is talking and for a third of a second after;
you speak in the pauses, like a speakerphone. With a headset set `CONVAI_BARGE_IN=true`. Browsers cancel echo
themselves, so the portal is full duplex.

While a session runs, the Mac's own always-listening microphone is stopped (one capture stream at a time) and
restarted afterwards.

## Real work

The `work` skills (`daxton/skills/work.py`):

| Skill | What it does | Guard rails |
|---|---|---|
| `read_file`, `write_file`, `list_files` | text files and folders | only under your home folder; never `.env`, keys, keychains, Daxton's own state |
| `run_command` | one shell line, `bash -lc`, from your home folder | deny list (`sudo`, `rm -rf /` or `~`, disk tools, `shutdown`, piping downloads into a shell, ...), 60 s, output clipped; `DAXTON_SHELL=false` turns it off |
| `run_python` | a Python snippet | same as above |
| `fetch_page` | a web page as readable text | 20 s, clipped |
| `delegate_task` | a full brief to Claude Code, headless, in a task folder, on a background thread | `claude -p ... --permission-mode acceptEdits --max-turns 60`, `TASK_TIMEOUT_MINUTES`; result saved to `~/.daxton/tasks/`, announced by voice and in the HUD |
| `task_status` | running, done or failed, with results | |

The agent's prompt tells it to use these without asking for ordinary work, to say what it ran when a command
changes something, and to ask first when something could delete or overwrite your files. Everything it does shows
up in the HUD log as a tool line.

With a business system configured (`docs/business.md`), the agent also has `bookings`, `find_customer`,
`recent_leads`, `sales_summary`, `inbox`, `reminders` and `price_check`, all read-only, and its prompt tells it to
answer business questions from them, never from memory, and never to name the vendor.

`delegate_task` needs Claude Code installed and signed in on the Mac (`npm install -g @anthropic-ai/claude-code`,
then run `claude` once). It runs with Claude Code's own login, not the `ANTHROPIC_API_KEY` in `.env`. Each task
gets its own folder unless you name one ("in my daxton-ai project"), and `TASK.md` in it records the brief.

## Commands and settings

```
daxton convai setup        create or update the agent and its tools (idempotent; run after adding skills)
daxton convai talk         a conversation on the Mac now, without the wake word
daxton convai status       key, agent, llm, voice, tools, local mode, shell, Claude Code
```

| Setting | Default | Notes |
|---|---|---|
| `ELEVENLABS_API_KEY` | | required |
| `CONVAI_LLM` | `claude-sonnet-5` | any model ElevenLabs lists (`claude-haiku-4-5` is cheaper and quicker) |
| `CONVAI_VOICE_ID` | `ELEVENLABS_VOICE_ID` | |
| `CONVAI_TTS_MODEL` | `eleven_flash_v2` | `eleven_turbo_v2` is a touch richer; English agents must use a v2 turbo or flash model |
| `CONVAI_LOCAL` | `true` | the name opens a conversation at the Mac |
| `CONVAI_BARGE_IN` | `false` | see above |
| `CONVAI_SILENCE_END` | `45` | seconds of silence that end a session |
| `CONVAI_MAX_MINUTES` | `30` | hard stop |
| `DAXTON_SHELL` | `true` | the shell and delegation skills |
| `CLAUDE_CODE_BIN` | on PATH | |
| `TASKS_DIR` | `~/Documents/Claude/Projects/daxton-tasks` | |
| `TASK_TIMEOUT_MINUTES` | `30` | |

## Costs

Conversation minutes are billed by ElevenLabs on your plan, and the LLM is billed through ElevenLabs as well
(Claude Sonnet costs more per minute than Haiku). Claude Code tasks use your Claude subscription or API on the Mac.
The old single-exchange pipeline (Whisper, the tiered brain, ElevenLabs text-to-speech) still exists and is what
typed requests in the dashboard use.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `daxton convai status` says the key is not set | add `ELEVENLABS_API_KEY=` to `.env` and restart the dashboard service |
| Setup fails with 422 on the agent | the model name in `CONVAI_LLM` is not one ElevenLabs offers; the error lists the options |
| The agent answers but no tools run | `daxton convai setup` again (tools are re-registered from the current skills) |
| The Mac keeps interrupting itself | `CONVAI_BARGE_IN=false` (the default), or use a headset |
| "Connection closed before the session was established" in the HUD | the token was minted for an agent that no longer exists: `daxton convai setup` |
| Converse works at the Mac but not on the phone | the phone needs HTTPS (the portal) and microphone permission for the site |
