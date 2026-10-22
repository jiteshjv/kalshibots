# Gold: her risk check vs the Version C moneyness line, gold bot settings (80/40, max 2000, 20% stop, 99.9c), valid prices.
# bash scripts/run_gold_risk.sh -> results/gr_<name>_*  (filters applied afterwards by gold_fail.py + scratch cmp)
cd /Users/jvidhani/trading/research/kalshi_btc/data_gold
B="SIZE_E=80 SIZE_L=40 MAXHELD=2000 MAXR=0.012 STOPX=0.20 LEADSTOP=0.0001 TP=0.999 TICK=1"
C="MNY_START=0.00132 MNY_FLOOR=0.00044"; R="MNY_START=0 MNY_FLOOR=0"; N="LIM=0.003 WAIT=5 CONFIRM=bid"
V=("now:$C LIM=0.005" "nowB:$C $N" "rk10:$R RISKMAX=0.10 $N" "rk15:$R RISKMAX=0.15 $N" "rk20:$R RISKMAX=0.20 $N"
   "rk25:$R RISKMAX=0.25 $N" "rk15C:$C RISKMAX=0.15 $N" "rk15fv:$R RISKMAX=0.15 $N FVSTOP=2")
for v in "${V[@]}"; do n=${v%%:*}; e="$B ${v#*:}"
  while [ "$(jobs -rp | wc -l)" -ge 8 ]; do sleep 1; done
  (env $e python3 -P ../scripts/her_trades.py 100 composite 0 ../results/gr_$n mixed off > ../logs/gr_$n.log 2>&1; cd .. && SRC=gr_$n python3 -P scripts/gold_fail.py > /dev/null 2>&1) &
done
wait; echo GOLD_RISK_DONE
