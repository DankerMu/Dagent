#!/usr/bin/env bash
# Prove each engineering gate via tests/engineering, including runtime.
# Pytest fixtures live under an owned --basetemp so leftover processes that
# name the temp root are visible to the residue sweep.
set -u

repo_root="$(git rev-parse --show-toplevel 2>/dev/null || pwd)" || exit 1
tmp="$(mktemp -d "${TMPDIR:-/tmp}/guardrails.XXXXXX")" || exit 1
tmp="$(cd "$tmp" && pwd -P)" || exit 1
basetemp="$tmp/pytest"
mkdir -p "$basetemp"

fixture_strays() {
  ps -A -ww -o pid= -o args= | FIXTURE_ROOT="$tmp" awk 'index($0, ENVIRON["FIXTURE_ROOT"])'
}

kill_strays() {
  kill -KILL $(printf '%s\n' "$1" | awk '{ print $1 }') 2>/dev/null
}

cleanup() {
  cd "$repo_root" || exit 1
  strays=$(fixture_strays)
  [ -n "$strays" ] && kill_strays "$strays"
  rm -rf "$tmp"
}
trap cleanup EXIT

PYTHON="${PYTHON:-python3}"
cd "$repo_root" || exit 1

run_gate() {
  name="$1"
  shift
  echo "== $name =="
  "$@"
  rc=$?
  if [ "$rc" -eq 0 ]; then
    echo "PASS  $name"
    return 0
  fi
  if [ "$rc" -eq 126 ] || [ "$rc" -eq 127 ]; then
    echo "FAIL  $name — not runnable (exit $rc)"
  else
    echo "FAIL  $name — pytest exit $rc"
  fi
  return 1
}

fail=0
# Discover the whole directory so newly added contract tests cannot be omitted
# from the required CI self-proof. Verbose pytest output retains per-case results.
run_gate "engineering gate contracts and runtime ownership" \
  "$PYTHON" -m pytest -v tests/engineering --basetemp="$basetemp" --timeout=60 \
  || fail=$((fail + 1))

strays=$(fixture_strays)
if [ -n "$strays" ]; then
  echo "FAIL  fixture processes outlived the run:"
  printf '%s\n' "$strays" | sed 's/^/        /'
  kill_strays "$strays"
  fail=$((fail + 1))
else
  echo "PASS  no process naming $tmp outlived the run"
fi

echo
echo "guardrail self-test: $fail failed gate group(s)"
[ "$fail" -eq 0 ]
