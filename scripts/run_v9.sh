# Stop variants for the BTC sheet: l3 = BTC within 0.03%, s25l3 = 25% stop + BTC within 0.03%; mixed sizing; filters off (no suffix) / on (_f)
cd /Users/jvidhani/trading/research/kalshi_btc
run(){ # name filt env...
  n=$1; f=$2; shift 2
  [ -s results/v9_${n}_66d_markets.csv ] || (cd data && env "$@" python3 -P ../scripts/her_trades.py 67 composite 0 ../results/v9_${n}_66d mixed $f >/dev/null 2>&1)
  (cd data_recent && env "$@" python3 -P ../scripts/her_trades.py 1 composite 0 ../results/v9_${n}_recent mixed $f >/dev/null 2>&1)
  (cd data_mon && env "$@" python3 -P ../scripts/her_trades.py 1 composite 0 ../results/v9_${n}_mon mixed $f >/dev/null 2>&1)
  echo "done $n"
}
for s in markets entries skipped; do cp results/stg_l3_$s.csv results/v9_l3_f_66d_$s.csv 2>/dev/null; cp results/stg_s25_l3_$s.csv results/v9_s25l3_f_66d_$s.csv 2>/dev/null; done
run l3 off LEADSTOP=0.0003 & run s25l3 off STOPX=0.25 LEADSTOP=0.0003 & run l3_f on LEADSTOP=0.0003 & run s25l3_f on STOPX=0.25 LEADSTOP=0.0003 & wait
echo V9DONE
