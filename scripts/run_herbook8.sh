# Her stop style: limit sell lowered every few seconds instead of a market sell (LADDER=o,w,d,k), her book, 100 ms trigger
cd /Users/jvidhani/trading/research/kalshi_btc
B="STOPX=0.25 LEADSTOP=0.0003 JUMP=0.005 FVSTOP=2 SWEEPRULE=b314 TICK=1 LIM=0.003 RISKMAX=0.15 MNY_START=0 MNY_FLOOR=0 WAIT=5 CONFIRM=bid"
V=("rkLa:HERBOOK=1 STOPLAT=100 LADDER=1,3,1,5" "rkLb:HERBOOK=1 STOPLAT=100 LADDER=1,5,1,5" "rkLc:HERBOOK=1 STOPLAT=100 LADDER=2,3,1,5" "rkLd:HERBOOK=1 STOPLAT=100 LADDER=1,2,1,10" "rkLe:HERBOOK=1 STOPLAT=100 LADDER=3,5,2,5" "rkLf:HERBOOK=1 STOPLAT=100 LADDER=99,3,1,10")
P=("data_hist 2026-06-01 2026-06-30 jh _jun" "data_hist 2026-07-01 2026-07-31 jh _jul" "data_hist 2026-08-01 2026-08-10 jh _aug"
   "data 67 v9 _66d" "data_recent 1 v9 _recent" "data_mon 1 v9 _mon" "data_oct20 0 v9 _oct20" "data_oct21 0 v9 _oct21")
for v in "${V[@]}"; do n=${v%%:*}; e="$B ${v#*:}"
  for p in "${P[@]}"; do set -- $p
    if [ "$1" = data_hist ]; then echo "cd $1 && env START_DATE=$2 END_DATE=$3 $e python3 -P ../scripts/her_trades.py 0 composite 0 ../results/$4_${n}$5 equal on > ../logs/zn_${n}$5.log 2>&1"
    else echo "cd $1 && env $e python3 -P ../scripts/her_trades.py $2 composite 0 ../results/$3_${n}$4 equal on > ../logs/zn_${n}$4.log 2>&1"; fi
  done
done > logs/run_hb8_cmds.txt
while IFS= read -r c; do while [ "$(jobs -rp | wc -l)" -ge 14 ]; do sleep 1; done; (eval "$c") & done < logs/run_hb8_cmds.txt
wait
echo HB_DONE
