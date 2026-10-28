#!/bin/bash
# Clawd Chat installer (macOS). Builds "Clawd Chat.app" in ~/Applications that
# runs this folder, so keep the folder where it is (or re-run after moving it).
#
#   ./install.sh              build the app
#   ./install.sh --dock       ...and pin it to the Dock
#   ./install.sh --autoopen   ...and open it whenever you start `claude` in a terminal
set -e
BASE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP="$HOME/Applications/Clawd Chat.app"

command -v claude >/dev/null || [ -x "$HOME/.local/bin/claude" ] || { echo "Claude Code isn't installed: https://claude.com/claude-code"; exit 1; }
[ -x "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" ] || { echo "Clawd Chat opens in a Google Chrome app window; install Chrome first."; exit 1; }

mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cat > "$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>Clawd Chat</string>
  <key>CFBundleDisplayName</key><string>Clawd Chat</string>
  <key>CFBundleIdentifier</key><string>com.clawdchat.app</string>
  <key>CFBundleVersion</key><string>1.0</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleExecutable</key><string>ClawdChat</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>LSUIElement</key><true/>
  <key>NSHighResolutionCapable</key><true/>
</dict>
</plist>
PLIST
printf '#!/bin/bash\nexec "%s/launch.sh"\n' "$BASE" > "$APP/Contents/MacOS/ClawdChat"
chmod +x "$APP/Contents/MacOS/ClawdChat" "$BASE/launch.sh" "$BASE/autoopen.sh"

# icon: icon1024.png -> AppIcon.icns
SET="$(mktemp -d)/AppIcon.iconset"; mkdir -p "$SET"
for s in 16 32 128 256 512; do
  sips -z $s $s "$BASE/icon1024.png" --out "$SET/icon_${s}x${s}.png" >/dev/null
  sips -z $((s*2)) $((s*2)) "$BASE/icon1024.png" --out "$SET/icon_${s}x${s}@2x.png" >/dev/null
done
iconutil -c icns "$SET" -o "$APP/Contents/Resources/AppIcon.icns"
rm -rf "$(dirname "$SET")"
touch "$APP"
/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -f "$APP" >/dev/null 2>&1 || true
echo "Built $APP"

for a in "$@"; do
  if [ "$a" = "--dock" ]; then
    if ! defaults read com.apple.dock persistent-apps 2>/dev/null | grep -q "Clawd%20Chat.app"; then
      url="file://$(python3 -c 'import sys,urllib.parse;print(urllib.parse.quote(sys.argv[1]))' "$APP")/"
      defaults write com.apple.dock persistent-apps -array-add "<dict><key>tile-data</key><dict><key>file-data</key><dict><key>_CFURLString</key><string>$url</string><key>_CFURLStringType</key><integer>15</integer></dict></dict></dict>"
      killall Dock
    fi
    echo "Pinned to the Dock"
  fi
  if [ "$a" = "--autoopen" ]; then
    python3 - "$BASE/autoopen.sh" <<'PY'
import json, os, shutil, sys, time
p = os.path.expanduser("~/.claude/settings.json")
d = json.load(open(p)) if os.path.exists(p) else {}
cmd = "/bin/bash " + sys.argv[1].replace(os.path.expanduser("~"), "~", 1)
groups = d.setdefault("hooks", {}).setdefault("SessionStart", [])
if not any(h.get("command", "").endswith("autoopen.sh") for g in groups for h in g.get("hooks", [])):
    if os.path.exists(p):
        shutil.copy(p, p + ".bak-clawdchat-%d" % time.time())
    groups.append({"matcher": "startup", "hooks": [{"type": "command", "command": cmd, "timeout": 10}]})
    json.dump(d, open(p, "w"), indent=2)
print("Opens with every new terminal claude session")
PY
  fi
done
echo "Open it from ~/Applications/Clawd Chat.app"
