#!/usr/bin/env bash
#
# refresh-youtube-cookies.sh — mint a fresh Netscape cookies.txt from a local
# browser (or import an existing export) and swap it atomically into the
# Dify docker host's yt-dlp cookie file used by the plugin daemon's
# server-side fallback.
#
# Safety properties:
#   - cookie contents are never printed (header lines and counts only)
#   - replacement is atomic: hidden staging file + mv, mode 0600
#   - no container restart; the plugin reads the file on its next invocation
#   - staging copies are removed on both machines
#
# Usage (run from the machine where you are logged into YouTube):
#   refresh-youtube-cookies.sh -s user@dify-host -d /srv/dify/docker
#   refresh-youtube-cookies.sh -s user@dify-host -d /srv/dify/docker -b chrome
#   refresh-youtube-cookies.sh -s user@dify-host -d /srv/dify/docker \
#     -f ~/Downloads/exported-cookies.txt      # extension export instead
#
# Requirements on the local machine: yt-dlp (only when not using -f), ssh.
# Requirements on the remote host: the Dify docker/ directory with
# docker-compose.yaml, docker compose v2, the running plugin_daemon service.

set -euo pipefail

# --- defaults (override via environment or flags) ---------------------------
BROWSER="${DIFY_COOKIE_BROWSER:-safari}"
SSH_TARGET="${DIFY_SSH_TARGET:-}"
DOCKER_DIR="${DIFY_DOCKER_DIR:-}"
HOST_COOKIE_DIR="${DIFY_HOST_COOKIE_DIR:-secrets/yt-dlp}"
HOST_COOKIE_FILE="${DIFY_HOST_COOKIE_FILE:-${HOST_COOKIE_DIR}/youtube-cookies.txt}"
CONTAINER_FILE="${DIFY_CONTAINER_COOKIE_FILE:-/etc/dify/yt-dlp/youtube-cookies.txt}"
COMPOSE_SERVICE="${DIFY_PLUGIN_SERVICE:-plugin_daemon}"
PROBE_URL="${DIFY_PROBE_URL:-https://www.youtube.com/}"
IMPORT_FILE=""
ASSUME_YES=0

REMOTE_STAGING="/tmp/dify-refresh-cookies.$$.txt"

err() { printf 'ERROR: %s\n' "$*" >&2; }
warn() { printf 'WARN:  %s\n' "$*" >&2; }
info() { printf -- '-> %s\n' "$*"; }

usage() {
  cat <<'EOF'
Refresh the Dify plugin daemon's server-side YouTube cookies.

Options:
  -s TARGET   ssh target of the Dify docker host            (user@host)
  -d DIR      path to the Dify docker/ directory on that host
  -b BROWSER  browser to mint cookies from (default: safari; chrome, edge,
              firefox, brave, chromium also work)
  -f FILE     import an existing Netscape export instead of minting
  -y          skip the "replace?" confirmation (for scheduled runs)
  -h          this help

Environment overrides: DIFY_SSH_TARGET, DIFY_DOCKER_DIR, DIFY_COOKIE_BROWSER,
DIFY_HOST_COOKIE_FILE, DIFY_CONTAINER_COOKIE_FILE, DIFY_PLUGIN_SERVICE,
DIFY_PROBE_URL (see defaults in the script header).
EOF
}

validate_cookie_file() {
  local file=$1 rows header
  if [[ ! -s "$file" ]]; then
    err "cookie file is empty or missing: $file"
    return 1
  fi
  header=$(head -n 1 "$file")
  if [[ "$header" != *"HTTP Cookie File"* ]]; then
    err "not a Netscape cookies.txt export (first line: ${header})"
    return 1
  fi
  rows=$(wc -l < "$file" | tr -d ' ')
  if (( rows < 5 )); then
    err "only ${rows} lines in the export; not a usable cookie file"
    return 1
  fi
  # Header line contains no cookie values; line/byte counts only.
  printf 'local file OK: %s lines · %s bytes\n' "$rows" "$(wc -c < "$file" | tr -d ' ')"
}

while getopts ":s:d:b:f:yh" opt; do
  case "$opt" in
    s) SSH_TARGET=$OPTARG ;;
    d) DOCKER_DIR=$OPTARG ;;
    b) BROWSER=$OPTARG ;;
    f) IMPORT_FILE=$OPTARG ;;
    y) ASSUME_YES=1 ;;
    h) usage; exit 0 ;;
    :) err "option -$OPTARG requires a value"; usage >&2; exit 2 ;;
    *) err "unknown option -$OPTARG"; usage >&2; exit 2 ;;
  esac
done

[[ -n "$SSH_TARGET" ]] || { err "missing -s user@host (Dify docker host)"; exit 2; }
[[ -n "$DOCKER_DIR" ]] || { err "missing -d /path/to/dify/docker"; exit 2; }
command -v ssh >/dev/null 2>&1 || { err "ssh not found"; exit 1; }
command -v scp >/dev/null 2>&1 || { err "scp not found"; exit 1; }

# --- step 1: obtain a fresh local export ------------------------------------
TMP_DIR=""
if [[ -n "$IMPORT_FILE" ]]; then
  SOURCE_FILE=$IMPORT_FILE
else
  command -v yt-dlp >/dev/null 2>&1 || {
    err "yt-dlp not found; install it (e.g. brew install yt-dlp) or pass -f with an existing export"
    exit 1
  }
  info "minting fresh cookies from browser '$BROWSER' (close the browser first — its cookie DB is locked)"
  TMP_DIR=$(mktemp -d "${TMPDIR:-/tmp}/dify-cookies.XXXXXX")
  # shellcheck disable=SC2064
  trap '[[ -n "$TMP_DIR" ]] && rm -rf "$TMP_DIR"' EXIT
  SOURCE_FILE="$TMP_DIR/youtube-cookies.txt"
  yt-dlp \
    --cookies-from-browser "$BROWSER" \
    --cookies "$SOURCE_FILE" \
    --skip-download \
    "$PROBE_URL" || {
      err "yt-dlp could not mint cookies (browser locked or needs re-login?); retry, or use -f with an extension export"
      exit 1
    }
fi

validate_cookie_file "$SOURCE_FILE"

# --- step 2: confirmation ----------------------------------------------------
if (( ASSUME_YES != 1 )); then
  read -r -p "Replace cookies on '$SSH_TARGET' ($(basename "$DOCKER_DIR")/${HOST_COOKIE_FILE})? [y/N] " answer
  [[ "$answer" =~ ^[Yy] ]] || { info "aborted; nothing changed"; exit 0; }
fi

# --- step 3: ship to remote staging ------------------------------------------
info "copying to $SSH_TARGET:$REMOTE_STAGING"
scp -q "$SOURCE_FILE" "$SSH_TARGET:$REMOTE_STAGING"

# --- step 4: atomic swap + cleanup on the remote host -------------------------
info "installing atomically into ${DOCKER_DIR}/${HOST_COOKIE_FILE} (mode 600)"
{
  printf 'set -eu\n'
  printf 'cd %q\n' "$DOCKER_DIR"
  printf 'mkdir -p %q\n' "$HOST_COOKIE_DIR"
  printf 'chmod 700 %q\n' "$HOST_COOKIE_DIR"
  printf 'install -m 600 %q %q\n' "$REMOTE_STAGING" "${HOST_COOKIE_DIR}/.youtube-cookies.txt.new"
  printf 'mv -f %q %q\n' "${HOST_COOKIE_DIR}/.youtube-cookies.txt.new" "$HOST_COOKIE_FILE"
  printf 'rm -f %q\n' "$REMOTE_STAGING"
  printf 'ls -l %q\n' "$HOST_COOKIE_FILE"
} | ssh "$SSH_TARGET" sh

# --- step 5: verify from inside the running plugin daemon ---------------------
info "verifying inside the ${COMPOSE_SERVICE} container (nothing content-sensitive is printed)"
if ssh "$SSH_TARGET" "cd '$DOCKER_DIR' && docker compose exec -T $COMPOSE_SERVICE sh -c \
  'head -n 1 $CONTAINER_FILE; test -s $CONTAINER_FILE && echo REACHABLE'"; then
  info "cookies refreshed and reachable; no restart needed — the plugin reads the file on its next invocation"
  info "finish by running the workflow API curl end-to-end (see deploy/daemon-cookies/README.md step 3)"
else
  warn "could not verify via docker compose exec (compose down or service name differs)"
  warn "check manually on $SSH_TARGET:${DOCKER_DIR} — file install itself already succeeded"
  exit 1
fi