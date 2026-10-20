# One variant on every BTC period (Jun, Jul, Aug 1-10, Aug 10-Oct 15, Oct 15-19, Oct 19, Oct 19-20) -> results/{jh,v9}_<name>_*
# bash scripts/run_all7.sh <name> <mixed|equal> "<env>"
cd /Users/jvidhani/trading/research/kalshi_btc
N=$1; Z=$2; E=$3
for m in "2026-06-01 2026-06-30 jun" "2026-07-01 2026-07-31 jul" "2026-08-01 2026-08-10 aug"; do set -- $m
  (cd data_hist && env START_DATE=$1 END_DATE=$2 $E python3 -P ../scripts/her_trades.py 0 composite 0 ../results/jh_${N}_$3 $Z on > ../logs/a7_${N}_$3.log 2>&1) &
done
(cd data && env $E python3 -P ../scripts/her_trades.py 67 composite 0 ../results/v9_${N}_66d $Z on > ../logs/a7_${N}_66d.log 2>&1) &
for d in "data_recent recent 1" "data_mon mon 1" "data_oct20 oct20 0"; do set -- $d
  (cd $1 && env $E python3 -P ../scripts/her_trades.py $3 composite 0 ../results/v9_${N}_$2 $Z on > ../logs/a7_${N}_$2.log 2>&1) &
done
wait
