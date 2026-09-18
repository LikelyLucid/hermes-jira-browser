#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"
WORK_DIR="$(mktemp -d "${TMPDIR:-/tmp}/hermes-jira-browser-quality.XXXXXX")"
trap 'rm -rf "$WORK_DIR"' EXIT
cd "$ROOT_DIR"

export HOME="$WORK_DIR/home"
export HERMES_HOME="$WORK_DIR/hermes-home"
export XDG_CACHE_HOME="$WORK_DIR/cache"
unset HERMES_JIRA_CONFIG_FILE JIRA_BASE_URL JIRA_SITE JIRA_EMAIL JIRA_API_TOKEN || true
while IFS= read -r variable; do
  case "$variable" in
    JIRA_*|HERMES_JIRA_*) unset "$variable" ;;
  esac
done < <(compgen -v)

printf '%s\n' '== Isolated Python environment =='
"$PYTHON_BIN" -m venv "$WORK_DIR/venv"
VENV_PYTHON="$WORK_DIR/venv/bin/python"
"$VENV_PYTHON" -m pip install \
  --disable-pip-version-check \
  --no-input \
  --only-binary=:all: \
  --require-hashes \
  --requirement "$ROOT_DIR/scripts/requirements-ci.txt"

# The plugin imports a few small Hermes APIs, but this repository is intentionally
# tested without checking out the private Hermes source tree. These compatibility
# shims cover only the imported contracts; product behavior remains under test.
mkdir -p "$WORK_DIR/shims/hermes_cli"
cat > "$WORK_DIR/shims/hermes_cli/__init__.py" <<'PY'
"""Test-only Hermes compatibility namespace."""
PY
cat > "$WORK_DIR/shims/hermes_cli/_subprocess_compat.py" <<'PY'
from __future__ import annotations

import os


def noninteractive_git_env() -> dict[str, str]:
    environment = os.environ.copy()
    environment["GIT_TERMINAL_PROMPT"] = "0"
    environment["GIT_ASKPASS"] = os.devnull
    return environment
PY
cat > "$WORK_DIR/shims/hermes_cli/worktree_ops.py" <<'PY'
from __future__ import annotations

from pathlib import Path


def _ensure_worktrees_gitignored(repo: Path) -> None:
    gitignore = repo / ".gitignore"
    existing = gitignore.read_text(encoding="utf-8") if gitignore.exists() else ""
    if any(line.strip() in {".worktrees", ".worktrees/"} for line in existing.splitlines()):
        return
    with gitignore.open("a", encoding="utf-8") as stream:
        if existing and not existing.endswith("\n"):
            stream.write("\n")
        stream.write(".worktrees/\n")
PY
cat > "$WORK_DIR/shims/hermes_constants.py" <<'PY'
from __future__ import annotations

import os
from pathlib import Path


def get_hermes_home() -> Path:
    return Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes")).expanduser()


def profile_name_for_home(home: Path) -> str:
    home = Path(home)
    return home.name if home.parent.name == "profiles" else "default"
PY

export PYTHONPATH="$WORK_DIR/shims${PYTHONPATH:+:$PYTHONPATH}"

printf '%s\n' '== JavaScript syntax =='
node --check desktop/plugin.js

printf '%s\n' '== Python bytecode compilation =='
"$VENV_PYTHON" -m compileall -q dashboard tests

printf '%s\n' '== Python unit tests =='
"$VENV_PYTHON" -m unittest discover -s tests -v

printf '%s\n' '== Repository secret/PII scan =='
"$VENV_PYTHON" scripts/scan-repository.py
