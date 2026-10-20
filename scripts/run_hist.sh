# Jan 1 - Aug 10 backtests in data_hist/, month by month (UTC close dates), 5 variants -> results/jh_<variant>_<month>_*
cd /Users/jvidhani/trading/research/kalshi_btc/data_hist
M=("2026-01-01 2026-01-31 jan" "2026-02-01 2026-02-28 feb" "2026-03-01 2026-03-31 mar" "2026-04-01 2026-04-30 apr" "2026-05-01 2026-05-31 may" "2026-06-01 2026-06-30 jun" "2026-07-01 2026-07-31 jul" "2026-08-01 2026-08-10 aug")
one(){ v=$1; filt=$2; shift 2
  for m in "${M[@]}"; do set -- $m; a=$1; b=$2; n=$3
    [ -s ../results/jh_${v}_${n}_markets.csv ] && continue
    env START_DATE=$a END_DATE=$b $VARS python3 -P ../scripts/her_trades.py 0 composite 0 ../results/jh_${v}_${n} mixed $filt > ../logs/jh_${v}_${n}.log 2>&1 &
  done; wait; echo "done $v"; }
VARS="" one now on; VARS="LEADSTOP=0.0003" one l3 on; VARS="MAXR=1" one nocap on; VARS="STOPX=0.25 LEADSTOP=0.0003" one s25l3 on; VARS="" one off off
echo HISTDONE
