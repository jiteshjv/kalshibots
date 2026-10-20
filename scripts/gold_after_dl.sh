# When the Dukascopy retry round finishes (it rebuilds prices_1s/composite_1s), rerun gold Versions A and C on all days incl. Oct 8-19.
cd /Users/jvidhani/trading/research/kalshi_btc
while pgrep -f "gold_dl.py duka" >/dev/null; do sleep 20; done
grep FINISHED logs/gold_duka_retry.log | tail -1
python3 -P -c "
import numpy as np,datetime as dt;Z=np.load('data_gold/prices_1s.npz');T0=int(Z['T0']);P=np.load('data_gold/composite_1s.npy');o=np.where(~np.isnan(P))[0];print('gold prices now end',dt.datetime.utcfromtimestamp(T0+o[-1]),'UTC')"
cd data_gold
env SIZE_E=40 SIZE_L=20 MAXHELD=1000 MNY_START=0.0010 MNY_FLOOR=0.00033 MAXR=0.012 STOPX=0.20 TP=0.999 python3 -P ../scripts/her_trades.py 100 composite 0 ../results/gn_A mixed off > /dev/null 2>&1 &
env SIZE_E=40 SIZE_L=20 MAXHELD=1000 MNY_START=0.00132 MNY_FLOOR=0.00044 MAXR=0.012 STOPX=0.20 TP=0.999 python3 -P ../scripts/her_trades.py 100 composite 0 ../results/gn_C mixed off > /dev/null 2>&1 &
wait; cd .. && SRC=gn_A python3 -P scripts/gold_fail.py > /dev/null 2>&1; SRC=gn_C python3 -P scripts/gold_fail.py > /dev/null 2>&1; python3 -P scripts/gold_news.py > results/gold_news.txt 2>&1; echo GOLD_RERUN_DONE
