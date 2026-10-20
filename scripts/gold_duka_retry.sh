# Re-run the Dukascopy gold fetch every 30 min until Oct 8-19 weekday hours exist (429 rate limit).
cd /Users/jvidhani/trading/research/kalshi_btc/data_gold
for i in $(seq 1 24); do
  DUKA_GAP=3 python3 -P -u ../scripts/gold_dl.py duka
  n=$(find duka -name "2026-10-1[0-9]_*.bi5" -size +0 | wc -l)
  echo "$(date -u +%FT%TZ) round $i: Oct 10-19 non-empty hours: $n"
  [ "$n" -ge 150 ] && { echo "DONE"; break; }
  sleep 1800
done
