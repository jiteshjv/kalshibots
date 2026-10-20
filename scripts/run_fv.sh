# Fair-value stop / entry variants on the recommended Equal-100 setup, Jun 1 - Oct 19 -> results/{jh,v9}_fv<name>_*
# bash scripts/run_fv.sh <name> "<extra env>"   e.g. bash scripts/run_fv.sh s1 "FVSTOP=1"
cd /Users/jvidhani/trading/research/kalshi_btc
N=$1; E="STOPX=0.25 LEADSTOP=0.0003 JUMP=0.005 $2"
for m in "2026-06-01 2026-06-30 jun" "2026-07-01 2026-07-31 jul" "2026-08-01 2026-08-10 aug"; do set -- $m
  (cd data_hist && env START_DATE=$1 END_DATE=$2 $E python3 -P ../scripts/her_trades.py 0 composite 0 ../results/jh_fv${N}_$3 equal on > ../logs/fv${N}_$3.log 2>&1) &
done
(cd data && env $E python3 -P ../scripts/her_trades.py 67 composite 0 ../results/v9_fv${N}_66d equal on > ../logs/fv${N}_66d.log 2>&1) &
(cd data_recent && env $E python3 -P ../scripts/her_trades.py 1 composite 0 ../results/v9_fv${N}_recent equal on > ../logs/fv${N}_recent.log 2>&1) &
(cd data_mon && env $E python3 -P ../scripts/her_trades.py 1 composite 0 ../results/v9_fv${N}_mon equal on > ../logs/fv${N}_mon.log 2>&1) &
wait; echo FV_${N}_DONE
