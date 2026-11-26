#!/usr/bin/env bash
# One-command verify, commit, push, and optional tag-release helper for Faiz Control Center.
set -euo pipefail

REPO_DIR="$(cd -P "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR"

usage() {
    cat <<'EOF'
Usage: ./scripts/release.sh [-m "commit message"] [-t vX.Y.Z] [--dry-run]

Options:
  -m, --message MSG   Commit message when working tree has changes
  -t, --tag TAG       Create annotated/lightweight tag (e.g. v0.1.1) and push to trigger GitHub Release
  --dry-run           Run privacy scan, syntax checks, and full unit tests without committing or pushing
  -h, --help          Show this help
EOF
}

MSG=""
TAG=""
DRY_RUN=0

while [ $# -gt 0 ]; do
    case "$1" in
        -m|--message)
            [ $# -ge 2 ] || { echo "Missing argument for $1" >&2; exit 1; }
            MSG="$2"
            shift 2
            ;;
        -t|--tag)
            [ $# -ge 2 ] || { echo "Missing argument for $1" >&2; exit 1; }
            TAG="$2"
            shift 2
            ;;
        --dry-run)
            DRY_RUN=1
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            if [ -z "$MSG" ]; then
                MSG="$1"
                shift
            else
                echo "Unknown argument: $1" >&2
                usage >&2
                exit 1
            fi
            ;;
    esac
done

_pick_python() {
    if [ -n "${FAIZ_DASH_PYTHON:-}" ] && [ -x "${FAIZ_DASH_PYTHON}" ]; then
        echo "${FAIZ_DASH_PYTHON}"
        return 0
    fi
    local cand
    for cand in "$REPO_DIR/.venv/bin/python" "$HOME/.venvs/faiz/bin/python" "$HOME/.venvs/faiz-dash/bin/python"; do
        if [ -x "$cand" ]; then
            echo "$cand"
            return 0
        fi
    done
    if command -v python3 >/dev/null 2>&1 && python3 -c "import textual" >/dev/null 2>&1; then
        command -v python3
        return 0
    fi
    for cand in "$HOME"/.venvs/*/bin/python; do
        if [ -x "$cand" ] && "$cand" -c "import textual" >/dev/null 2>&1; then
            echo "$cand"
            return 0
        fi
    done
    command -v python3
}

PY_BIN="$(_pick_python)"
echo "[release] Using Python: $PY_BIN"

echo "[release] 1/4 Privacy & personal-path scan..."
PRIVACY_RE="($HOME|/Users/[A-Za-z0-9._-]+/|/home/[A-Za-z0-9._-]+/|-----BEGIN (RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----|ghp_[A-Za-z0-9]{20,}|AIza[0-9A-Za-z_-]{35})"
if grep -rnI --exclude-dir=__pycache__ --exclude-dir=.git --exclude=".release-privacy-patterns" -E "$PRIVACY_RE" . ; then
    echo "[release] ERROR: Sensitive or machine-specific pattern detected above!" >&2
    exit 1
fi
if [ -f .release-privacy-patterns ]; then
    if grep -rnI --exclude-dir=__pycache__ --exclude-dir=.git --exclude=".release-privacy-patterns" -i -f .release-privacy-patterns . ; then
        echo "[release] ERROR: Local privacy pattern matched above!" >&2
        exit 1
    fi
fi

echo "[release] 2/4 Shell syntax checks..."
bash -n install.sh docs/site/install.sh scripts/statusline.sh scripts/grok-statusline.sh scripts/release.sh bin/faiz-dash bin/_faiz_dash_hook.sh

echo "[release] 3/4 Running full unittest suite..."
PYTHONPATH=src "$PY_BIN" -m unittest discover -s tests -v

if [ "$DRY_RUN" = "1" ]; then
    echo "[release] DRY_RUN complete. All checks passed."
    exit 0
fi

echo "[release] 4/4 Staging, committing, and pushing..."
if [ -n "$(git status --porcelain)" ]; then
    [ -n "$MSG" ] || MSG="chore: update Faiz Control Center"
    git add -A
    git commit -m "$MSG"
fi

git push origin main

if [ -n "$TAG" ]; then
    git tag "$TAG"
    git push origin "$TAG"
    echo "[release] Pushed tag $TAG -> GitHub Release workflow triggered."
fi

echo "[release] Done! GitHub & Cloudflare Pages will sync within ~10s."
