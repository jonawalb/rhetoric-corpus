#!/bin/zsh
# Keep the laptop a thin buffer: everything collected goes to the private Hugging Face store, not this disk.
#   * docs/: merged into the store by document id (store_sync.py push --only docs). docs/ stays local because
#     collectors read it to skip ids they already have (it is small: ~1-4 GB).
#   * raw/ HTML caches (not part of the store's INCLUDE, except the RAW_DIRS listing caches): files older than an
#     hour are packed per source into raw-archive/<source>/<stamp>.tar.gz in the store, verified, then deleted here.
# Usage: scripts/laptop_offload.sh [--once]      (default: loop every hour)
cd "${0:A:h}/.."
REPO=${RHETORIC_STORE_REPO:-wallabee1/rhetoric-corpus-store}
KEEP_RAW=(by_president ir_presstv kp_rodong_en ir_khamenei_en)   # = store_sync.RAW_DIRS (synced by the nightly job)
mkdir -p logs staging/raw-archive

offload_once() {
  echo "== $(date) docs push"
  uv run --no-sync python scripts/store_sync.py push --only docs 2>&1 | tail -2
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
  df -h ~ | tail -1
}

if [[ $1 == --once ]]; then offload_once; exit; fi
while true; do offload_once; sleep 3600; done
