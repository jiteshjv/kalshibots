# Jun 1 - Aug 10 runs for the remaining BTC sheet options -> results/jh_<variant>_<month>
cd /Users/jvidhani/trading/research/kalshi_btc/data_hist
M=("2026-06-01 2026-06-30 jun" "2026-07-01 2026-07-31 jul" "2026-08-01 2026-08-10 aug")
one(){ v=$1; siz=$2; filt=$3; shift 3
  for m in "${M[@]}"; do set -- $m
    [ -s ../results/jh_${v}_$3_markets.csv ] || env START_DATE=$1 END_DATE=$2 $VARS python3 -P ../scripts/her_trades.py 0 composite 0 ../results/jh_${v}_$3 $siz $filt > ../logs/jh_${v}_$3.log 2>&1 &
  done; }
VARS="LEADSTOP=0.0003" one l3off mixed off; VARS="STOPX=0.25 LEADSTOP=0.0003" one s25l3off mixed off; VARS="" one eq equal off; wait
VARS="" one eqf equal on; VARS="STEP=5" one m5 mixed off; VARS="STEP=5" one m5f mixed on; wait
echo SHEETRUNS_DONE
