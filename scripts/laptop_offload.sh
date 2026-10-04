#!/bin/zsh
# Keep the laptop a thin buffer: everything collected goes to the private Hugging Face store, not this disk.
#   * docs/ (store layout 2): `store_sync.py seal` cuts the complete lines of every docs/<CC>/<source>.jsonl into one
#     gzip part, uploads it, verifies it in the store (sha256 + line count), records the ids in state/ids/ (collectors
#     skip those ids) and only then removes the sealed lines from the local files (scripts/segments.py). docs/ then
#     holds only what was collected since the last seal. Layout 1 (store not yet migrated): merged into the store by
#     document id (store_sync.py push --only docs), docs/ stays whole.
#   * raw/ HTML caches (not part of the store's INCLUDE, except the RAW_DIRS listing caches): files older than an
#     hour are packed per source into raw-archive/<source>/<stamp>.tar.gz in the store, verified, then deleted here.
#   * once a day (and only while no GitHub Actions run is active) the store's history is squashed: every push stores a
#     new version of each changed file (layout 1: whole docs files), and old versions count against the 100 GB quota.
# Usage: scripts/laptop_offload.sh [--once]      (default: loop every 3 hours)
cd "${0:A:h}/.."
REPO=${RHETORIC_STORE_REPO:-wallabee1/rhetoric-corpus-store}
export HF_HUB_DISABLE_PROGRESS_BARS=1
KEEP_RAW=(by_president ir_presstv kp_rodong_en ir_khamenei_en)   # = store_sync.RAW_DIRS (synced by the nightly job)
mkdir -p logs staging/raw-archive

offload_once() {
  if [[ $(uv run --no-sync python scripts/store_sync.py layout 2>/dev/null | tail -1) == 2 ]]; then
    echo "== $(date) docs seal (docs/ before: $(du -sh docs | cut -f1))"
    uv run --no-sync python scripts/store_sync.py seal 2>&1 | tail -2
    echo "docs/ after: $(du -sh docs | cut -f1)"
  else
    echo "== $(date) docs push (store layout 1)"
    uv run --no-sync python scripts/store_sync.py push --only docs 2>&1 | tail -2
  fi
  for d in raw/*(/N); do
    src=${d:t}
    (( ${KEEP_RAW[(Ie)$src]} )) && continue
    list=staging/raw-archive/$src.list
    find "$d" -type f -mmin +60 > "$list"
    [[ -s $list ]] || continue
    stamp=$(date -u +%Y%m%dT%H%M%SZ)
    tgz=staging/raw-archive/${src}_$stamp.tar.gz
    tar -czf "$tgz" -T "$list" || { echo "tar failed: $src"; continue; }
    if hf upload "$REPO" "$tgz" "raw-archive/$src/${tgz:t}" --repo-type dataset \
         --commit-message "raw archive $src $stamp" >/dev/null 2>&1; then
      n=$(wc -l < "$list"); tr '\n' '\0' < "$list" | xargs -0 rm -f
      echo "raw $src: archived + removed $n files"
    else
      echo "upload failed: $src (kept local files)"
    fi
    rm -f "$tgz" "$list"
  done
  if [[ ! -f state/.last_squash || -n $(find state/.last_squash -mmin +1380) ]]; then
    if [[ -z $(gh run list --repo jonawalb/rhetoric-corpus --status in_progress --limit 1 --json databaseId -q '.[].databaseId' 2>/dev/null) ]]; then
      uv run --no-sync python scripts/store_sync.py squash 2>&1 | tail -1 && touch state/.last_squash
    else
      echo "squash deferred: CI run in progress"
    fi
  fi
  df -h ~ | tail -1
}

if [[ $1 == --once ]]; then offload_once; exit; fi
while true; do offload_once; sleep 10800; done
