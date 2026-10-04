#!/bin/zsh
# Keep the laptop collection alive without supervision (Jonathan, 2026-10-04: "fix these problems when they happen").
# Every 30 minutes:
#   * restart any collector that died (scripts/resume_all.sh skips ones already running; all are resumable)
#   * make sure the offload loop (laptop_offload.sh) and the disk guard (disk_guard.sh) are running
#   * if free space is low, ask macOS to drop local Time Machine snapshots (they pin deleted corpus files)
# Usage: nohup scripts/watchdog.sh >> logs/watchdog.log 2>&1 &
cd "${0:A:h}/.."
LOW_GB=${LOW_GB:-20}
while true; do
  echo "== $(date)"
  free_gb=$(df -g ~ | awk 'NR==2{print $4}')
  if (( free_gb < LOW_GB )); then
    echo "free ${free_gb}G < ${LOW_GB}G: thinning local snapshots"
    tmutil thinlocalsnapshots / 40000000000 4 >/dev/null 2>&1
    echo "free now $(df -g ~ | awk 'NR==2{print $4}')G"
  fi
  # disk_guard stops collectors below 6 GB; only restart them when there is room again
  if (( $(df -g ~ | awk 'NR==2{print $4}') >= 10 )); then
    zsh scripts/resume_all.sh 2>&1 | grep -c "^started" | xargs -I{} echo "collectors restarted: {}"
  fi
  pgrep -f "scripts/laptop_offload.sh" >/dev/null || { nohup zsh scripts/laptop_offload.sh >> logs/offload.log 2>&1 & echo "restarted offload"; }
  pgrep -f "scripts/disk_guard.sh" >/dev/null || { nohup zsh scripts/disk_guard.sh 6 >> logs/disk_guard.log 2>&1 & echo "restarted disk_guard"; }
  echo "collectors running: $(pgrep -f 'collectors/' | wc -l | tr -d ' ')"
  sleep 1800
done
