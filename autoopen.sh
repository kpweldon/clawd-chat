#!/bin/bash
# SessionStart hook: bring up Clawd Chat when you start `claude` in a terminal.
# Stays silent for everything else: headless runs (the chat app's own replies,
# LaunchAgent jobs, cron), resumes/compactions, and when the window is already open.
input="$(cat)"
[ -n "$CLAUDE_CHAT_CHILD" ] && exit 0
case "$input" in *'"source":"startup"'*|*'"source": "startup"'*) ;; *) exit 0;; esac

# Only when the claude process that fired this hook has a real terminal.
p=$PPID
for _ in 1 2 3 4 5; do
  comm="$(ps -o comm= -p "$p" 2>/dev/null)"
  case "$comm" in *claude*) break;; esac
  p="$(ps -o ppid= -p "$p" 2>/dev/null | tr -d ' ')"
  [ -z "$p" ] || [ "$p" = "1" ] && exit 0
done
tty="$(ps -o tty= -p "$p" 2>/dev/null | tr -d ' ')"
[ -z "$tty" ] || [ "$tty" = "??" ] && exit 0

# Already open with a window? Leave it alone (don't steal focus every session).
PROFILE="$HOME/Library/Application Support/ClaudeChatProfile"
for c in $(pgrep -x "Google Chrome" 2>/dev/null); do
  if ps -o args= -p "$c" 2>/dev/null | grep -q -- "--user-data-dir=$PROFILE"; then
    n="$(osascript -e "tell application \"System Events\" to tell (first process whose unix id is $c) to return count of windows" 2>/dev/null)"
    [ "${n:-0}" -gt 0 ] 2>/dev/null && exit 0
  fi
done

# LaunchServices owns the launch, so nothing is tied to this hook's process.
[ -n "$AUTOOPEN_DRYRUN" ] && { echo WOULD_OPEN; exit 0; }
APP="$HOME/Applications/Clawd Chat.app"
[ -d "$APP" ] || APP="$HOME/Applications/CLAUDE CHAT.app"
# Open in the background (-g) so it never steals focus (e.g. mid-dictation), and if
# Chrome grabs focus anyway, hand it straight back to whatever was in front.
front="$(osascript -e 'tell application "System Events" to get bundle identifier of first process whose frontmost is true' 2>/dev/null)"
/usr/bin/open -g --env CLAWD_BACKGROUND=1 "$APP" >/dev/null 2>&1
if [ -n "$front" ]; then
  (                                   # detached, so claude's startup doesn't wait on it
    trap '' HUP
    for _ in 1 2 3 4 5 6 7 8 9 10 11 12; do
      /bin/sleep 0.5
      now="$(osascript -e 'tell application "System Events" to get bundle identifier of first process whose frontmost is true' 2>/dev/null)"
      if [ -n "$now" ] && [ "$now" != "$front" ]; then /usr/bin/open -b "$front"; break; fi
    done
  ) >/dev/null 2>&1 </dev/null &
  disown
fi
exit 0
