# No-cap runs (MAXR=1, sweep filters on, mixed) for the volatility / news study, Jun 1 - Oct 19: now stop and 25% + 0.03% stop
cd /Users/jvidhani/trading/research/kalshi_btc
H=("2026-06-01 2026-06-30 jun" "2026-07-01 2026-07-31 jul" "2026-08-01 2026-08-10 aug")
for v in "nc MAXR=1" "ncs MAXR=1 STOPX=0.25 LEADSTOP=0.0003"; do set -- $v; n=$1; shift; E="$*"
  for m in "${H[@]}"; do set -- $m
    [ -s results/jh_${n}_$3_markets.csv ] || (cd data_hist && env START_DATE=$1 END_DATE=$2 $E python3 -P ../scripts/her_trades.py 0 composite 0 ../results/jh_${n}_$3 mixed on >/dev/null 2>&1) &
  done
  (cd data && env $E python3 -P ../scripts/her_trades.py 67 composite 0 ../results/v9_${n}_66d mixed on >/dev/null 2>&1) &
  (cd data_recent && env $E python3 -P ../scripts/her_trades.py 1 composite 0 ../results/v9_${n}_recent mixed on >/dev/null 2>&1) &
  (cd data_mon && env $E python3 -P ../scripts/her_trades.py 1 composite 0 ../results/v9_${n}_mon mixed on >/dev/null 2>&1) &
done; wait; echo VOLRUNS_DONE
