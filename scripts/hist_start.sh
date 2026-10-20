#!/bin/bash
# Starts (or resumes) the Jan 1 - Aug 10 download into data_hist/ in the background; keeps the Mac awake while it runs.
# Safe to run again: running parts are skipped, finished parts are skipped. Progress: bash scripts/hist_status.sh
ROOT="$(cd "$(dirname "$0")/.." && pwd)"; H="$ROOT/data_hist"; mkdir -p "$H" "$ROOT/logs"; cd "$H" || exit 1
A=2026-01-01; B=2026-08-11
for p in markets ktr coinbase kraken binance; do
  log="$ROOT/logs/hist_$p.log"
  if pgrep -f "hist_dl.py $p " >/dev/null; then echo "running   $p"; continue; fi
  if grep -q "FINISHED" "$log" 2>/dev/null; then echo "finished  $p"; continue; fi
  nohup perl -MPOSIX -e "POSIX::setsid(); exec @ARGV" python3 -P -u "$ROOT/scripts/hist_dl.py" $p $A $B >> "$log" 2>&1 &
  echo "started   $p  (log: logs/hist_$p.log)"
done
if ! pgrep -f "sleepguard-hist" >/dev/null; then
  nohup perl -MPOSIX -e "POSIX::setsid(); exec @ARGV" caffeinate -i bash -c 'exec -a sleepguard-hist bash -c "while pgrep -f \"hist_dl.py\" >/dev/null; do sleep 60; done"' >/dev/null 2>&1 &
  echo "started   sleep guard"
fi
