# polish-chat

**A manual reply box that floats next to your chat window.**
It reads the conversation on screen with local OCR and keeps it as context. When you feel like replying,
click once to generate a draft, or write your own and click polish — then hit send.

`Windows 10 1903+ / 11` · `Local offline OCR` · `No auto-reply` · `MIT`

[中文说明 →](README.md)

> The user interface is entirely in Chinese and the app calls itself **润色** ("polish");
> `polish-chat` is only the project, file and repository name (`polish-chat.exe`, `polish-chat-vX.Y.Z.zip`).

---

## What it is

A small always-on-top window next to your WeChat window. It captures that window with Windows Graphics
Capture and reads it with a local offline OCR engine, so it knows what the conversation looks like.
When you want to reply:

- **Generate** — writes 3 candidates from the recent conversation. The first one lands in the input box;
  the other two become "swap" buttons.
- **Or write your own**, then **Polish** — rewrites only *how* you say it, never *what* you say.
- **Send** — pastes into the WeChat input box and presses Enter (`Ctrl+Enter` works too).

## What it deliberately does not do

- **No auto-reply.** Incoming messages are only recorded as context, entirely offline. No model is called,
  no request is sent, until you click a button. Idle time costs exactly zero tokens.
- **No hooking, no injection, no reading WeChat's database or process memory.** It only screenshots its own
  window and OCRs it — the same class of operation as a screen reader or a screen recorder.
- **Frames never touch the disk.** Captured frames are numpy arrays in memory: never saved, never logged,
  never uploaded.
- **No money-related UI elements.** Transfers, red packets and payment screens are never touched, and the
  prompts forbid those topics.
- **It never presses send on its own.** There is no automatic trigger anywhere in the program; the only
  Enter press happens right after you click Send.

## Screenshots

<table>
<tr>
<td width="50%"><img src="docs/ui_home.png" alt="Main window"></td>
<td width="50%"><img src="docs/ui_settings.png" alt="Settings"></td>
</tr>
<tr>
<td align="center">Persistent input box + generate / polish / send; the generated line is editable, with two alternates and an undo below</td>
<td align="center">Settings: relationship, speaking style, context length, group reply target, docking, model</td>
</tr>
</table>

The panel **docks itself to the right of the chat window** by default (it flips to the left if there is no
room, vertically aligned and matching the window height). Drag it to detach; the pin in the title bar or the
settings switch docks it again.

## Download

👉 **[Latest release](https://github.com/Echosong/polish-chat/releases/latest)**

1. Download `polish-chat-vX.Y.Z.zip` (~140 MB) from the Releases page.
2. Extract it to a permanent folder (keep the whole folder together — the exe needs the files next to it).
3. Run `polish-chat.exe`.

Requirements: Windows 10 1903+ / 11, a chat window, and one API key.

> The exe is unsigned, so SmartScreen will complain: "More info" → "Run anyway". If that bothers you,
> build it yourself (see below).

## First run

The settings page asks for one key:

1. **Provider** — defaults to **DeepSeek** (get a key at [platform.deepseek.com](https://platform.deepseek.com/);
   a generation costs a fraction of a cent). Twelve providers are built in (OpenAI / Anthropic / Gemini
   protocols), plus two "custom" entries where you supply your own base URL.
2. Click **Fetch models** to pull the list and pick one, or type a model id by hand.
3. Pick your relationship (partner / friend / colleague / family / custom) and save.

The key goes into the Windows user environment (registry `HKCU\Environment`, the same place `setx` writes) —
only ever one name, `LLM_API_KEY`. **It never appears in a file and never in a log** (all error text is
redacted). Everything else lives in `config.json` next to the exe, so the whole folder is portable.

## Daily use

- Keep the chat window open and not minimized (it may be covered by other windows — capture still works).
- The panel follows WeChat; the current conversation drives the context, and history is stored per
  conversation, so switching chats does not mix them up.
- The capture switch in the title bar pauses reading entirely; the input box and buttons keep working.

**Cost**: one model call per click on Generate or Polish. Ten minutes of silence is ten minutes of zero
calls; watching without clicking calls nothing.

## Features

- **Manual only** — incoming messages are recorded, never analyzed automatically.
- **Generate** — 3 candidates, the first one is the model's own pick; swap between them in place.
- **Polish** — makes your own draft flow naturally and match the conversation, without changing meaning,
  stance or information. The original stays under "undo".
- **Send** — paste + Enter (`Ctrl+Enter` also works).
- **Follows the active conversation** — the conversation name is OCR'd from the panel header; history and
  context are kept per conversation. You can also browse another conversation (read-only: no generating or
  sending, to avoid posting into the wrong chat).
- **Group chats** — speaker names are fed to the model; an optional reply target makes every candidate
  address that person, and Send can prefix `@name ` (plain text).
- **Docks next to the chat window**; drag to detach, pin to re-dock.
- **Capture switch**, **live chat log**, **debug view** (draws the frame and every
  recognition box in a separate window, in memory only), **auto-restore of a minimized chat window**.
  The chat log is collapsed by default: the home page shows one line — the conversation title with the
  number of messages recorded for it — and the arrow next to it expands "what they said last" plus the log
  of exactly what OCR read.
- **Any model** — 12 providers plus custom base URLs, one key for all of them; thinking mode toggle;
  optional update check at startup.

## Privacy

- Only reads conversations on your own machine that you are already allowed to see.
- Screenshots its own window and OCRs locally (RapidOCR). No hooking, no injection, no database access,
  no memory reading.
- Frames stay in memory. The shipped program (`app/`, `core/`) contains no `.save()` call.
- **Network traffic** happens only when you click Generate or Polish: the recent conversation, your
  relationship setting, up to 12 of your own short messages (as a style sample), your style note, plus your
  draft when polishing. Nothing else. There is **no server operated by this project** — content goes
  straight to the API endpoint you configured. The optional update check sends one GET to the GitHub
  Releases API with nothing but a user agent and the current version.

## How it works

```
WGC captures the chat window (works for GPU-composited and occluded windows)
  → pixel anchors locate the message area (background colour + separator lines; theme-independent)
  → the header is OCR'd for the conversation name (skipped when the header pixels are unchanged)
  → RapidOCR reads the message area only
  → bubble colours split me / her; grey text (quotes, timestamps, speaker names, link cards) is filtered out
  → diffing against the previous frame avoids re-reporting messages that scrolled into view
  → all of the above is local; core/engine.py is only reached when you click a button
       Generate → generate()  one model call → 3 candidates, first one into the box
       Polish   → polish()    one model call → your sentence, rewritten (same meaning)
       Send     → clipboard → focus the WeChat input box → Ctrl+V → Enter
```

Capture and OCR run in a separate process (one frame costs 250–800 ms, which would freeze the Qt UI).
The parent process only handles the UI and network calls. The panel re-checks the chat window rectangle
every 50 ms while docked.

### Why OCR

The target window renders its UI onto a GPU-composited canvas: its UIA tree has two nodes and **no control
tree** (verified by `probe/probe_win.py` and `probe/probe_win2.py`). So the only clean non-invasive capture
path is a screenshot of your own window plus local OCR — offline, zero tokens, no touching the other process.

### Why the output does not read like an AI

- A Chinese anti-template system prompt: no summarizing, no restating, no "first/second/in conclusion", no
  customer-service pleasantries, no sentence-final periods, incomplete sentences and filler words allowed.
- Style samples: your last 12 short messages are handed over verbatim so the model imitates your wording,
  sentence length and punctuation habits.
- The polish prompt is stricter still: meaning, stance and information must not change, length grows at most
  ~20%, and an already-fine draft is returned as-is rather than reworded for the sake of it.
- Output cleanup strips numbering, brackets, quotes and copied `me:` prefixes.
- A hard anti-injection filter: messages that look like "ignore the rules above" are flagged, and candidates
  that simply echo the other person's words are dropped.

## Supported models

**Generate + polish (one key: `LLM_API_KEY`)**

| Provider | Protocol | Base URL | Default model |
| --- | --- | --- | --- |
| DeepSeek (default) | OpenAI | `api.deepseek.com` | `deepseek-flash` |
| OpenRouter | OpenAI | `openrouter.ai/api/v1` | `deepseek/deepseek-v4.1-flash` |
| OpenAI | OpenAI | `api.openai.com/v1` | pick your own |
| Moonshot (Kimi) | OpenAI | `api.moonshot.cn/v1` | pick your own |
| Zhipu GLM | OpenAI | `open.bigmodel.cn/api/paas/v4` | pick your own |
| Qwen (DashScope) | OpenAI | `dashscope.aliyuncs.com/compatible-mode/v1` | pick your own |
| SiliconFlow | OpenAI | `api.siliconflow.cn/v1` | pick your own |
| OpenCode Go | OpenAI | `opencode.ai/zen/go/v1` | `deepseek-v4.1-flash` |
| Anthropic | Anthropic | `api.anthropic.com` | pick your own |
| Google Gemini | Gemini | SDK default | pick your own |
| Custom · OpenAI-compatible | OpenAI | yours | pick your own |
| Custom · Anthropic-compatible | Anthropic | yours | pick your own |

Each protocol goes through its official SDK (`openai` / `anthropic` / `google-genai`) — no hand-written HTTP.
Generation runs at temperature 1.2, polishing at 1.0 and returns a single rewritten passage.

## Requirements

- **Windows 10 1903+ or Windows 11** (minimum for Windows Graphics Capture)
- **Python 3.10–3.12** for running from source (3.13+ fails: `rapidocr-onnxruntime` 1.4.x caps
  `requires_python` below 3.13 and pip silently installs 1.2.3, which crashes at startup)
- A chat window
- One API key (`LLM_API_KEY`)

> On Windows 10, WGC draws a yellow border around the captured window and the system will not let you turn
> it off (Windows 11 does). Flip the capture switch to pause and the border disappears immediately.

## Run from source

```bash
git clone https://github.com/Echosong/polish-chat.git
cd polish-chat
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

Preview the UI with synthetic data (offline, never touches WeChat):

```bash
python tools/preview_ui.py --state ready --screenshot docs/ui_home.png
python tools/preview_ui.py --state typed
python tools/preview_ui.py --state ready --width 320 --height 420
```

Exercise the two model paths (needs a key and network):

```bash
set LLM_API_KEY=...
python tools/demo.py
python tools/demo.py --draft "my own draft"   # polish only
```

Offline self-tests for the core modules:

```bash
python -m core.keys && python -m core.providers && python -m core.draft
python -m core.engine && python -m core.llm && python -m app.update
```

### Build it yourself

Run `build.bat` (it creates a venv, installs dependencies and calls PyInstaller), or:

```bash
pip install -r requirements.txt pyinstaller
pyinstaller --noconfirm --clean polish-chat.spec
```

Output is `dist\polish-chat\` (onedir — a onefile build would re-extract 140 MB on every launch).
Pushing a `v*` tag makes `.github/workflows/release.yml` build on `windows-latest` and attach the zip to a
Release; manual runs only produce an artifact.

## Settings reference

| Control | Effect | Stored in |
| --- | --- | --- |
| Relationship | partner / friend / colleague / family / custom; sets the tone for both paths | `config.json` → `relationship` |
| Speaking style (optional) | One line describing your own tone | `config.json` → `style` |
| Context length | How many recent messages both paths see (3–30) | `config.json` → `context` (default 10) |
| Group reply target | Adds a reply-target row in group chats | `config.json` → `reply_target` (off) |
| Dock next to chat window | Follow the chat window; dragging detaches for this run only | `config.json` → `dock` (on) |
| Check for updates at startup | One GitHub API call | `config.json` → `check_update` (on) |
| Debug view | Separate window drawing frames and recognition boxes; takes effect immediately | `config.json` → `debug_view` (off) |
| Provider / Base URL | Any provider in the table; base URL only for custom ones | `config.json` → `draft_provider` / `draft_base_url` |
| API key | Key of the selected provider; leave blank to keep the stored one | registry `HKCU\Environment` → `LLM_API_KEY` |
| Model | Fetch the list or type an id | `config.json` → `draft_model` |
| Thinking mode | Model thinks before writing: slower and pricier | `config.json` → `thinking` (off) |

## FAQ

**Send only inserted a newline.**
Your WeChat is configured to send with `Ctrl+Enter`. This tool presses Enter; it will not change your WeChat
settings — either switch WeChat's shortcut or click WeChat's own send button (the text is already typed).

**Why must the chat window stay un-minimized?**
Windows does not render minimized windows, so no capture method can read them. Covering it with other windows
is fine. If it is minimized, the app restores it without stealing focus and pushes it to the bottom.

**A yellow border on Windows 10?**
That is WGC's capture indicator; the OS will not let it be disabled before Windows 11. Flip the capture
switch to pause.

**Polish returned nothing / "the model only thought about it".**
Some OpenAI-compatible endpoints run a model with thinking enabled by default and no way to disable it, so
reasoning tokens can consume the whole output budget and leave the content empty. The app automatically
retries the same request once; if it is still empty you get that message — clicking again usually works, or
pick a different model.

**Is the "@" prefix a real mention?**
No. It is plain text; WeChat will not turn it into a mention. Real mentions require WeChat's own member
picker, which this tool does not simulate.

## Project layout

```
main.py                 entry point: parent process = UI + network; child process = capture + OCR
app/                    UI and capture layer
  capture.py            find the window, WGC frames, pixel-anchored message area, window_rect() for docking
  ocr.py                RapidOCR + me/her/grey classification + scroll de-duplication + header title
  worker.py             capture subprocess main loop
  fill.py               fill() pastes only; send() pastes and presses Enter (only from the Send button)
  overlay.py            the floating panel: input box, buttons, alternates, undo, docking, settings page
  debugwin.py           debug view window (in-memory frames only)
  settings.py           one key in the registry, everything else in config.json
  update.py             optional version check
core/                   model paths, platform independent
  engine.py             the single entry point: generate() / polish()
  providers.py          provider table (12 entries): protocol, base URL, default model
  keys.py               key lookup (env → registry), redaction, ChatError
  llm.py                thin adapter over the three protocols, official SDKs only
  draft.py              both prompts, parsing, filtering, follow-up completion
tools/                  developer tools: demo.py, preview_ui.py, make_icon.py
probe/                  one-off probes whose conclusions are quoted in this README
polish-chat.spec        PyInstaller definition (onedir)
build.bat               one-click local build
.github/workflows/release.yml   tag → build → attach zip to the Release
```

## Known limitations

- **WeChat only.** Recognition depends on WeChat's own layout and colours; a redesign can break it. Other
  messengers are out of scope.
- **Send relies on Enter** (see FAQ).
- **A very tall input box can confuse message-area detection** (it is located by the first separator line
  below 45% of the panel height).
- **The "text must sit on a flat background" rule assumes unscaled pixels**; heavily scaled screenshots can
  turn the whole screen into "images".
- **Group speaker names missed by OCR attach that message to the previous speaker.**
- **Conversations are keyed by header title**; OCR slips are merged into known conversations by similarity,
  at the cost of merging two conversations whose names differ by one character.
- **Two identical consecutive messages from the same person collapse into one** (similarity de-duplication).
- **Send clicks a computed input-box coordinate** (40 px below the message area, 60 px from its left edge).
- **No tray icon**: closing the window exits the app.
- **Windows 10 yellow capture border** cannot be disabled.

## Changelog

**v1.0.0 — first release**
- Persistent input box next to the chat window: **Generate** (3 candidates, first into the box, two swap
  buttons), **Polish** (rewrites your own draft, meaning preserved, original restorable), **Send**
  (paste + Enter)
- **Manual triggering only**: incoming messages are recorded, never analyzed automatically
- Auto-docking to the right of the chat window, drag to detach, pin to re-dock
- Local capture + offline OCR context, stored per conversation; group speaker names and optional reply target
- Capture switch, live chat log, debug view, auto-restore of a minimized window
- 12 providers plus custom base URLs with a single key; thinking-mode toggle; optional update check
- Anti-injection filtering, style imitation, robust candidate parsing
- PyInstaller onedir packaging with `build.bat` and tag-driven releases

## Credits

- Derived from [`jev-chat-windows`](https://github.com/jev-chat/jev-chat-windows) (MIT), where the
  window-capture + offline-OCR approach originated.
- Which in turn was the Windows sister project of
  [Jev Chat Assistant](https://github.com/jev-chat/jev-chat-jarvis); its Jev decision kernel and question set
  came from that upstream project and **have been removed here**.
- [RapidOCR](https://github.com/RapidAI/RapidOCR), [windows-capture](https://github.com/NiiightmareXD/windows-capture),
  [PyQt-Fluent-Widgets](https://github.com/zhiyiYo/PyQt-Fluent-Widgets),
  [openai](https://github.com/openai/openai-python),
  [anthropic](https://github.com/anthropics/anthropic-sdk-python),
  [google-genai](https://github.com/googleapis/python-genai).

## License

MIT — see [LICENSE](LICENSE) and [NOTICE](NOTICE).

```
Copyright (c) 2026 Echosong
Portions Copyright (c) 2026 rezoch340 and the jev-chat contributors (jev-chat-windows)
```

The Windows release zip bundles PySide6-Fluent-Widgets (GPLv3, free for non-commercial use), so the package
as a whole is bound by GPLv3 terms. Commercial users must buy that license or replace the component.

**Disclaimer**: this project only handles conversations on your own device that you are allowed to view.
Running it on someone else's machine to read their chats is a different matter and is not endorsed here.
Follow the terms of service of the software you use and your local laws; a redesign on their side may break
recognition. Use at your own risk.
