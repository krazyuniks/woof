#!/usr/bin/env zsh
# Install the tmux_harness package into the checkout venv.
#
# tmux_harness is the interactive-TUI dispatch transport the dispatcher imports
# for every real produce/review dispatch. It is not published to a package
# index yet, so `uv sync` cannot provide it and an exact sync prunes it from
# the venv; `just setup` re-runs this script after every sync.

set -euo pipefail

REPO_DIR="${0:A:h:h}"

candidates=(
    "$REPO_DIR/../agent-toolkit/skills/tmux-harness"
    "$HOME/Work/agent-toolkit/skills/tmux-harness"
)

source_dir=""
for candidate in "${candidates[@]}"; do
    if [[ -f "$candidate/pyproject.toml" ]]; then
        source_dir="${candidate:A}"
        break
    fi
done

if [[ -z "$source_dir" ]]; then
    print -u2 -r -- "[ERROR] tmux-harness source not found (the dispatch transport package)."
    print -u2 -r -- "        Checked: ${(j:, :)candidates}"
    print -u2 -r -- "        Fix: git clone git@github.com:krazyuniks/agent-toolkit.git ~/Work/agent-toolkit"
    exit 1
fi

cd "$REPO_DIR"
uv pip install --quiet -e "$source_dir"
if ! uv run --no-sync python -c "import tmux_harness" 2>/dev/null; then
    print -u2 -r -- "[ERROR] tmux_harness failed to import after install from $source_dir"
    exit 1
fi
print -r -- "[OK] tmux_harness installed (editable) from $source_dir"
