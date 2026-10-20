#!/bin/bash
ROOT="$(cd "$(dirname "$0")/.." && pwd)"; OUT="${PAPER_OUT:-$ROOT/logs/paper}"
pgrep -f "paper_trader.py" >/dev/null && echo "RUNNING" || echo "NOT RUNNING  (start: bash scripts/paper_start.sh)"
echo "-- last log lines"; tail -6 "$OUT/paper.log" 2>/dev/null
rows() { [ -f "$1" ] && echo $(( $(wc -l < "$1") - 1 )) || echo 0; }
echo "-- windows settled: $(rows "$OUT/windows.csv")   paper trades: $(rows "$OUT/trades.csv")"
