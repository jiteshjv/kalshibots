# Every BTC sheet option on the new days (data_oct20/: Oct 19 20:00 UTC -> now) -> results/<prefix>_oct20_*
cd /Users/jvidhani/trading/research/kalshi_btc/data_oct20
L3="LEADSTOP=0.0003"; S25="STOPX=0.25 LEADSTOP=0.0003"; REC="STOPX=0.25 LEADSTOP=0.0003 JUMP=0.005"
r(){ (env $4 python3 -P ../scripts/her_trades.py 0 composite 0 ../results/$1_oct20 $2 $3 > ../logs/oct20_$1.log 2>&1) & }
r v8_mixed mixed off ""; r v8_mixed_f mixed on ""; r v9_l3 mixed off "$L3"; r v9_l3_f mixed on "$L3"; r v9_s25l3 mixed off "$S25"; r v9_s25l3_f mixed on "$S25"
r v8_equal equal off ""; r v8_equal_f equal on ""; r v9_eq_l3 equal off "$L3"; r v9_eq_l3_f equal on "$L3"; r v9_eq_s25l3 equal off "$S25"; r v9_eq_s25l3_f equal on "$S25"
wait
r v8_mixed5 mixed off "STEP=5"; r v8_mixed5_f mixed on "STEP=5"; r v9_j5 mixed on "$REC"; r v9_eqrec equal on "$REC"; r v9_m5rec mixed on "$REC STEP=5"; r v9_fvs2 equal on "$REC FVSTOP=2"
wait; echo OCT20_RUNS_DONE
