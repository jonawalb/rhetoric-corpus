#!/bin/zsh
# Stop all collectors (and the watchdog loop) if free disk space drops below a floor. Collectors are resumable.
FLOOR_GB=${1:-6}
cd "${0:A:h}/.."
while true; do
  free_gb=$(df -g ~ | awk 'NR==2{print $4}')
  if (( free_gb < FLOOR_GB )); then
    echo "$(date) free=${free_gb}G < ${FLOOR_GB}G: stopping collectors"
    pkill -f "resume_all.sh" ; pkill -f "collectors/" ; pkill -f "pk_ispr.py; sleep"
    exit 0
  fi
  sleep 600
done
