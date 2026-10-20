# Mixed every 5 s (2x buying) + recommended rules (25% + 0.03% stop, 0.5% jump pause, sweep filters), Jun 1 - Oct 19 -> jh_/v9_ m5rec
cd /Users/jvidhani/trading/research/kalshi_btc
E="STOPX=0.25 LEADSTOP=0.0003 JUMP=0.005 STEP=5"
for m in "2026-06-01 2026-06-30 jun" "2026-07-01 2026-07-31 jul" "2026-08-01 2026-08-10 aug"; do set -- $m
  (cd data_hist && env START_DATE=$1 END_DATE=$2 $E python3 -P ../scripts/her_trades.py 0 composite 0 ../results/jh_m5rec_$3 mixed on >/dev/null 2>&1) &
done
(cd data && env $E python3 -P ../scripts/her_trades.py 67 composite 0 ../results/v9_m5rec_66d mixed on >/dev/null 2>&1) &
(cd data_recent && env $E python3 -P ../scripts/her_trades.py 1 composite 0 ../results/v9_m5rec_recent mixed on >/dev/null 2>&1) &
(cd data_mon && env $E python3 -P ../scripts/her_trades.py 1 composite 0 ../results/v9_m5rec_mon mixed on >/dev/null 2>&1) &
wait; echo EQREC_DONE
