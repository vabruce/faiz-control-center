#!/usr/bin/env bash
# Installer for Faiz Control Center (`faiz-dash`) & Antigravity CLI StatusLine.
set -euo pipefail

REPO_DIR="$(cd -P "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MODE="link"
WIRE_SETTINGS=0
SETTINGS_PATH_ARG=""

usage() {
    cat <<'EOF'
Usage: ./install.sh [--link | --copy] [--wire-settings] [--settings-path <path>] [--help]

Options:
  --link                 Symlink repo scripts/skills into ~/.agents and ~/.local/bin (recommended for development)
  --copy                 Copy scripts/skills into ~/.agents and ~/.local/bin (standalone install)
  --wire-settings        Safely merge statusLine config into ~/.gemini/antigravity-cli/settings.json (with .bak backup)
  --settings-path <path> Override settings.json target path when using --wire-settings
  -h, --help             Show this help message
EOF
}

while [ $# -gt 0 ]; do
    case "$1" in
        --link)
            MODE="link"
            shift
            ;;
        --copy)
            MODE="copy"
            shift
            ;;
        --wire-settings)
            WIRE_SETTINGS=1
            shift
            ;;
        --settings-path)
            [ $# -ge 2 ] || { echo "--settings-path requires a path argument" >&2; exit 1; }
            SETTINGS_PATH_ARG="$2"
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "Unknown option: $1" >&2
            usage >&2
            exit 1
            ;;
    esac
done

_resolve_jq() {
    if [ -x /usr/bin/jq ]; then
        echo /usr/bin/jq
    elif command -v jq >/dev/null 2>&1; then
        command -v jq
    fi
}

_wire_agy_settings() {
    local target="${SETTINGS_PATH_ARG:-${FAIZ_SETTINGS_PATH:-$HOME/.gemini/antigravity-cli/settings.json}}"
    local jq_bin bak_file tmp_file
    jq_bin="$(_resolve_jq || true)"
    [ -n "$jq_bin" ] || { echo "ERROR: jq is required for --wire-settings" >&2; exit 1; }

    mkdir -p "$(dirname "$target")"
    if [ ! -e "$target" ]; then
        tmp_file="$(mktemp "$(dirname "$target")/.settings.XXXXXX")"
        printf '{\n  "statusLine": {\n    "type": "command",\n    "command": "~/.agents/scripts/statusline.sh"\n  }\n}\n' > "$tmp_file"
        mv -f "$tmp_file" "$target"
        echo "Wired statusLine (new file): $target"
        return 0
    fi

    if ! "$jq_bin" -e 'type == "object"' "$target" >/dev/null 2>&1; then
        echo "ERROR: Invalid JSON object in $target; refusing to modify." >&2
        return 1
    fi

    if "$jq_bin" -e '(.statusLine.type == "command") and (.statusLine.command == "~/.agents/scripts/statusline.sh")' "$target" >/dev/null 2>&1; then
        echo "Already wired: $target"
        return 0
    fi

    bak_file="${target}.bak.$(date +%Y%m%d%H%M%S)"
    cp "$target" "$bak_file"
    tmp_file="$(mktemp "$(dirname "$target")/.settings.XXXXXX")"
    if ! "$jq_bin" '.statusLine = ((.statusLine // {}) + {"type": "command", "command": "~/.agents/scripts/statusline.sh"})' "$target" > "$tmp_file"; then
        rm -f "$tmp_file"
        echo "ERROR: Failed to merge statusLine into $target" >&2
        return 1
    fi
    mv -f "$tmp_file" "$target"
    echo "Wired statusLine (backup: $bak_file): $target"
}

_install_file() {
    local src="$1" dst="$2" exec_flag="${3:-1}"
    mkdir -p "$(dirname "$dst")"
    rm -rf "$dst"
    if [ "$MODE" = "link" ]; then
        ln -s "$src" "$dst"
    else
        cp "$src" "$dst"
    fi
    if [ "$exec_flag" = "1" ]; then
        chmod +x "$src"
        [ "$MODE" = "copy" ] && chmod +x "$dst" || true
    fi
    echo "Installed ($MODE): $dst -> $src"
}

_install_file "$REPO_DIR/scripts/statusline.sh" "$HOME/.agents/scripts/statusline.sh" 1
_install_file "$REPO_DIR/scripts/grok-statusline.sh" "$HOME/.agents/scripts/grok-statusline.sh" 1
_install_file "$REPO_DIR/bin/faiz-dash" "$HOME/.local/bin/faiz-dash" 1
_install_file "$REPO_DIR/bin/_faiz_dash_hook.sh" "$HOME/.local/bin/_faiz_dash_hook.sh" 1
_install_file "$REPO_DIR/skills/faiz-dash/SKILL.md" "$HOME/.agents/skills/faiz-dash/SKILL.md" 0

if [ "$WIRE_SETTINGS" -eq 1 ]; then
    _wire_agy_settings
fi

cat <<'EOF'

Next steps:
1. Ensure Python >=3.12 with `textual` and `rich` is installed (`pip install -e .`).
2. Ensure `~/.local/bin` is on your PATH (`export PATH="$HOME/.local/bin:$PATH"`).
3. Configure `~/.gemini/antigravity-cli/settings.json` statusLine (or run `./install.sh --wire-settings`):
   {
     "statusLine": {
       "type": "command",
       "command": "~/.agents/scripts/statusline.sh"
     }
   }
4. Quick test:
   faiz-dash doctor
   faiz-dash config
   faiz-dash inspect
EOF
