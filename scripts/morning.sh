#!/bin/zsh
# Manual control panel for the rhetoric corpus, for use in a plain Terminal (no Claude needed).
#   zsh ~/Projects/rhetoric-corpus/scripts/morning.sh            health report (default)
#   zsh ~/Projects/rhetoric-corpus/scripts/morning.sh start      start watchdog (it restarts collectors, offload, disk guard)
#   zsh ~/Projects/rhetoric-corpus/scripts/morning.sh upload     upload the laptop buffer to Hugging Face now
#   zsh ~/Projects/rhetoric-corpus/scripts/morning.sh publish    start a cloud run that refreshes the website
#   zsh ~/Projects/rhetoric-corpus/scripts/morning.sh stop       stop all collection on this Mac
#   zsh ~/Projects/rhetoric-corpus/scripts/morning.sh disk       free disk space (drop local Time Machine snapshots)
cd "${0:A:h}/.." || exit 1
REPO=jonawalb/rhetoric-corpus

health() {
  echo "== $(date)"
  echo "-- disk";            df -h ~ | tail -1
  echo "-- laptop buffer";   du -sh docs 2>/dev/null
  echo "-- stored on Hugging Face (ids)"
  for c in RU CN IR; do printf "   %s %s\n" $c "$(cat state/ids/$c/*.ids 2>/dev/null | grep -vc '^#')"; done
  echo "-- background jobs (should list watchdog, laptop_offload, disk_guard)"
  pgrep -fl "scripts/(watchdog|laptop_offload|disk_guard)" | sed 's/^/   /' || echo "   NONE RUNNING -> run: morning.sh start"
  echo "-- collector processes: $(pgrep -f 'collectors/' | wc -l | tr -d ' ')"
  echo "-- last watchdog pass";  tail -3 logs/watchdog.log 2>/dev/null | sed 's/^/   /'
  echo "-- last upload";         grep -E "^==" logs/offload.log 2>/dev/null | tail -1 | sed 's/^/   /'
  echo "-- collectors with errors in their last 300 log lines"
  for f in logs/ru_*.log logs/cn_*.log logs/ir_*.log; do
    t=$(tail -300 "$f" | grep -c Traceback); h=$(tail -300 "$f" | grep -cE "\b(403|429)\b")
    if (( t > 2 || h > 30 )); then echo "   $f  tracebacks=$t  403/429=$h"; fi
  done
  echo "-- cloud runs (GitHub)"; gh run list --repo $REPO --limit 3 2>/dev/null | sed 's/^/   /' || echo "   gh not logged in: run 'gh auth login'"
}

case ${1:-health} in
  health)  health ;;
  start)   pgrep -f "scripts/watchdog.sh" >/dev/null && echo "watchdog already running" \
             || { nohup zsh scripts/watchdog.sh >> logs/watchdog.log 2>&1 & echo "watchdog started (collectors restart within a minute)"; } ;;
  upload)  uv run --no-sync python scripts/store_sync.py seal 2>&1 | tail -3; du -sh docs ;;
  publish) if [[ -n $(gh run list --repo $REPO --status in_progress --limit 1 --json databaseId -q '.[].databaseId') || \
                 -n $(gh run list --repo $REPO --status queued --limit 1 --json databaseId -q '.[].databaseId') ]]; then
             echo "a cloud run is already running or queued; not starting another (it would cancel the queued one)"
           else gh workflow run nightly.yml --repo $REPO -f publish=true && echo "cloud run started; site refreshes in ~3-4 h"; fi ;;
  stop)    pkill -f "scripts/watchdog.sh"; pkill -f "scripts/laptop_offload.sh"; pkill -f "collectors/"; echo "collection stopped (disk guard left running)" ;;
  disk)    tmutil thinlocalsnapshots / 40000000000 4 >/dev/null 2>&1; df -h ~ | tail -1 ;;
  *)       sed -n 2,9p "$0" ;;
esac
