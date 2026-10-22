# Every BTC sheet option on the new day (data_oct21/: Oct 20 19:00 UTC -> Oct 21 ~16:00 UTC, long history stitched) -> results/<prefix>_oct21_*
# TICK=0 to match the older periods of these options (the bot's own option, v9_fvs2Bt1, uses valid prices: TICK=1)
cd /Users/jvidhani/trading/research/kalshi_btc/data_oct21
L3="LEADSTOP=0.0003"; S25="STOPX=0.25 LEADSTOP=0.0003"; REC="STOPX=0.25 LEADSTOP=0.0003 JUMP=0.005"
r(){ (env TICK=0 ${=4} python3 -P ../scripts/her_trades.py 0 composite 0 ../results/$1_oct21 $2 $3 > ../logs/oct21_$1.log 2>&1) & }
r v8_mixed mixed off ""; r v8_mixed_f mixed on ""; r v9_l3 mixed off "$L3"; r v9_l3_f mixed on "$L3"; r v9_s25l3 mixed off "$S25"; r v9_s25l3_f mixed on "$S25"
r v8_equal equal off ""; r v8_equal_f equal on ""; r v9_eq_l3 equal off "$L3"; r v9_eq_l3_f equal on "$L3"; r v9_eq_s25l3 equal off "$S25"; r v9_eq_s25l3_f equal on "$S25"
wait
r v8_mixed5 mixed off "STEP=5"; r v8_mixed5_f mixed on "STEP=5"; r v9_j5L mixed on "$REC"; r v9_eqrecL equal on "$REC"; r v9_m5recL mixed on "$REC STEP=5"; r v9_fvs2L equal on "$REC FVSTOP=2"
r v9_j5B mixed on "$REC SWEEPRULE=b314"; r v9_eqrecB equal on "$REC SWEEPRULE=b314"; r v9_m5recB mixed on "$REC STEP=5 SWEEPRULE=b314"
wait; echo OCT21_RUNS_DONE
