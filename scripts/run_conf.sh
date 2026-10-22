# Her risk check (RISKMAX) x moneyness line, Equal 100 + bot settings (valid prices), every period Jun 1 - Oct 21 -> results/{jh,v9}_rk*_*
# bash scripts/run_risk.sh   (8 at a time)
cd /Users/jvidhani/trading/research/kalshi_btc
B="STOPX=0.25 LEADSTOP=0.0003 JUMP=0.005 FVSTOP=2 SWEEPRULE=b314 TICK=1"
V=("rkW5c:LIM=0.003 RISKMAX=0.15 MNY_START=0 MNY_FLOOR=0 WAIT=5 CONFIRM=bid")
P=("data_hist 2026-06-01 2026-06-30 jh _jun" "data_hist 2026-07-01 2026-07-31 jh _jul" "data_hist 2026-08-01 2026-08-10 jh _aug"
   "data 67 v9 _66d" "data_recent 1 v9 _recent" "data_mon 1 v9 _mon" "data_oct20 0 v9 _oct20" "data_oct21 0 v9 _oct21")
for v in "${V[@]}"; do n=${v%%:*}; e="$B ${v#*:}"
  for p in "${P[@]}"; do set -- $p
    if [ "$1" = data_hist ]; then echo "cd $1 && env START_DATE=$2 END_DATE=$3 $e python3 -P ../scripts/her_trades.py 0 composite 0 ../results/$4_${n}$5 equal on > ../logs/rk_${n}$5.log 2>&1"
    else echo "cd $1 && env $e python3 -P ../scripts/her_trades.py $2 composite 0 ../results/$3_${n}$4 equal on > ../logs/rk_${n}$4.log 2>&1"; fi
  done
done > logs/run_conf_cmds.txt
while IFS= read -r c; do while [ "$(jobs -rp | wc -l)" -ge 8 ]; do sleep 1; done; (eval "$c") & done < logs/run_conf_cmds.txt
wait
echo RISK_RUNS_DONE
