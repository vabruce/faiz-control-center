#!/usr/bin/env bash
# Edge-accelerated remote installer for Faiz Control Center (`faiz-dash`).
set -euo pipefail

FAIZ_REPO_URL="${FAIZ_REPO_URL:-https://github.com/vabruce/faiz-control-center.git}"
FAIZ_TARBALL_URL="${FAIZ_TARBALL_URL:-https://github.com/vabruce/faiz-control-center/archive/refs/heads}"
FAIZ_BRANCH="${FAIZ_BRANCH:-main}"
FAIZ_INSTALL_DIR="${FAIZ_INSTALL_DIR:-$HOME/.local/share/faiz-control-center}"
FAIZ_INSTALL_MODE="${FAIZ_INSTALL_MODE:-link}"
FAIZ_WIRE_SETTINGS="${FAIZ_WIRE_SETTINGS:-0}"
DRY_RUN="${DRY_RUN:-0}"

_log() {
    printf '[faiz-install] %s\n' "$*"
}

_fail() {
    printf '[faiz-install] ERROR: %s\n' "$*" >&2
    exit 1
}

_check_python() {
    local py=""
    for cand in "${FAIZ_PYTHON:-}" python3.13 python3.12 python3; do
        [ -n "$cand" ] || continue
        if command -v "$cand" >/dev/null 2>&1; then
            if "$cand" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)' 2>/dev/null; then
                py="$(command -v "$cand")"
                echo "$py"
                return 0
            fi
        fi
    done
    return 1
}

PY_BIN="$(_check_python || true)"
[ -n "$PY_BIN" ] || _fail "Python 3.12+ is required on PATH (or set FAIZ_PYTHON)."

command -v jq >/dev/null 2>&1 || [ -x /usr/bin/jq ] || _fail "jq is required (install via 'brew install jq' or apt/dnf)."

case "$FAIZ_INSTALL_MODE" in
    link|copy) ;;
    *) _fail "FAIZ_INSTALL_MODE must be 'link' or 'copy' (got '$FAIZ_INSTALL_MODE')." ;;
esac

if [ "$DRY_RUN" = "1" ]; then
    _log "DRY_RUN=1: no files will be modified."
    _log "Python: $PY_BIN"
    _log "Repo: $FAIZ_REPO_URL (branch: $FAIZ_BRANCH)"
    _log "Target directory: $FAIZ_INSTALL_DIR"
    _log "Install mode: --$FAIZ_INSTALL_MODE"
    _log "Planned steps:"
    _log "  1. Fetch or update repository into $FAIZ_INSTALL_DIR"
    _log "  2. Create isolated virtualenv at $FAIZ_INSTALL_DIR/.venv and install package (-e .)"
    if [ "$FAIZ_WIRE_SETTINGS" = "1" ]; then
        _log "  3. Run $FAIZ_INSTALL_DIR/install.sh --$FAIZ_INSTALL_MODE --wire-settings"
    else
        _log "  3. Run $FAIZ_INSTALL_DIR/install.sh --$FAIZ_INSTALL_MODE"
    fi
    exit 0
fi

mkdir -p "$(dirname "$FAIZ_INSTALL_DIR")"

if command -v git >/dev/null 2>&1; then
    if [ -d "$FAIZ_INSTALL_DIR/.git" ]; then
        _log "Updating existing checkout in $FAIZ_INSTALL_DIR..."
        git -C "$FAIZ_INSTALL_DIR" fetch --depth 1 origin "$FAIZ_BRANCH"
        git -C "$FAIZ_INSTALL_DIR" checkout -q "$FAIZ_BRANCH" 2>/dev/null || true
        git -C "$FAIZ_INSTALL_DIR" pull --ff-only origin "$FAIZ_BRANCH"
    else
        rm -rf "$FAIZ_INSTALL_DIR"
        _log "Cloning $FAIZ_REPO_URL ($FAIZ_BRANCH) -> $FAIZ_INSTALL_DIR..."
        git clone --depth 1 --branch "$FAIZ_BRANCH" "$FAIZ_REPO_URL" "$FAIZ_INSTALL_DIR"
    fi
elif command -v curl >/dev/null 2>&1 && command -v tar >/dev/null 2>&1; then
    _log "git not found; downloading source archive..."
    tmp_tgz="$(mktemp)"
    tmp_ext="$(mktemp -d)"
    trap 'rm -rf "$tmp_tgz" "$tmp_ext"' EXIT
    curl -fsSL "$FAIZ_TARBALL_URL/$FAIZ_BRANCH.tar.gz" -o "$tmp_tgz"
    tar -xzf "$tmp_tgz" -C "$tmp_ext"
    extracted="$(find "$tmp_ext" -mindepth 1 -maxdepth 1 -type d | head -n 1)"
    [ -n "$extracted" ] || _fail "Failed to extract source archive."
    rm -rf "$FAIZ_INSTALL_DIR"
    mv "$extracted" "$FAIZ_INSTALL_DIR"
else
    _fail "Either git or curl+tar is required to fetch Faiz Control Center."
fi

VENV_DIR="$FAIZ_INSTALL_DIR/.venv"
_log "Setting up isolated virtualenv in $VENV_DIR..."
if command -v uv >/dev/null 2>&1; then
    uv venv --python "$PY_BIN" "$VENV_DIR"
    uv pip install --python "$VENV_DIR/bin/python" -e "$FAIZ_INSTALL_DIR"
else
    "$PY_BIN" -m venv "$VENV_DIR"
    "$VENV_DIR/bin/python" -m pip install --upgrade pip
    "$VENV_DIR/bin/python" -m pip install -e "$FAIZ_INSTALL_DIR"
fi

chmod +x "$FAIZ_INSTALL_DIR/install.sh"
install_flags=("--$FAIZ_INSTALL_MODE")
[ "$FAIZ_WIRE_SETTINGS" = "1" ] && install_flags+=(--wire-settings)
"$FAIZ_INSTALL_DIR/install.sh" "${install_flags[@]}"

case ":${PATH}:" in
    *":$HOME/.local/bin:"*) ;;
    *)
        printf '\n[faiz-install] NOTE: Add ~/.local/bin to PATH:\n'
        printf '  export PATH="$HOME/.local/bin:$PATH"\n'
        ;;
esac

_log "Done! Try running: faiz-dash inspect"
