#!/usr/bin/env bash
#
# Start Sunet Scribe for local development: scribe-backend and scribe-ui from
# main, with a fake OIDC server standing in for the login provider.
#
#   ./launch_dev_env.sh          start everything, Ctrl-C stops it
#   ./launch_dev_env.sh reset    forget the database, files and secrets first
#   ./launch_dev_env.sh --macos-deps
#                                install what is missing with Homebrew, then start
#
# The two clones, scribe-backend/ and scribe-ui/, sit next to this script: edit
# them and the backend (uvicorn --reload) and the UI (NiceGUI reload) restart
# on their own. Database, files, secrets and logs live in .dev/.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEV="$HERE/.dev"
SUPPORT="$HERE/support"   # fake OIDC server, seeding
BACKEND_REPO="${BACKEND_REPO:-https://github.com/SUNET/scribe-backend}"
UI_REPO="${UI_REPO:-https://github.com/SUNET/scribe-ui}"
BRANCH="${BRANCH:-main}"

OIDC_PORT="${OIDC_PORT:-9000}"
BACKEND_PORT="${BACKEND_PORT:-8000}"
UI_PORT=8888  # hard-coded in scribe-ui main.py
DEV_USER="${DEV_USER:-dev@example.com}"
# What the first login asks for; the sample files are encrypted with it.
DEV_PASSPHRASE="${DEV_PASSPHRASE:-devpassphrase}"

OIDC="http://localhost:$OIDC_PORT"
BACKEND="http://localhost:$BACKEND_PORT"
UI="http://localhost:$UI_PORT"

say() { printf '\033[1m==> %s\033[0m\n' "$*"; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }

RESET=0
MACOS_DEPS=0
for arg in "$@"; do
    case "$arg" in
        reset | --reset) RESET=1 ;;
        --macos-deps) MACOS_DEPS=1 ;;
        -h | --help) sed -n '2,/^$/p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) die "unknown argument: $arg (try --help)" ;;
    esac
done

# command -> Homebrew formula
brew_formula() {
    case "$1" in
        python3) echo python ;;
        *) echo "$1" ;;
    esac
}

if [ "$MACOS_DEPS" = 1 ]; then
    [ "$(uname -s)" = Darwin ] || die "--macos-deps is for macOS only"
    command -v brew >/dev/null \
        || die "Homebrew is not installed; get it from https://brew.sh and run this again"
    for tool in git uv curl python3; do
        if command -v "$tool" >/dev/null; then
            continue
        fi
        say "Installing $(brew_formula "$tool") with Homebrew"
        brew install "$(brew_formula "$tool")"
    done
fi

for tool in git uv curl python3; do
    command -v "$tool" >/dev/null \
        || die "$tool is required (on a Mac: ./launch_dev_env.sh --macos-deps)"
done

if [ "$RESET" = 1 ]; then
    say "Removing database, files and generated settings"
    rm -rf "$DEV/data" "$DEV/secrets" "$DEV/logs"
fi

mkdir -p "$DEV/data/files" "$DEV/logs"

# --- source ----------------------------------------------------------------

# These are ordinary clones next to this script, meant to be edited. A clone
# with local changes, or on another branch, is left exactly as it is.
checkout() {
    local url="$1" dir="$2" name
    name="$(basename "$dir")"
    if [ -d "$dir/.git" ]; then
        if [ -n "$(git -C "$dir" status --porcelain)" ] \
            || [ "$(git -C "$dir" branch --show-current)" != "$BRANCH" ]; then
            say "Using $name as it is (local changes or not on $BRANCH)"
        else
            say "Updating $name ($BRANCH)"
            git -C "$dir" pull --quiet --ff-only origin "$BRANCH" \
                || say "Could not update $name; using what is there"
        fi
    else
        say "Cloning $name ($BRANCH)"
        git clone --quiet --branch "$BRANCH" "$url" "$dir"
    fi
}

checkout "$BACKEND_REPO" "$HERE/scribe-backend"
checkout "$UI_REPO" "$HERE/scribe-ui"

# --- settings --------------------------------------------------------------

# Random secrets, made once and kept so a restart does not lock the database
# out of its own encrypted files. The env files are rewritten on every run, so
# a changed port or a new default here always takes effect.
secret() { python3 -c 'import secrets; print(secrets.token_hex(24))'; }

if [ ! -f "$DEV/secrets" ]; then
    cat > "$DEV/secrets" <<EOF
API_SECRET_KEY=$(secret)
API_PRIVATE_KEY_PASSWORD=$(secret)
STORAGE_SECRET=$(secret)
EOF
fi
. "$DEV/secrets"

cat > "$DEV/backend.env" <<EOF
API_DATABASE_URL=sqlite:///$DEV/data/jobs.db
API_DEBUG=True
API_FILE_STORAGE_DIR=$DEV/data/files
API_SECRET_KEY=$API_SECRET_KEY
API_PRIVATE_KEY_PASSWORD=$API_PRIVATE_KEY_PASSWORD
API_CLIENT_VERIFICATION_ENABLED=False
OIDC_CLIENT_ID=scribe
OIDC_CLIENT_SECRET=dev-secret
OIDC_SCOPE=openid,profile,email
OIDC_METADATA_URL=$OIDC/.well-known/openid-configuration
OIDC_REFRESH_URI=$OIDC/token
OIDC_REDIRECT_URI=$BACKEND/api/auth
OIDC_FRONTEND_URI=$UI
EOF

cat > "$DEV/ui.env" <<EOF
API_URL=$BACKEND
OIDC_APP_LOGIN_ROUTE=$BACKEND/api/login
OIDC_APP_LOGOUT_ROUTE=$BACKEND/api/logout
OIDC_APP_REFRESH_ROUTE=$BACKEND/api/refresh
OIDC_APP_EXCHANGE_ROUTE=$BACKEND/api/auth/exchange
STORAGE_SECRET=$STORAGE_SECRET
HTTPS_ONLY_COOKIES=false
EOF

load_env() { set -a; . "$1"; set +a; }

# --- processes -------------------------------------------------------------

PIDS=()

# Kill a process and everything it started (uvicorn --reload and NiceGUI both
# run the app in a child).
kill_tree() {
    local pid="$1" child
    for child in $(pgrep -P "$pid" 2>/dev/null || true); do
        kill_tree "$child"
    done
    kill "$pid" 2>/dev/null || true
}

cleanup() {
    trap - EXIT INT TERM
    say "Stopping"
    for pid in "${PIDS[@]:-}"; do
        [ -n "$pid" ] && kill_tree "$pid"
    done
    wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM

# start NAME DIR ENVFILE COMMAND... -- ENVFILE ("" for none) is exported into
# the process only; it wins over any .env file in DIR.
start() {
    local name="$1" dir="$2" envfile="$3"; shift 3
    (
        if [ -n "$envfile" ]; then set -a; . "$envfile"; set +a; fi
        cd "$dir"
        exec "$@"
    ) >"$DEV/logs/$name.log" 2>&1 &
    PIDS+=("$!")
}

wait_for() {
    local name="$1" url="$2"
    for _ in $(seq 1 60); do
        curl -fs -o /dev/null "$url" && return 0
        sleep 1
    done
    die "$name did not come up; see $DEV/logs/$name.log"
}

for port in "$OIDC_PORT" "$BACKEND_PORT" "$UI_PORT"; do
    if curl -s -o /dev/null "http://localhost:$port"; then
        die "port $port is already in use"
    fi
done

say "Installing dependencies"
(cd "$HERE/scribe-backend" && uv sync --quiet)
(cd "$HERE/scribe-ui" && uv sync --quiet)
(cd "$SUPPORT/fake_oidc" && uv sync --quiet)

say "Starting the fake OIDC server on $OIDC"
FAKE_OIDC_USER="$DEV_USER" start oidc "$SUPPORT/fake_oidc" "" uv run python server.py "$OIDC_PORT"
wait_for oidc "$OIDC/.well-known/openid-configuration"

say "Preparing the database"
(
    load_env "$DEV/backend.env"
    cd "$HERE/scribe-backend"
    PYTHONPATH="$PWD" uv run --quiet python "$SUPPORT/seed_user.py" "$DEV_USER"
    PYTHONPATH="$PWD" uv run --quiet python "$SUPPORT/seed_samples.py" \
        "$DEV_USER" "$DEV_PASSPHRASE"
    # Tables exist now, so mark the migrations as applied rather than run them.
    uv run --quiet alembic stamp head >/dev/null
)

say "Starting the backend on $BACKEND"
start backend "$HERE/scribe-backend" "$DEV/backend.env" \
    uv run uvicorn app:app --port "$BACKEND_PORT" \
    --reload --reload-dir "$HERE/scribe-backend"
wait_for backend "$BACKEND/api/docs"

say "Starting the UI on $UI"
start ui "$HERE/scribe-ui" "$DEV/ui.env" uv run python main.py
wait_for ui "$UI"

cat <<EOF

  Scribe is up:   $UI
  Sign in as:     $DEV_USER (admin and BOFH; the fake login has no form)
  Passphrase:     $DEV_PASSPHRASE (asked for after signing in)
  Backend docs:   $BACKEND/api/docs
  Logs:           $DEV/logs/

  Ctrl-C stops everything.

EOF

wait
