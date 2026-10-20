# In-window sharp-move rule (JUMP) with the 25% + 0.03% stop, 3.1% cap, filters on, mixed; Jun 1 - Oct 19 -> jh_/v9_ j4 / j5
cd /Users/jvidhani/trading/research/kalshi_btc
H=("2026-06-01 2026-06-30 jun" "2026-07-01 2026-07-31 jul" "2026-08-01 2026-08-10 aug")
for v in "j4 JUMP=0.004" "j5 JUMP=0.005"; do set -- $v; n=$1; J=$2; E="STOPX=0.25 LEADSTOP=0.0003 $J"
  for m in "${H[@]}"; do set -- $m
    (cd data_hist && env START_DATE=$1 END_DATE=$2 $E python3 -P ../scripts/her_trades.py 0 composite 0 ../results/jh_${n}_$3 mixed on >../logs/jh_${n}_$3.log 2>&1) &
  done
  (cd data && env $E python3 -P ../scripts/her_trades.py 67 composite 0 ../results/v9_${n}_66d mixed on >../logs/v9_${n}_66d.log 2>&1) &
  (cd data_recent && env $E python3 -P ../scripts/her_trades.py 1 composite 0 ../results/v9_${n}_recent mixed on >/dev/null 2>&1) &
  (cd data_mon && env $E python3 -P ../scripts/her_trades.py 1 composite 0 ../results/v9_${n}_mon mixed on >/dev/null 2>&1) &
done; wait; echo JUMP_DONE
