# Gold gates re-check (2026-10-21): gold bot settings after the Oct 21 update (equal 40, max 2000, 0.115% -> 0.030%, 20%/0.01% stop,
# 99.9c, bids 0.3c/1c, 5 s wait, stop re-check), sweep filters OFF; 2h range cap varied. -> results/gg_<name>_*  (+ gold_fail features)
cd /Users/jvidhani/trading/research/kalshi_btc/data_gold
B="SIZE_E=40 SIZE_L=40 MAXHELD=2000 STOPX=0.20 LEADSTOP=0.0001 TP=0.999 TICK=1 MNY_START=0.00115 MNY_FLOOR=0.0003 LIM=0.003 WAIT=5 CONFIRM=bid"
V=("r100:MAXR=1" "r080:MAXR=0.008" "r100c:MAXR=0.010" "r120:MAXR=0.012" "r150:MAXR=0.015" "r200:MAXR=0.020")
for v in "${V[@]}"; do n=${v%%:*}; e="$B ${v#*:}"
  (env $e python3 -P ../scripts/her_trades.py 100 composite 0 ../results/gg_$n mixed off > ../logs/gg_$n.log 2>&1; cd .. && SRC=gg_$n python3 -P scripts/gold_fail.py > /dev/null 2>&1) &
done
wait; echo GOLD_GATES_DONE
