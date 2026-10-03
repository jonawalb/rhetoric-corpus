#!/bin/zsh
# Nightly refresh of the public Rhetoric Search (Interactive Deterrence, jwalberg.com). NOT scheduled; run by hand
# or from launchd/cron once Jonathan decides to:
#   1. build_index.py                     bring index/corpus.sqlite up to date with docs/ (incremental)
#   2. python -m scripts.semantic.run     semantic layer, incremental (tone, targets, topics, trends, echo, alerts)
#   3. build_trends.py                    Trends page data (tsm-strait-layers tools/rhetoric-search/data/trends/, stays in the site)
#   4. build_hf_data.py --upload          search + full-document data, sealed, to the Hugging Face dataset repo
#                                         (HF_REPO in tools/rhetoric-search/js/config.js; needs `hf auth login` or HF_TOKEN)
#   5. build_site.py --site deterrence    rebuild and deploy the site (gate, trends, page code)
# Guards: a lock directory (.nightly_publish.lock) so two runs never overlap, and a free-disk floor (MIN_FREE_GB,
# default 8) checked before starting and again before the data build. Log: logs/nightly_publish.log.
# A run killed with SIGKILL leaves the lock behind: check no run is active (pgrep -f nightly_publish), then
# rmdir ~/Projects/rhetoric-corpus/.nightly_publish.lock
set -euo pipefail
CORPUS=${0:A:h:h}
SITE=${SITE_REPO:-$HOME/Projects/tsm-strait-layers}
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

cd "$SITE"
uv run --project "$CORPUS" python tools/rhetoric-search/scripts/build_trends.py
disk_ok
uv run --project "$CORPUS" --with cryptography python tools/rhetoric-search/scripts/build_hf_data.py --upload
uv run --with cryptography scripts/build_site.py --site deterrence
echo "==== $(date) nightly_publish done"
