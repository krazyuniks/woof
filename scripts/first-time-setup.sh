#!/usr/bin/env zsh
# Woof first-time setup.
#
# Bootstraps a development checkout by installing/checking host prerequisites,
# synchronising the uv environment, installing git hooks, and running the local
# quality gate.

set -euo pipefail

typeset -r RED=$'\033[0;31m'
typeset -r GREEN=$'\033[0;32m'
typeset -r YELLOW=$'\033[1;33m'
typeset -r BLUE=$'\033[0;34m'
typeset -r CYAN=$'\033[0;36m'
typeset -r BOLD=$'\033[1m'
typeset -r NC=$'\033[0m'

ASSUME_YES=false
CHECK_ONLY=false
SKIP_HOOKS=false
SKIP_CHECK=false

usage() {
    cat <<'USAGE'
Usage: ./scripts/first-time-setup.sh [options]

Options:
  -y, --yes      Install supported prerequisites without prompting.
  --check-only   Check prerequisites only; do not install or run setup.
  --no-hooks     Skip git hook installation.
  --no-check     Skip the final quality gate.
  -h, --help     Show this help.
USAGE
}

while (( $# > 0 )); do
    case "$1" in
        -y|--yes) ASSUME_YES=true ;;
        --check-only) CHECK_ONLY=true ;;
        --no-hooks) SKIP_HOOKS=true ;;
        --no-check) SKIP_CHECK=true ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            print -u2 "Unknown option: $1"
            usage
            exit 2
            ;;
    esac
    shift
done

log_info() { print -r -- "${GREEN}[OK]${NC} $1"; }
log_warn() { print -r -- "${YELLOW}[WARN]${NC} $1"; }
log_error() { print -r -- "${RED}[ERROR]${NC} $1"; }
log_step() {
    print -r -- ""
    print -r -- "${BLUE}==>${NC} ${BOLD}$1${NC}"
}

confirm() {
    local prompt="$1"
    if [[ "$ASSUME_YES" == true ]]; then
        return 0
    fi
    local reply
    printf "%s [Y/n] " "$prompt"
    read -r reply
    [[ ! "$reply" =~ '^[Nn]' ]]
}

detect_os() {
    if [[ "$OSTYPE" == darwin* ]]; then
        print -r -- "macos"
    elif [[ "$OSTYPE" == linux* ]]; then
        print -r -- "linux"
    else
        print -r -- "unknown"
    fi
}

run_install() {
    local description="$1"
    shift
    if [[ "$CHECK_ONLY" == true ]]; then
        log_warn "$description missing"
        return 1
    fi
    if confirm "Install $description?"; then
        "$@"
    else
        log_error "$description is required"
        return 1
    fi
}

install_just() {
    if command -v cargo >/dev/null 2>&1; then
        run_install "just via cargo" cargo install just
    elif command -v brew >/dev/null 2>&1; then
        run_install "just via Homebrew" brew install just
    elif command -v apt >/dev/null 2>&1; then
        run_install "just via apt" zsh -c 'sudo apt update && sudo apt install -y just'
    elif command -v pacman >/dev/null 2>&1; then
        run_install "just via pacman" sudo pacman -S --needed just
    else
        log_error "just not found and no supported installer is available"
        print -r -- "Install just manually: https://github.com/casey/just#installation"
        return 1
    fi
}

install_uv() {
    if ! command -v curl >/dev/null 2>&1; then
        log_error "curl is required to install uv"
        return 1
    fi
    run_install "uv via the official installer" zsh -c 'curl -LsSf https://astral.sh/uv/install.sh | sh'
    export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
}

install_node() {
    if command -v brew >/dev/null 2>&1; then
        run_install "Node.js via Homebrew" brew install node
    elif command -v apt >/dev/null 2>&1; then
        run_install "Node.js and npm via apt" zsh -c 'sudo apt update && sudo apt install -y nodejs npm'
    elif command -v pacman >/dev/null 2>&1; then
        run_install "Node.js and npm via pacman" sudo pacman -S --needed nodejs npm
    else
        log_error "npm not found and no supported installer is available"
        print -r -- "Install Node.js manually: https://nodejs.org/"
        return 1
    fi
}

ensure_tool() {
    local name="$1"
    local installer="${2:-}"
    if command -v "$name" >/dev/null 2>&1; then
        log_info "$name: $($name --version 2>/dev/null | head -n 1)"
        return 0
    fi
    if [[ -n "$installer" ]]; then
        "$installer"
        command -v "$name" >/dev/null 2>&1 && return 0
    fi
    log_error "$name not found"
    return 1
}

ensure_ajv() {
    if command -v ajv >/dev/null 2>&1; then
        log_info "ajv: $(ajv --version 2>/dev/null || print -r -- present)"
    else
        ensure_tool npm install_node
        run_install "ajv-cli and ajv-formats globally via npm" npm install -g ajv-cli ajv-formats
    fi

    if ! ajv compile --spec=draft2020 -c ajv-formats -s schemas/prerequisites.schema.json >/dev/null 2>&1; then
        if [[ "$CHECK_ONLY" == true ]]; then
            log_warn "ajv-formats is not available to ajv-cli"
            return 1
        fi
        run_install "ajv-formats globally via npm" npm install -g ajv-formats
    fi
}

ensure_python() {
    if uv python find 3.11 >/dev/null 2>&1; then
        log_info "Python 3.11+: $(uv python find 3.11)"
    elif [[ "$CHECK_ONLY" == true ]]; then
        log_warn "Python 3.11 not found via uv"
        return 1
    else
        log_warn "Python 3.11 not found via uv"
        uv python install 3.11
        log_info "Python 3.11 installed"
    fi
}

require_manual_tool() {
    local name="$1"
    local hint="$2"
    if command -v "$name" >/dev/null 2>&1; then
        local version
        version="$($name --version 2>/dev/null | head -n 1 || true)"
        log_info "$name: ${version:-present}"
        return 0
    fi
    log_error "$name not found"
    print -r -- "       $hint"
    return 1
}

REPO_DIR="${0:A:h:h}"
OS="$(detect_os)"
missing=0

print -r -- ""
print -r -- "${CYAN}=========================================${NC}"
print -r -- "${CYAN}  Woof - First-Time Setup${NC}"
print -r -- "${CYAN}=========================================${NC}"
print -r -- ""

cd "$REPO_DIR"

if [[ ! -f pyproject.toml || ! -d .woof ]]; then
    log_error "Run this script from a Woof repository checkout"
    exit 2
fi

log_step "Checking core prerequisites..."
ensure_tool just install_just || missing=1
ensure_tool uv install_uv || missing=1
ensure_tool git || missing=1
ensure_python || missing=1
ensure_ajv || missing=1

log_step "Checking Woof workflow prerequisites..."
require_manual_tool gh "Install GitHub CLI and authenticate with: gh auth login" || missing=1
require_manual_tool cld "Install the Claude wrapper expected by .woof/agents.toml." || missing=1
require_manual_tool cod "Install the Codex wrapper expected by .woof/agents.toml." || missing=1
require_manual_tool agent-sync "Install agent-sync so project agent instructions can be rendered." || missing=1

if (( missing != 0 )); then
    log_error "One or more prerequisites are missing"
    exit 1
fi

if [[ "$CHECK_ONLY" == true ]]; then
    log_info "Prerequisite check complete"
    exit 0
fi

log_step "Synchronising Python environment..."
just setup

if [[ "$SKIP_HOOKS" == false ]]; then
    log_step "Installing git hooks..."
    just install-hooks
fi

if [[ "$SKIP_CHECK" == false ]]; then
    log_step "Running quality gate..."
    just check
fi

print -r -- ""
print -r -- "${GREEN}=========================================${NC}"
print -r -- "${GREEN}  Setup Complete${NC}"
print -r -- "${GREEN}=========================================${NC}"
print -r -- ""
print -r -- "Quick commands:"
print -r -- "  just --list          Show available tasks"
print -r -- "  just check           Run lint and tests"
print -r -- "  just woof --help     Run the checkout CLI"
print -r -- ""
