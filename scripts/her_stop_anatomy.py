# What our side's bids do around each stop-out (real book): best bid and top-5 depth from -10 s to +10 s of the sale.
#   python3 -P scripts/her_stop_anatomy.py <prefix>   (entries from a HERBOOK=1 run)
import glob,os,sys,numpy as np,pandas as pd
PRE=sys.argv[1]
fs=glob.glob(f"results/jh_{PRE}_*_entries.csv")+glob.glob(f"results/v9_{PRE}_*_entries.csv")
E=pd.concat([pd.read_csv(f) for f in fs]).drop_duplicates(["ticker","entry_no"],keep="last"); S=E[E.exit=="stopped"]
IDX={os.path.basename(p)[:-4]:p for p in glob.glob("data_her/reduced/*/KXBTC*.npz")}
meta={}
for f in glob.glob("data*/settled.json")+glob.glob("data_hist/settled.json"):
    pass
sec=lambda s:int(s.split(":")[0])*60+int(s.split(":")[1])
OFF=np.arange(-10,10.01,0.5); rows=[]
for (tk,xt,sd),g in S.groupby(["ticker","exit_time","side"]):
    if tk not in IDX: continue
    z=np.load(IDX[tk]); t=z["t"]; import json; m=json.loads(str(z["meta"])); 
    import datetime as dt; op=dt.datetime.fromisoformat(m["close_time"].replace("Z","+00:00")).timestamp()-900
    T=(op+sec(xt))*1000; side="y" if sd.startswith("YES") else "n"
    P,Q=(z["yp"],z["yq"]) if side=="y" else (z["np"],z["nq"])
    i=np.searchsorted(t,T+OFF*1000,side="right")-1; ok=i>=0
    bb=np.where(ok,P[np.maximum(i,0),0],0)/1000; dep=np.where(ok,Q[np.maximum(i,0)].sum(1),0)
    # depth within 5c of the best bid at -10 s (liquidity near the old price)
    rows.append(dict(ticker=tk,n=g.n.sum(),exit_px=(g.exit_px*g.n).sum()/g.n.sum(),stop=(g.stop*g.n).sum()/g.n.sum(),**{f"b{o:+.1f}":b for o,b in zip(OFF,bb)},**{f"d{o:+.1f}":d for o,d in zip(OFF,dep)}))
D=pd.DataFrame(rows); D.to_csv(f"results/{PRE}_stop_anatomy.csv",index=False)
print(len(D),"stop events,",int(D.n.sum()),"contracts; median size",D.n.median())
show=[-10,-5,-3,-2,-1,-0.5,0,0.5,1,1.5,2,3,4,5,7,10]
print(" sec   best bid med  mean |  top5 depth med  mean | depth < our size")
for o in show:
    b=D[f"b{o:+.1f}"]; d=D[f"d{o:+.1f}"]
    print(f"{o:+5.1f}   {b.median()*100:6.1f}c {b.mean()*100:6.1f}c | {d.median():9.0f} {d.mean():9.0f} | {(d<D.n).mean():.1%}")
print("our real sale price: median %.1fc mean %.1fc; stop price mean %.1fc"%(D.exit_px.median()*100,D.exit_px.mean()*100,D.stop.mean()*100))
for o in (2,3,5,10):
    x=D[f"b{o:+.1f}"]-D.exit_px; print(f"best bid {o}s after the sale minus our price: median {x.median()*100:+.1f}c mean {x.mean()*100:+.1f}c, higher in {(x>0).mean():.0%}")
