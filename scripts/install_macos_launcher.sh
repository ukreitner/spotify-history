#!/bin/zsh

# Compile a small clickable AppleScript application that starts Listening Archive.

set -eu

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
LAUNCHER_PATH="$ROOT_DIR/scripts/launch_local_app.sh"
APP_DIR="${1:-$HOME/Applications/Listening Archive.app}"
APP_PARENT="$(dirname "$APP_DIR")"

mkdir -p "$APP_PARENT"

if [[ -e "$APP_DIR" ]]; then
  backup_path="$APP_DIR.backup-$(date '+%Y%m%d-%H%M%S')"
  mv "$APP_DIR" "$backup_path"
  printf 'Existing launcher moved to %s\n' "$backup_path"
fi

escaped_launcher="${LAUNCHER_PATH//\\/\\\\}"
escaped_launcher="${escaped_launcher//\"/\\\"}"

/usr/bin/osacompile -o "$APP_DIR" \
  -e 'on run' \
  -e "do shell script \"/bin/zsh \\\"$escaped_launcher\\\" >/dev/null 2>&1 &\"" \
  -e 'end run'

printf 'Installed %s\n' "$APP_DIR"
