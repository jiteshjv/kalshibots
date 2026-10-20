#!/bin/bash
# One-glance status of all downloads. Usage: bash scripts/status.sh
ROOT="$(cd "$(dirname "$0")/.." && pwd)"; D="$ROOT/data"
run(){ pgrep -f "scripts/$1" >/dev/null && echo RUNNING || echo stopped; }
echo "== $(date -u +%FT%TZ) =="
echo "Kalshi trades : $(ls $D/ktr/*.json 2>/dev/null | wc -l | tr -d ' ') / 6322 windows   [$(run ktrades_all.py)]"
echo "Coinbase      : $(ls $D/cb/[0-9]*.json 2>/dev/null | wc -l | tr -d ' ') / 48 chunks        [$(run cb.py)]"
echo "Kraken        : $(ls $D/kr/*.json 2>/dev/null | wc -l | tr -d ' ') / ~68 days, latest $(ls $D/kr/*.json 2>/dev/null | tail -1 | xargs -n1 basename 2>/dev/null)   [$(run kr.py)]"
echo "Binance 1s    : $(ls $D/bn/*.zip 2>/dev/null | wc -l | tr -d ' ') days (complete)"
echo "Sleep guard   : $(pgrep -f sleepguard-kalshi >/dev/null && echo on || echo off)"
echo "-- last log lines --"; for l in kalshi_trades coinbase kraken; do printf '%-14s %s\n' "$l:" "$(tail -1 $ROOT/logs/$l.log 2>/dev/null)"; done
