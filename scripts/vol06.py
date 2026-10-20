# Her 0.6% volatility rule on 66 days: BTC 2h high-low range before the window (composite), <= or >= 0.6%.
# Entry = her rule (>= mny past start, buy 65-75c taker, 1s latency); exit = 95c resting sell (opt) or hold. Date split as rule_search.
# (run from data/: python3 -P ../scripts/vol06.py 67 composite)
import numpy as np
src=open("../scripts/exit_test.py").read(); exec(src[:src.index("print(f\"windows")])
def rng(op):
    i=op-T0; seg=ARR[max(0,i-7200):i]
    return (np.nanmax(seg)-np.nanmin(seg))/np.nanmean(seg) if np.sum(seg==seg)>3600 else np.nan
R={op:rng(op) for _,op,_,_ in W}
v=np.array([x for x in R.values() if x==x]); print(f"2h range across windows: median {np.median(v)*100:.2f}%, share <= 0.6%: {np.mean(v<=0.006)*100:.0f}%\n")
for mny in (0.0010,0.0015,0.0020):
    E=entries(mny)
    for lab,keep in (("no filter",lambda r:True),("2h range <= 0.6%",lambda r:r<=0.006),("2h range >= 0.6%",lambda r:r>=0.006)):
        Ek=[e for e in E if keep(R[e[1]])]
        for tp,nm in ((0.95,"95c exit"),(None,"hold")):
            r=sim(Ek,tp,None,False,None,"opt")
            print(f"mny {mny*100:.2f}% {lab:17s} {nm:8s} | train {summ(r,'train'):28s} | val {summ(r,'val'):28s} | test {summ(r,'test')}")
    print()
