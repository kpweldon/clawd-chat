# Clawd Chat

**A message-app window for Claude Code.** Every Claude Code session on your Mac shows up as a chat thread. You see what you asked and what Claude answered. The tool calls, diffs and walls of code stay out of the way.

> Unofficial side project. Not made by or affiliated with Anthropic.

## What it does

- **Threads, not terminals.** Lists your Claude Code sessions from the last 14 days, newest first, with search. A dot marks a new reply.
- **Just the words.** Your messages are orange bubbles. Claude's replies are rendered markdown. Code blocks fold under "Show code", and tool work shrinks to a "worked · 12 steps" line.
- **Sub-agents get their own threads.** Every agent Claude starts shows up as a card. Click it to read what the agent was asked and what it said back.
- **Reply from the window.** Type and press Enter. The thread continues with full context (`claude -p --resume` runs in the session's own folder). **+ New chat** starts a fresh session.
- **Label your threads.** Rename, colour-code and pin threads to the top. Use the ⋯ button or right-click a thread.
- **Clickable output.** Links and file paths in replies open in your browser or Finder.
- **Status at a glance.** Threads are tagged *Working…*, *Open in terminal*, or *3 agents · 1 running*.

Everything runs locally: a small Python server (standard library only) on `127.0.0.1:8792`, plus one HTML page. Your conversations, labels and logs never leave your Mac, and they're git-ignored.

## Install (macOS)

You need [Claude Code](https://claude.com/claude-code), Google Chrome and Python 3.

```bash
git clone https://github.com/kpweldon/clawd-chat.git ~/clawd-chat
cd ~/clawd-chat
./install.sh --dock --autoopen
```

- `--dock` pins **Clawd Chat** to your Dock.
- `--autoopen` adds a Claude Code `SessionStart` hook that opens the window whenever you start `claude` in a terminal. It skips headless runs, and it skips if the window is already open.

Leave out either flag if you don't want it. The app lives in `~/Applications/Clawd Chat.app` and runs the folder you cloned, so `git pull` updates it.

## Settings

`config.json`:

| Key | Default | What it does |
|---|---|---|
| `port` | `8792` | Local port |
| `days` | `14` | How far back threads are listed (pinned threads always show) |
| `idle_exit_minutes` | `15` | The server quits after this long with no window open and nothing running |
| `claude` | `~/.local/bin/claude` | Path to the Claude Code CLI |
| `permission_mode` | `auto` | Permission mode for replies sent from the app (headless runs can't show prompts) |
| `new_chat_folder` | `~` | Where new chats start |
| `chat_note` | *(built in)* | Optional extra system prompt for replies sent from the app |

## Good to know

- **Replies run headless**, so Claude can't show permission or pick dialogs there. It asks in plain words instead, and you answer in the next message. Pick the `permission_mode` you're comfortable with.
- **A thread that's still running in a terminal** is tagged *Open in terminal*. Sending to it from the app starts a second Claude on the same session, so let the terminal finish first.
- **Keyboard:** `Enter` sends, `Shift+Enter` adds a new line, `⌘K` searches, `⌘N` starts a new chat, `Esc` closes the agent panel.

## Files

| File | What it is |
|---|---|
| `server.py` | Reads `~/.claude/projects/*/*.jsonl` incrementally, serves the API, runs replies |
| `chat.html` | The whole UI |
| `launch.sh` | Starts the server and opens or raises the window |
| `autoopen.sh` | Optional `SessionStart` hook |
| `install.sh` | Builds the `.app` (plus the optional Dock tile and hook) |
| `make_icon.py` | Redraws `icon1024.png` (needs Pillow) |

MIT licensed.
<!-- sync test -->
