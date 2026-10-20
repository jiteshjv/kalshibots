# Equal sizing x stop variants, Jun 1 - Oct 19 -> jh_/v9_ eq_<stop>[_f]
cd /Users/jvidhani/trading/research/kalshi_btc
one(){ n=$1; filt=$2; E=$3
  for m in "2026-06-01 2026-06-30 jun" "2026-07-01 2026-07-31 jul" "2026-08-01 2026-08-10 aug"; do set -- $m
    (cd data_hist && env START_DATE=$1 END_DATE=$2 $E python3 -P ../scripts/her_trades.py 0 composite 0 ../results/jh_${n}_$3 equal $filt >/dev/null 2>&1) &
  done
  (cd data && env $E python3 -P ../scripts/her_trades.py 67 composite 0 ../results/v9_${n}_66d equal $filt >/dev/null 2>&1) &
  (cd data_recent && env $E python3 -P ../scripts/her_trades.py 1 composite 0 ../results/v9_${n}_recent equal $filt >/dev/null 2>&1) &
  (cd data_mon && env $E python3 -P ../scripts/her_trades.py 1 composite 0 ../results/v9_${n}_mon equal $filt >/dev/null 2>&1) &
}
one eq_l3 off "LEADSTOP=0.0003"; one eq_l3_f on "LEADSTOP=0.0003"; wait
one eq_s25l3 off "STOPX=0.25 LEADSTOP=0.0003"; one eq_s25l3_f on "STOPX=0.25 LEADSTOP=0.0003"; wait
echo EQSTOPS_DONE
