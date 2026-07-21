#!/bin/zsh

# Start the local Listening Archive production app and open it in Chrome.
# Safe to run repeatedly: healthy services are reused instead of duplicated.

set -u

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
WEB_DIR="$ROOT_DIR/web"
RUNTIME_DIR="$ROOT_DIR/.run"
APP_URL="http://127.0.0.1:3000"
API_STATUS_URL="http://127.0.0.1:8001/api/meta/status"
FRONTEND_STATUS_URL="$APP_URL/"
NODE_PATH="/opt/homebrew/bin/node"
NPM_PATH="/opt/homebrew/bin/npm"

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

mkdir -p "$RUNTIME_DIR"
LAUNCH_LOG="$RUNTIME_DIR/launcher.log"
BACKEND_LOG="$RUNTIME_DIR/backend.log"
FRONTEND_LOG="$RUNTIME_DIR/frontend.log"
LOCK_DIR="$RUNTIME_DIR/launcher.lock"

log() {
  printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$1" >> "$LAUNCH_LOG"
}

notify() {
  /usr/bin/osascript -e "display notification \"$1\" with title \"Listening Archive\"" >/dev/null 2>&1 || true
}

open_archive() {
  if ! /usr/bin/open -a "Google Chrome" "$APP_URL" >/dev/null 2>&1; then
    /usr/bin/open "$APP_URL" >/dev/null 2>&1 || true
  fi
}

is_ready() {
  /usr/bin/curl --silent --fail --max-time 2 "$1" >/dev/null 2>&1
}

wait_until_ready() {
  local url="$1"
  local attempts="${2:-40}"
  local index=0
  while (( index < attempts )); do
    if is_ready "$url"; then
      return 0
    fi
    /bin/sleep 0.5
    (( index += 1 ))
  done
  return 1
}

if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  log "Another launcher process is already starting the app."
  open_archive
  exit 0
fi
trap 'rmdir "$LOCK_DIR" >/dev/null 2>&1 || true' EXIT

cd "$ROOT_DIR" || exit 1
log "Launcher invoked."

if is_ready "$API_STATUS_URL" && is_ready "$FRONTEND_STATUS_URL"; then
  log "Services already healthy; opening Chrome."
  open_archive
  exit 0
fi

# Pull fresh history when the checkout is clean. Network or authentication
# failures do not prevent the last local snapshot from opening.
if [[ -z "$(git status --porcelain --untracked-files=no 2>/dev/null)" ]]; then
  log "Checking GitHub for newer listening history."
  git pull --ff-only origin main >> "$LAUNCH_LOG" 2>&1 || log "Git pull failed; continuing with the local snapshot."
else
  log "Skipping git pull because the checkout has local changes."
fi

if [[ ! -x "$ROOT_DIR/.venv/bin/python" ]]; then
  log "Creating the Python environment."
  /usr/bin/python3 -m venv "$ROOT_DIR/.venv" >> "$LAUNCH_LOG" 2>&1 || {
    notify "Could not create the Python environment. See .run/launcher.log."
    exit 1
  }
fi

requirements_fingerprint="$(/usr/bin/shasum "$ROOT_DIR/requirements.txt" | /usr/bin/awk '{print $1}')"
installed_requirements="$(cat "$RUNTIME_DIR/requirements.fingerprint" 2>/dev/null || true)"
if [[ "$requirements_fingerprint" != "$installed_requirements" ]]; then
  log "Installing Python dependencies."
  "$ROOT_DIR/.venv/bin/pip" install -r "$ROOT_DIR/requirements.txt" >> "$LAUNCH_LOG" 2>&1 || {
    notify "Python dependencies could not be installed. See .run/launcher.log."
    exit 1
  }
  printf '%s\n' "$requirements_fingerprint" > "$RUNTIME_DIR/requirements.fingerprint"
fi

if [[ ! -x "$NODE_PATH" || ! -x "$NPM_PATH" ]]; then
  log "Node.js was not found at /opt/homebrew/bin."
  notify "Node.js is missing. Install it with Homebrew, then click again."
  exit 1
fi

web_fingerprint="$(git rev-parse HEAD:web 2>/dev/null || true)"
built_web_fingerprint="$(cat "$RUNTIME_DIR/web.fingerprint" 2>/dev/null || true)"
if [[ ! -d "$WEB_DIR/node_modules" || ! -f "$WEB_DIR/.next/BUILD_ID" || "$web_fingerprint" != "$built_web_fingerprint" ]]; then
  log "Preparing the production web app."
  (
    cd "$WEB_DIR" || exit 1
    "$NPM_PATH" install --no-audit --no-fund
    API_PROXY_TARGET="http://127.0.0.1:8001" "$NPM_PATH" run build
  ) >> "$LAUNCH_LOG" 2>&1 || {
    notify "The web app could not be built. See .run/launcher.log."
    exit 1
  }
  printf '%s\n' "$web_fingerprint" > "$RUNTIME_DIR/web.fingerprint"
fi

if ! is_ready "$API_STATUS_URL"; then
  log "Starting the API on port 8001."
  /usr/bin/nohup "$ROOT_DIR/.venv/bin/python" -m uvicorn api.main:app \
    --host 127.0.0.1 --port 8001 >> "$BACKEND_LOG" 2>&1 < /dev/null &
  printf '%s\n' "$!" > "$RUNTIME_DIR/backend.pid"
fi

if ! wait_until_ready "$API_STATUS_URL" 40; then
  log "The API did not become ready."
  notify "The API did not start. See .run/backend.log."
  exit 1
fi

if ! is_ready "$FRONTEND_STATUS_URL"; then
  log "Starting the web app on port 3000."
  (
    cd "$WEB_DIR" || exit 1
    API_PROXY_TARGET="http://127.0.0.1:8001" /usr/bin/nohup "$NPM_PATH" run start -- \
      --hostname 127.0.0.1 --port 3000 >> "$FRONTEND_LOG" 2>&1 < /dev/null &
    printf '%s\n' "$!" > "$RUNTIME_DIR/frontend.pid"
  )
fi

if ! wait_until_ready "$FRONTEND_STATUS_URL" 60; then
  log "The web app did not become ready."
  notify "The web app did not start. See .run/frontend.log."
  exit 1
fi

log "Listening Archive is ready."
notify "Ready — opening in Chrome."
open_archive
