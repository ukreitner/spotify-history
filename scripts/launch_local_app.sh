#!/bin/zsh

# Start the local Listening Archive production app and open it in Chrome.
# Safe to run repeatedly: healthy services are reused only when they are the
# launcher-owned processes running the current source fingerprints.

set -u
umask 077

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
WEB_DIR="$ROOT_DIR/web"
RUNTIME_DIR="$ROOT_DIR/.run"
APP_URL="http://127.0.0.1:3000"
API_STATUS_URL="http://127.0.0.1:8001/api/meta/status"
FRONTEND_STATUS_URL="$APP_URL/"
FRONTEND_API_STATUS_URL="$APP_URL/api/meta/status"
API_PROXY_TARGET="http://127.0.0.1:8001"
NODE_PATH="/opt/homebrew/bin/node"
NPM_PATH="/opt/homebrew/bin/npm"

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

mkdir -p "$RUNTIME_DIR"
LAUNCH_LOG="$RUNTIME_DIR/launcher.log"
BACKEND_LOG="$RUNTIME_DIR/backend.log"
FRONTEND_LOG="$RUNTIME_DIR/frontend.log"
LOCK_PATH="$RUNTIME_DIR/launcher.lock"
touch "$LAUNCH_LOG" "$BACKEND_LOG" "$FRONTEND_LOG"
chmod 600 "$LAUNCH_LOG" "$BACKEND_LOG" "$FRONTEND_LOG"

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

source_fingerprint() {
  # Hash the current contents of tracked and untracked (but not ignored) source
  # files. Unlike a Git tree hash, this also notices uncommitted edits while
  # naturally excluding data/, node_modules/, and .next/ through .gitignore.
  {
    git ls-files -- "$@"
    git ls-files --others --exclude-standard -- "$@"
  } | LC_ALL=C /usr/bin/sort -u | while IFS= read -r source_path; do
    [[ -n "$source_path" ]] || continue
    if [[ -f "$source_path" ]]; then
      printf 'file:%s\n' "$source_path"
      /usr/bin/shasum "$source_path"
    else
      printf 'missing:%s\n' "$source_path"
    fi
  done | /usr/bin/shasum | /usr/bin/awk '{print $1}'
}

read_pid() {
  local pid_file="$1"
  local pid
  pid="$(cat "$pid_file" 2>/dev/null || true)"
  if [[ "$pid" == <-> ]]; then
    printf '%s\n' "$pid"
  fi
}

process_cwd() {
  local pid="$1"
  /usr/sbin/lsof -a -p "$pid" -d cwd -Fn 2>/dev/null \
    | /usr/bin/sed -n 's/^n//p' \
    | /usr/bin/head -n 1
}

pid_listens_on_port() {
  local pid="$1"
  local port="$2"
  local listener
  listener="$(/usr/sbin/lsof -nP -a -p "$pid" \
    -iTCP:"$port" -sTCP:LISTEN -t 2>/dev/null \
    | /usr/bin/head -n 1)"
  [[ "$listener" == "$pid" ]]
}

pid_is_same_or_descendant() {
  local child_pid="$1"
  local ancestor_pid="$2"
  local current_pid="$child_pid"
  local parent_pid
  local depth=0

  while [[ "$current_pid" == <-> && "$current_pid" -gt 1 && depth -lt 64 ]]; do
    [[ "$current_pid" == "$ancestor_pid" ]] && return 0
    parent_pid="$(/bin/ps -p "$current_pid" -o ppid= 2>/dev/null \
      | /usr/bin/awk '{print $1}')"
    [[ "$parent_pid" == <-> ]] || break
    current_pid="$parent_pid"
    (( depth += 1 ))
  done
  return 1
}

process_tree_listens_on_port() {
  local root_pid="$1"
  local port="$2"
  local listener_pid
  local listeners
  listeners="$(/usr/sbin/lsof -nP -iTCP:"$port" -sTCP:LISTEN -t 2>/dev/null || true)"
  for listener_pid in ${(f)listeners}; do
    [[ -n "$listener_pid" ]] || continue
    if pid_is_same_or_descendant "$listener_pid" "$root_pid"; then
      return 0
    fi
  done
  return 1
}

pid_matches_service() {
  local service="$1"
  local pid="$2"
  local command cwd

  [[ "$pid" == <-> ]] || return 1
  /bin/kill -0 "$pid" >/dev/null 2>&1 || return 1
  command="$(/bin/ps -p "$pid" -o command= 2>/dev/null || true)"
  cwd="$(process_cwd "$pid")"

  case "$service" in
    backend)
      [[ "$cwd" == "$ROOT_DIR" \
        && "$command" == *"uvicorn"* \
        && "$command" == *"api.main:app"* \
        && "$command" == *"--port 8001"* ]]
      ;;
    frontend)
      [[ "$cwd" == "$WEB_DIR" ]] || return 1
      # Accept both the current direct Next.js process and the npm wrapper used
      # by older launcher versions so the first upgraded click can stop it.
      if [[ "$command" == *"next/dist/bin/next"* \
        && "$command" == *" start"* \
        && "$command" == *"--port 3000"* ]]; then
        return 0
      fi
      if [[ "$command" == next-server* ]]; then
        return 0
      fi
      if [[ "$command" == *"npm"* \
        && "$command" == *"run start"* \
        && "$command" == *"--port 3000"* ]]; then
        return 0
      fi
      return 1
      ;;
    *)
      return 1
      ;;
  esac
}

pid_serves_service() {
  local service="$1"
  local pid="$2"
  pid_matches_service "$service" "$pid" || return 1
  case "$service" in
    backend)
      pid_listens_on_port "$pid" 8001
      ;;
    frontend)
      process_tree_listens_on_port "$pid" 3000
      ;;
    *)
      return 1
      ;;
  esac
}

managed_pid() {
  local service="$1"
  local pid_file="$2"
  local pid
  pid="$(read_pid "$pid_file")"
  if pid_matches_service "$service" "$pid"; then
    printf '%s\n' "$pid"
  fi
}

managed_serving_pid() {
  local service="$1"
  local pid_file="$2"
  local pid
  pid="$(read_pid "$pid_file")"
  if pid_serves_service "$service" "$pid"; then
    printf '%s\n' "$pid"
  fi
}

collect_process_tree() {
  local parent_pid="$1"
  local child_pid
  local children
  children="$(/bin/ps -axo pid=,ppid= \
    | /usr/bin/awk -v parent="$parent_pid" '$2 == parent {print $1}')"
  for child_pid in ${(f)children}; do
    [[ -n "$child_pid" ]] || continue
    collect_process_tree "$child_pid"
  done
  MANAGED_PROCESS_TREE+=("$parent_pid")
}

stop_managed_service() {
  local service="$1"
  local label="$2"
  local pid_file="$3"
  local pid process_id index still_running
  pid="$(managed_pid "$service" "$pid_file")"
  if [[ -z "$pid" ]]; then
    return 1
  fi

  log "Stopping the launcher-owned $label (PID $pid)."
  MANAGED_PROCESS_TREE=()
  collect_process_tree "$pid"
  for process_id in "${MANAGED_PROCESS_TREE[@]}"; do
    /bin/kill -TERM "$process_id" >/dev/null 2>&1 || true
  done

  index=0
  while (( index < 40 )); do
    still_running=0
    for process_id in "${MANAGED_PROCESS_TREE[@]}"; do
      if /bin/kill -0 "$process_id" >/dev/null 2>&1; then
        still_running=1
        break
      fi
    done
    (( still_running == 0 )) && break
    /bin/sleep 0.25
    (( index += 1 ))
  done

  if (( still_running != 0 )); then
    log "The launcher-owned $label did not stop after 10 seconds; refusing to start a duplicate."
    return 1
  fi

  /bin/rm -f "$pid_file"
  return 0
}

write_runtime_value() {
  local destination="$1"
  local value="$2"
  local temporary="$destination.$$"
  printf '%s\n' "$value" > "$temporary"
  /bin/mv -f "$temporary" "$destination"
}

refuse_unmanaged_healthy_service() {
  local service="$1"
  local label="$2"
  local pid_file="$3"
  local status_url="$4"

  if is_ready "$status_url" && [[ -z "$(managed_serving_pid "$service" "$pid_file")" ]]; then
    log "$label is healthy but is not identified by the launcher PID file; refusing to stop or replace it."
    notify "$label is running outside the launcher. Close it, then click again."
    exit 1
  fi
}

read_lock_owner() {
  local owner
  if [[ -f "$LOCK_PATH" ]]; then
    owner="$(read_pid "$LOCK_PATH")"
  elif [[ -d "$LOCK_PATH" ]]; then
    # Compatibility with the short-lived directory-lock launcher version.
    owner="$(read_pid "$LOCK_PATH/owner.pid")"
  fi
  if [[ "$owner" == <-> ]]; then
    printf '%s\n' "$owner"
  fi
}

try_create_launcher_lock() {
  # noclobber opens the fixed file with O_EXCL: creating the path and storing
  # its owner PID are one atomic operation.
  setopt local_options noclobber
  { printf '%s\n' "$$" > "$LOCK_PATH" } 2>/dev/null
}

pid_matches_launcher() {
  local pid="$1"
  local command cwd
  [[ "$pid" == <-> ]] || return 1
  /bin/kill -0 "$pid" >/dev/null 2>&1 || return 1
  command="$(/bin/ps -p "$pid" -o command= 2>/dev/null || true)"
  cwd="$(process_cwd "$pid")"
  [[ "$cwd" == "$ROOT_DIR" && "$command" == *"launch_local_app.sh"* ]]
}

release_launcher_lock() {
  if [[ -f "$LOCK_PATH" && "$(read_lock_owner)" == "$$" ]]; then
    /bin/rm -f "$LOCK_PATH"
  fi
}

cd "$ROOT_DIR" || exit 1

# The PID file is created with O_EXCL. Directory locks are read only for
# seamless migration from the short-lived earlier implementation.
if ! try_create_launcher_lock; then
  lock_wait_index=0
  lock_owner="$(read_lock_owner)"
  while [[ -z "$lock_owner" && -d "$LOCK_PATH" && lock_wait_index -lt 10 ]]; do
    /bin/sleep 0.1
    (( lock_wait_index += 1 ))
    lock_owner="$(read_lock_owner)"
  done

  if pid_matches_launcher "$lock_owner"; then
    log "Another launcher process (PID $lock_owner) is already starting the app."
    notify "Listening Archive is still starting and will open when ready."
    exit 0
  fi

  stale_lock_path="$RUNTIME_DIR/launcher.lock.stale.$$"
  if /bin/mv "$LOCK_PATH" "$stale_lock_path" 2>/dev/null; then
    if [[ -f "$stale_lock_path" || -L "$stale_lock_path" ]]; then
      /bin/rm -f "$stale_lock_path"
    elif [[ -d "$stale_lock_path" ]]; then
      /bin/rm -f "$stale_lock_path/owner.pid"
      /bin/rmdir "$stale_lock_path" >/dev/null 2>&1 || true
    fi
    log "Reclaimed a stale launcher lock."
  fi

  if ! try_create_launcher_lock; then
    log "Another launcher acquired the lock first."
    notify "Listening Archive is already starting."
    exit 0
  fi
fi
trap release_launcher_lock EXIT

log "Launcher invoked."

# Pull fresh history when the checkout is clean. Network or authentication
# failures do not prevent the last local snapshot from opening. Do this before
# reusing healthy services: the API discovers the monthly databases per request,
# so a click can refresh the archive without restarting either process.
if [[ -z "$(git status --porcelain --untracked-files=no 2>/dev/null)" ]]; then
  log "Checking GitHub for newer listening history."
  git pull --ff-only origin main >> "$LAUNCH_LOG" 2>&1 || log "Git pull failed; continuing with the local snapshot."
else
  log "Skipping git pull because the checkout has local changes."
fi

BACKEND_PID_FILE="$RUNTIME_DIR/backend.pid"
FRONTEND_PID_FILE="$RUNTIME_DIR/frontend.pid"
BACKEND_RUNNING_FINGERPRINT_FILE="$RUNTIME_DIR/backend.fingerprint"
FRONTEND_RUNNING_FINGERPRINT_FILE="$RUNTIME_DIR/frontend.fingerprint"

# A healthy response is reusable only when the private PID file still points to
# the expected process in this checkout. Never kill a process merely because it
# happens to own one of the app's ports.
refuse_unmanaged_healthy_service backend "The API" "$BACKEND_PID_FILE" "$API_STATUS_URL"
refuse_unmanaged_healthy_service frontend "The web app" "$FRONTEND_PID_FILE" "$FRONTEND_STATUS_URL"

backend_source_fingerprint="$(source_fingerprint api requirements.txt)"
if [[ -f "$ROOT_DIR/.env" ]]; then
  backend_environment_fingerprint="$(/usr/bin/shasum "$ROOT_DIR/.env" | /usr/bin/awk '{print $1}')"
else
  backend_environment_fingerprint="missing"
fi
backend_fingerprint="$(printf '%s:%s\n' \
  "$backend_source_fingerprint" \
  "$backend_environment_fingerprint" \
  | /usr/bin/shasum \
  | /usr/bin/awk '{print $1}')"
web_fingerprint="$(source_fingerprint \
  web/src \
  web/public \
  web/package.json \
  web/package-lock.json \
  web/next.config.ts \
  web/postcss.config.mjs \
  web/tsconfig.json)"

venv_created=0
if [[ ! -x "$ROOT_DIR/.venv/bin/python" ]]; then
  log "Creating the Python environment."
  /usr/bin/python3 -m venv "$ROOT_DIR/.venv" >> "$LAUNCH_LOG" 2>&1 || {
    notify "Could not create the Python environment. See .run/launcher.log."
    exit 1
  }
  venv_created=1
fi

requirements_fingerprint="$(/usr/bin/shasum "$ROOT_DIR/requirements.txt" | /usr/bin/awk '{print $1}')"
installed_requirements="$(cat "$RUNTIME_DIR/requirements.fingerprint" 2>/dev/null || true)"
if (( venv_created == 1 )) || [[ "$requirements_fingerprint" != "$installed_requirements" ]]; then
  if [[ -n "$(managed_pid backend "$BACKEND_PID_FILE")" ]]; then
    stop_managed_service backend "API" "$BACKEND_PID_FILE" || {
      notify "The old API could not be stopped safely. See .run/launcher.log."
      exit 1
    }
  fi
  log "Installing Python dependencies."
  "$ROOT_DIR/.venv/bin/pip" install -r "$ROOT_DIR/requirements.txt" >> "$LAUNCH_LOG" 2>&1 || {
    notify "Python dependencies could not be installed. See .run/launcher.log."
    exit 1
  }
  write_runtime_value "$RUNTIME_DIR/requirements.fingerprint" "$requirements_fingerprint"
fi

if [[ ! -x "$NODE_PATH" || ! -x "$NPM_PATH" ]]; then
  log "Node.js was not found at /opt/homebrew/bin."
  notify "Node.js is missing. Install it with Homebrew, then click again."
  exit 1
fi

current_build_id="$(cat "$WEB_DIR/.next/BUILD_ID" 2>/dev/null || true)"
built_web_fingerprint="$(cat "$RUNTIME_DIR/web.fingerprint" 2>/dev/null || true)"
expected_web_build_fingerprint="v2:$web_fingerprint:$current_build_id:$API_PROXY_TARGET"
if [[ ! -d "$WEB_DIR/node_modules" \
  || -z "$current_build_id" \
  || "$expected_web_build_fingerprint" != "$built_web_fingerprint" ]]; then
  if [[ -n "$(managed_pid frontend "$FRONTEND_PID_FILE")" ]]; then
    stop_managed_service frontend "web app" "$FRONTEND_PID_FILE" || {
      notify "The old web app could not be stopped safely. See .run/launcher.log."
      exit 1
    }
  fi
  log "Preparing the production web app."
  (
    cd "$WEB_DIR" || exit 1
    "$NPM_PATH" install --no-audit --no-fund
    API_PROXY_TARGET="$API_PROXY_TARGET" "$NPM_PATH" run build
  ) >> "$LAUNCH_LOG" 2>&1 || {
    notify "The web app could not be built. See .run/launcher.log."
    exit 1
  }
  current_build_id="$(cat "$WEB_DIR/.next/BUILD_ID" 2>/dev/null || true)"
  if [[ -z "$current_build_id" ]]; then
    log "The web build completed without a BUILD_ID."
    notify "The web build is incomplete. See .run/launcher.log."
    exit 1
  fi
  built_web_fingerprint="v2:$web_fingerprint:$current_build_id:$API_PROXY_TARGET"
  write_runtime_value "$RUNTIME_DIR/web.fingerprint" "$built_web_fingerprint"
fi

running_backend_fingerprint="$(cat "$BACKEND_RUNNING_FINGERPRINT_FILE" 2>/dev/null || true)"
if is_ready "$API_STATUS_URL" && [[ "$backend_fingerprint" != "$running_backend_fingerprint" ]]; then
  log "API source changed; restarting it before reuse."
  stop_managed_service backend "API" "$BACKEND_PID_FILE" || {
    notify "The old API could not be stopped safely. See .run/launcher.log."
    exit 1
  }
fi

if ! is_ready "$API_STATUS_URL"; then
  if [[ -n "$(managed_pid backend "$BACKEND_PID_FILE")" ]]; then
    stop_managed_service backend "unhealthy API" "$BACKEND_PID_FILE" || {
      notify "The unhealthy API could not be stopped safely. See .run/launcher.log."
      exit 1
    }
  fi
  log "Starting the API on port 8001."
  /usr/bin/nohup "$ROOT_DIR/.venv/bin/python" -m uvicorn api.main:app \
    --host 127.0.0.1 --port 8001 >> "$BACKEND_LOG" 2>&1 < /dev/null &
  write_runtime_value "$BACKEND_PID_FILE" "$!"
fi

if ! wait_until_ready "$API_STATUS_URL" 40; then
  log "The API did not become ready."
  notify "The API did not start. See .run/backend.log."
  exit 1
fi
if [[ -z "$(managed_serving_pid backend "$BACKEND_PID_FILE")" ]]; then
  log "The API endpoint became ready, but the launcher-owned API process is not serving port 8001."
  notify "A different process owns the API port. See .run/launcher.log."
  exit 1
fi
write_runtime_value "$BACKEND_RUNNING_FINGERPRINT_FILE" "$backend_fingerprint"

running_frontend_fingerprint="$(cat "$FRONTEND_RUNNING_FINGERPRINT_FILE" 2>/dev/null || true)"
if is_ready "$FRONTEND_STATUS_URL" && [[ "$web_fingerprint" != "$running_frontend_fingerprint" ]]; then
  log "Web source changed; restarting it before reuse."
  stop_managed_service frontend "web app" "$FRONTEND_PID_FILE" || {
    notify "The old web app could not be stopped safely. See .run/launcher.log."
    exit 1
  }
fi

if ! is_ready "$FRONTEND_STATUS_URL"; then
  if [[ -n "$(managed_pid frontend "$FRONTEND_PID_FILE")" ]]; then
    stop_managed_service frontend "unhealthy web app" "$FRONTEND_PID_FILE" || {
      notify "The unhealthy web app could not be stopped safely. See .run/launcher.log."
      exit 1
    }
  fi
  log "Starting the web app on port 3000."
  (
    cd "$WEB_DIR" || exit 1
    exec /usr/bin/nohup /usr/bin/env API_PROXY_TARGET="$API_PROXY_TARGET" \
      "$NODE_PATH" "$WEB_DIR/node_modules/next/dist/bin/next" start \
      --hostname 127.0.0.1 --port 3000
  ) >> "$FRONTEND_LOG" 2>&1 < /dev/null &
  write_runtime_value "$FRONTEND_PID_FILE" "$!"
fi

if ! wait_until_ready "$FRONTEND_STATUS_URL" 60; then
  log "The web app did not become ready."
  notify "The web app did not start. See .run/frontend.log."
  exit 1
fi
if [[ -z "$(managed_serving_pid frontend "$FRONTEND_PID_FILE")" ]]; then
  log "The web endpoint became ready, but the launcher-owned web process tree is not serving port 3000."
  notify "A different process owns the web port. See .run/launcher.log."
  exit 1
fi
if ! wait_until_ready "$FRONTEND_API_STATUS_URL" 20; then
  log "The web app is up, but its API proxy did not reach port 8001."
  notify "The web app could not reach its API. See .run/frontend.log."
  exit 1
fi
write_runtime_value "$FRONTEND_RUNNING_FINGERPRINT_FILE" "$web_fingerprint"

log "Listening Archive is ready."
notify "Ready — opening in Chrome."
open_archive
