#!/bin/zsh
# LAPTOP FALLBACK ONLY. The nightly refresh now runs on GitHub Actions (.github/workflows/nightly.yml) against the
# private Hugging Face store. Do not run this while the CI job owns the store: it publishes from this laptop's copy.
# Steps (same as CI after collection):
#   1. build_index.py                         bring index/corpus.sqlite up to date with docs/ (incremental)
#   2. python -m scripts.semantic.run         semantic layer, incremental
#   3. publish/build_trends.py                Trends data -> staging/trends
#   4. publish/build_hf_data.py --upload      search + full documents + Trends, sealed, to the Hugging Face data repo
#                                             (needs `hf auth login` or HF_TOKEN; tier password from the keychain)
# The site is not redeployed: the live page reads search and Trends data from the Hugging Face build.
# Guards: a lock directory (.nightly_publish.lock) so two runs never overlap, and a free-disk floor (MIN_FREE_GB,
# default 8). Log: logs/nightly_publish.log. A run killed with SIGKILL leaves the lock behind: check no run is active
# (pgrep -f nightly_publish), then rmdir ~/Projects/rhetoric-corpus/.nightly_publish.lock
set -euo pipefail
CORPUS=${0:A:h:h}
MIN_FREE_GB=${MIN_FREE_GB:-8}
LOCK=$CORPUS/.nightly_publish.lock

if ! mkdir "$LOCK" 2>/dev/null; then
  echo "$(date) another nightly_publish run holds $LOCK; exiting" >&2
  exit 1
fi
trap 'rmdir "$LOCK" 2>/dev/null || true' EXIT INT TERM

disk_ok() {
  local free_gb
  free_gb=$(df -g "$CORPUS" | awk 'NR==2{print $4}')
  if (( free_gb < MIN_FREE_GB )); then
    echo "$(date) only ${free_gb} GB free (< ${MIN_FREE_GB} GB); aborting" >&2
    return 1
  fi
}

mkdir -p "$CORPUS/logs"
exec >>"$CORPUS/logs/nightly_publish.log" 2>&1
echo "==== $(date) nightly_publish start"
disk_ok

cd "$CORPUS"
uv run python scripts/build_index.py
uv run python -m scripts.semantic.run
uv run python publish/build_trends.py
disk_ok
uv run python publish/build_hf_data.py --trends staging/trends --upload
echo "==== $(date) nightly_publish done"
