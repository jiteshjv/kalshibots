# Equal sizing (100 every entry) + recommended rules (25% + 0.03% stop, 0.5% jump pause, sweep filters), Jun 1 - Oct 19 -> jh_/v9_ eqrec
cd /Users/jvidhani/trading/research/kalshi_btc
E="STOPX=0.25 LEADSTOP=0.0003 JUMP=0.005"
for m in "2026-06-01 2026-06-30 jun" "2026-07-01 2026-07-31 jul" "2026-08-01 2026-08-10 aug"; do set -- $m
  (cd data_hist && env START_DATE=$1 END_DATE=$2 $E python3 -P ../scripts/her_trades.py 0 composite 0 ../results/jh_eqrec_$3 equal on >/dev/null 2>&1) &
done
(cd data && env $E python3 -P ../scripts/her_trades.py 67 composite 0 ../results/v9_eqrec_66d equal on >/dev/null 2>&1) &
(cd data_recent && env $E python3 -P ../scripts/her_trades.py 1 composite 0 ../results/v9_eqrec_recent equal on >/dev/null 2>&1) &
(cd data_mon && env $E python3 -P ../scripts/her_trades.py 1 composite 0 ../results/v9_eqrec_mon equal on >/dev/null 2>&1) &
wait; echo EQREC_DONE
