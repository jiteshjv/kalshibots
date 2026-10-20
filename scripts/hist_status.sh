#!/bin/bash
# Progress of the Jan 1 - Aug 10 download (data_hist/).
ROOT="$(cd "$(dirname "$0")/.." && pwd)"; H="$ROOT/data_hist"
for p in markets ktr coinbase kraken binance; do
  st=$(pgrep -f "hist_dl.py $p " >/dev/null && echo RUNNING || (grep -q FINISHED "$ROOT/logs/hist_$p.log" 2>/dev/null && echo done || echo stopped))
  printf "%-9s %-8s %s\n" "$p" "$st" "$(tail -1 "$ROOT/logs/hist_$p.log" 2>/dev/null | cut -c1-110)"
done
echo "markets listed: $(python3 -c "import json;print(len(json.load(open('$H/settled.json'))))" 2>/dev/null || echo '-')  | Kalshi trade files: $(ls "$H/ktr" 2>/dev/null | grep -vc tmp)  | Coinbase chunks: $(ls "$H/cb" 2>/dev/null | grep -c '^[0-9]')/200  | Kraken days: $(ls "$H/kr" 2>/dev/null | grep -c json$)  | size $(du -sh "$H" 2>/dev/null | cut -f1)"
