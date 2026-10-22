# Sweep zone / cool-down rules on top of the bot now (rkW5c), every period Jun 1 - Oct 21 -> results/{jh,v9}_zn*_*
cd /Users/jvidhani/trading/research/kalshi_btc
B="STOPX=0.25 LEADSTOP=0.0003 JUMP=0.005 FVSTOP=2 SWEEPRULE=b314 TICK=1 LIM=0.003 RISKMAX=0.15 MNY_START=0 MNY_FLOOR=0 WAIT=5 CONFIRM=bid"
V=("znZ5a:SWZONE=0.001 SWZN=5" "znZ5b:SWZONE=0.002 SWZN=5" "znZ5c:SWZONE=0.003 SWZN=5" "znZ3b:SWZONE=0.002 SWZN=3" "znZ7b:SWZONE=0.002 SWZN=7"
   "znC1:SWCOOL=1" "znC2:SWCOOL=2" "znC4:SWCOOL=4" "znZ5bC2:SWZONE=0.002 SWZN=5 SWCOOL=2")
P=("data_hist 2026-06-01 2026-06-30 jh _jun" "data_hist 2026-07-01 2026-07-31 jh _jul" "data_hist 2026-08-01 2026-08-10 jh _aug"
   "data 67 v9 _66d" "data_recent 1 v9 _recent" "data_mon 1 v9 _mon" "data_oct20 0 v9 _oct20" "data_oct21 0 v9 _oct21")
for v in "${V[@]}"; do n=${v%%:*}; e="$B ${v#*:}"
  for p in "${P[@]}"; do set -- $p
    if [ "$1" = data_hist ]; then echo "cd $1 && env START_DATE=$2 END_DATE=$3 $e python3 -P ../scripts/her_trades.py 0 composite 0 ../results/$4_${n}$5 equal on > ../logs/zn_${n}$5.log 2>&1"
    else echo "cd $1 && env $e python3 -P ../scripts/her_trades.py $2 composite 0 ../results/$3_${n}$4 equal on > ../logs/zn_${n}$4.log 2>&1"; fi
  done
done > logs/run_zone_cmds.txt
while IFS= read -r c; do while [ "$(jobs -rp | wc -l)" -ge 12 ]; do sleep 1; done; (eval "$c") & done < logs/run_zone_cmds.txt
wait
echo ZONE_RUNS_DONE
