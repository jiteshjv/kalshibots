#!/bin/bash
# Idempotent: (re)starts any unfinished downloader that isn't running, plus a sleep guard.
# Safe to run any time, any number of times. Usage: bash scripts/resume.sh
ROOT="$(cd "$(dirname "$0")/.." && pwd)"; cd "$ROOT/data" || exit 1
mkdir -p "$ROOT/logs"
start() {  # $1=script name  $2=log name
  local s=$1 log="$ROOT/logs/$2.log"
  if pgrep -f "scripts/$s" >/dev/null; then echo "running   $s"; return; fi
  local fin; fin=$(grep -a "FINISHED" "$log" 2>/dev/null | tail -1)
  if [ -n "$fin" ] && { [ "$s" != "ktrades_all.py" ] || awk '{exit !($2==$4)}' <<<"$fin"; }; then echo "finished  $s"; return; fi
  echo "$(date -u +%FT%TZ) ==== (re)start $s ====" >> "$log"
  nohup bash -c "python3 -I -u '$ROOT/scripts/$s' 2>&1 | while IFS= read -r l; do printf '%s %s\n' \"\$(date -u +%FT%TZ)\" \"\$l\"; done >> '$log'" >/dev/null 2>&1 &
  echo "started   $s  (log: logs/$2.log)"
}
start ktrades_all.py kalshi_trades
start cb.py          coinbase
start kr.py          kraken
if ! pgrep -f "sleepguard-kalshi" >/dev/null; then
  nohup caffeinate -i bash -c 'exec -a sleepguard-kalshi bash -c "while pgrep -f \"scripts/(ktrades_all|cb|kr).py\" >/dev/null; do sleep 60; done"' >/dev/null 2>&1 &
  echo "started   sleep guard (Mac stays awake while downloads run)"
fi
