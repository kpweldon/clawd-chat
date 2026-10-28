#!/bin/bash
# Clawd Chat launcher - used by the Dock app (~/Applications/Clawd Chat.app).
# Starts the local server if it isn't answering, then opens (or raises) the
# chat window: a Chrome app window on its own profile, so it never mixes with
# the normal browser.
BASE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
PROFILE="$HOME/Library/Application Support/ClaudeChatProfile"
URL="http://127.0.0.1:8792/"
ARGS=(--app="$URL" --user-data-dir="$PROFILE" --no-first-run --no-default-browser-check
      --window-size=1280,900 --window-position=120,60)

PY="$(command -v python3 || echo /usr/bin/python3)"
[ -x /opt/homebrew/bin/python3 ] && PY=/opt/homebrew/bin/python3
if ! /usr/bin/curl -s -o /dev/null --max-time 1 "${URL}ping"; then
  "$PY" "$BASE/server.py" --daemon
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    /usr/bin/curl -s -o /dev/null --max-time 1 "${URL}ping" && break
    /bin/sleep 0.3
  done
fi

chat_pid(){
  local p
  for p in $(pgrep -x "Google Chrome" 2>/dev/null); do
    if ps -o args= -p "$p" 2>/dev/null | grep -q -- "--user-data-dir=$PROFILE"; then
      echo "$p"; return 0
    fi
  done
  return 1
}
window_count(){
  local n
  n="$(osascript -e "tell application \"System Events\" to tell (first process whose unix id is $1) to return count of windows" 2>/dev/null)"
  case "$n" in ''|*[!0-9]*) n=0;; esac
  echo "$n"
}
raise(){ osascript -e "tell application \"System Events\" to set frontmost of (first process whose unix id is $1) to true" >/dev/null 2>&1; }

pid="$(chat_pid)" || pid=""
if [ -n "$pid" ]; then
  if [ "$(window_count "$pid")" -gt 0 ]; then
    raise "$pid"
  else
    # foreground on purpose: Chrome hands the command line to the instance that
    # owns the profile and exits; a backgrounded child would be reaped first
    "$CHROME" "${ARGS[@]}" >/dev/null 2>&1
    raise "$pid"
  fi
  exit 0
fi

# cold start: the app process becomes Chrome, so the Dock icon stays lit
exec "$CHROME" "${ARGS[@]}"
