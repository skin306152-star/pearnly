#!/bin/sh
# Run from the long-lived integration checkout, not a disposable worktree.
set -eu
root=$(git rev-parse --show-toplevel)
common=$(git rev-parse --path-format=absolute --git-common-dir)
if [ "$common" != "$root/.git" ]; then
    echo 'Install hooks from the main repository checkout so other worktrees share a durable path.' >&2
    exit 1
fi
wanted="$root/.githooks"
current=$(git config --local --get core.hooksPath || true)
if [ -n "$current" ] && [ "$current" != "$wanted" ]; then
    echo 'An existing hooksPath is configured; preserve and integrate those hooks before installing.' >&2
    exit 1
fi
if [ -z "$current" ] && [ -f "$common/hooks/pre-commit" ]; then
    echo 'An existing pre-commit hook is installed; preserve and integrate it before installing.' >&2
    exit 1
fi
test -x "$wanted/pre-commit"
git config --local core.hooksPath "$wanted"
printf 'Repository hook installed: %s\n' "$wanted/pre-commit"
