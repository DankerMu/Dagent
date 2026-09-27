#!/bin/sh
# Xagent installer — https://get.xagent.co
#
#   curl -fsSL https://get.xagent.co | sh
#
# Installs the `xagent-ai` package (backend + bundled web UI) as an isolated uv
# tool, so nothing touches your system Python and PEP 668 never bites. On
# success the `xagent` command is available; start it and open the browser.
#
# Options (environment variables):
#   XAGENT_VERSION   pin a specific version, e.g. XAGENT_VERSION=0.6.0
#   XAGENT_PACKAGE_SOURCE
#                    install from an explicit local wheel or source checkout
#                    (mutually exclusive with XAGENT_VERSION)
#   XAGENT_PYTHON    tool interpreter version; defaults to validated Python 3.12
#   XAGENT_SKIP_BROWSER_INSTALL=1
#                    skip the Playwright Chromium browser download
#   XAGENT_SKIP_DEEPDOC_INSTALL=1
#                    skip the build-time DeepDoc/ONNX/NLTK/tiktoken asset bake
#
# This script is a connected installation path, NOT a LAN deployment script.
# Prepare archives/images on a connected host and transfer them out of band
# before starting Xagent in an isolated network.
# For an unpublished fork, set XAGENT_PACKAGE_SOURCE to its built wheel;
# the default package source is the published xagent-ai distribution.
#
# Manual equivalent: install xagent-ai[browser], run
#   python -m deepdoc.download_models
#   python -m xagent.providers.pdf_parser.prepare_deepdoc_assets
#   python -m playwright install chromium
set -eu

# The user's PATH before this script mutates it (below, when bootstrapping uv).
# Used at the end to warn correctly about whether the parent shell will find the
# installed command.
ORIG_PATH="$PATH"

APP="xagent-ai"
CMD="xagent"

info() { printf '\033[1;34m==>\033[0m %s\n' "$1"; }
warn() { printf '\033[1;33mwarning:\033[0m %s\n' "$1" >&2; }
err() {
  printf '\033[1;31merror:\033[0m %s\n' "$1" >&2
  exit 1
}
is_truthy() {
  case "${1:-}" in
    1 | [Tt][Rr][Uu][Ee] | [Yy][Ee][Ss] | [Oo][Nn]) return 0 ;;
    *) return 1 ;;
  esac
}

# uv supports Linux and macOS. Windows users should use pip in a venv.
os="$(uname -s)"
case "$os" in
  Linux | Darwin) ;;
  *) err "Unsupported OS '$os'. On Windows, install with: pip install $APP (in a virtualenv)." ;;
esac

# Ensure uv is available (isolates the install; avoids system-Python/PEP 668).
if ! command -v uv >/dev/null 2>&1; then
  info "Installing uv (Python tool manager)..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  # uv installs into ~/.local/bin (or ~/.cargo/bin on older installers); make it
  # visible to the rest of this script without requiring a new shell.
  for d in "$HOME/.local/bin" "$HOME/.cargo/bin"; do
    [ -d "$d" ] && PATH="$d:$PATH"
  done
  export PATH
fi
command -v uv >/dev/null 2>&1 || err "uv not found on PATH after install; open a new shell and re-run."

spec="${APP}[browser]"
if [ -n "${XAGENT_VERSION:-}" ]; then
  # Strip a leading 'v' (e.g. v0.6.0 -> 0.6.0) so a git-tag-style value works.
  version="${XAGENT_VERSION#v}"
  [ -n "$version" ] || err "XAGENT_VERSION='$XAGENT_VERSION' is not a valid version."
  spec="${spec}==$version"
fi

if [ -n "${XAGENT_PACKAGE_SOURCE:-}" ]; then
  [ -z "${XAGENT_VERSION:-}" ] || err "Choose XAGENT_PACKAGE_SOURCE or XAGENT_VERSION, not both."
  spec="${XAGENT_PACKAGE_SOURCE}[browser]"
fi
info "Installing $spec ..."
uv tool install --upgrade --python "${XAGENT_PYTHON:-3.12}" "$spec"

tool_python="$(uv tool dir)/$APP/bin/python"
[ -x "$tool_python" ] || err "Xagent tool Python not found at '$tool_python'."

if is_truthy "${XAGENT_SKIP_DEEPDOC_INSTALL:-}"; then
  warn "Skipping DeepDoc/ONNX/NLTK assets; PDF parsing will fail until they are preloaded."
  info "Preparing required runtime tokenizers..."
  "$tool_python" -m xagent.providers.pdf_parser.prepare_deepdoc_assets --tokenizers-only ||
    err "Tokenizer assets could not be prepared. Complete this connected install before deploying offline."
else
  export DEEPDOC_MODEL_HOME="${DEEPDOC_MODEL_HOME:-$HOME/.cache/deepdoc}"
  export DEEPDOC_NLTK_DATA_DIR="${DEEPDOC_NLTK_DATA_DIR:-$DEEPDOC_MODEL_HOME/nltk_data}"
  export DEEPDOC_TIKTOKEN_CACHE_DIR="${DEEPDOC_TIKTOKEN_CACHE_DIR:-$DEEPDOC_MODEL_HOME/tiktoken_cache}"
  export TIKTOKEN_CACHE_DIR="$DEEPDOC_TIKTOKEN_CACHE_DIR"
  info "Preparing DeepDoc models, NLTK data and tiktoken cache..."
  "$tool_python" -m deepdoc.download_models ||
    err "DeepDoc assets could not be downloaded. Complete this connected install before deploying offline."
  "$tool_python" -m xagent.providers.pdf_parser.prepare_deepdoc_assets ||
    err "DeepDoc assets could not be materialized for local-only parsing."
fi

if is_truthy "${XAGENT_SKIP_BROWSER_INSTALL:-}"; then
  warn "Skipping Playwright Chromium; browser tasks need a preloaded browser."
else
  info "Installing Playwright Chromium browser..."
  "$tool_python" -m playwright install chromium ||
    err "Playwright Chromium could not be downloaded. Complete this connected install before deploying offline."
fi

printf '\n'
info "Installed. Next steps:"
printf '\n'
printf '  Start Xagent:   %s\n' "$CMD"
printf '  Open:           http://127.0.0.1:8000\n'
printf '  Configure OPENAI_BASE_URL and OPENAI_MODEL for your LAN endpoint; set OPENAI_API_KEY only if required.\n'
printf '\n'

if ! PATH="$ORIG_PATH" command -v "$CMD" >/dev/null 2>&1; then
  warn "'$CMD' is not on your PATH in this shell yet."
  if PATH="$ORIG_PATH" command -v uv >/dev/null 2>&1; then
    warn "Run 'uv tool update-shell' and open a new terminal, then run '$CMD'."
  else
    # uv was just installed by this script and isn't on the parent shell's PATH.
    warn "Open a new terminal, or run: export PATH=\"\$HOME/.local/bin:\$PATH\""
  fi
fi
