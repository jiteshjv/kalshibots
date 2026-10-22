# Early exit before the stop (her real book, 100 ms): for every lot of a HERBOOK=1 run, does a warning sign (bids near the price
# draining, the bid dropping fast, BRTI moving against us) fire while the lot is near its stop, and what would selling then
# (100 ms later, into her real bids) have made vs what the lot actually did?  Lot by lot, no re-entries / rolls after the sale.
#   python3 -P scripts/her_early_exit.py <prefix>        (e.g. rkHBl100 -> results/<prefix>_early_exit.csv + table)
import glob,os,sys,math,numpy as np,pandas as pd
from multiprocessing import Pool
PRE=sys.argv[1]; LAT=100
ROOT=os.path.join(os.path.dirname(os.path.abspath(__file__)),"..")
def fee(p): return 0.07*p*(1-p)
G=[0.03,0.05,0.08,0.10]                      # "near the stop": our best bid <= stop + G
TRIG=[("drain2s<=0.5","dr",0.5),("drain2s<=0.3","dr",0.3),("bid-1s>=3c","d1",0.03),("bid-1s>=5c","d1",0.05),
      ("brti-1s>=1bp","b1",1.0),("brti-1s>=2bp","b1",2.0),("brti-1s>=3bp","b1",3.0),("brti-2s>=3bp","b2",3.0),("brti-2s>=5bp","b2",5.0),
      ("near only","no",0)]
LEAD=[None,0.0003,0.0005]                    # also BTC within this of the strike (BRTI 5 Hz), like the stop's lead rule
RULES=[(g,t,l) for g in G for t in TRIG for l in LEAD]

_PX={}
def prices(day):
    if day not in _PX:
        fs=[os.path.join(ROOT,"data_her","reduced",d,"prices.npz") for d in (day,(pd.Timestamp(day)+pd.Timedelta(days=1)).strftime("%Y-%m-%d"))]
        zs=[np.load(f) for f in fs if os.path.exists(f)]
        k=lambda z:("ft","fv") if "ft" in z.files else ("et","vwap")   # 5 Hz BRTI from Jun 7; exchange VWAP 1/s before
        _PX.clear(); _PX[day]=(np.concatenate([z[k(z)[0]] for z in zs]),np.concatenate([z[k(z)[1]] for z in zs]))
    return _PX[day]

def walk(Pk,Qk,n):                             # sell n into our side's top 5 bids (dollars)
    lv=[(p/1000,q) for p,q in zip(Pk,Qk) if p>0]
    if not lv: return None
    left=n; c=0.0
    for p,q in lv:
        u=min(left,q); c+=u*p; left-=u
        if left<=0: break
    if left>0: c+=left*lv[-1][0]
    return c/n

def do_market(a):
    path,day,lots=a
    z=np.load(path); t=z["t"]; ft,fv=prices(day)
    import json; m=json.loads(str(z["meta"]))
    op=int(pd.Timestamp(m["close_time"]).timestamp()*1000)-900_000
    g=op+100*np.arange(9000)
    ib=np.searchsorted(t,g,side="right")-1; okb=ib>=0; ib=np.maximum(ib,0)
    ir=np.searchsorted(ft,g,side="right")-1; br=np.where(ir>=0,fv[np.maximum(ir,0)],np.nan)
    out=[]
    for side in ("y","n"):
        sl=lots[lots.side.str.startswith("YES" if side=="y" else "NO")]
        if sl.empty: continue
        P=(z["yp"] if side=="y" else z["np"])[ib].astype(float); Q=(z["yq"] if side=="y" else z["nq"])[ib]
        P[~okb]=0; Q[~okb]=0
        bb=P[:,0]/1000; bb[bb<=0]=np.nan
        d=1 if side=="y" else -1; K=sl.strike.iloc[0]
        thr=np.r_[np.full(20,np.nan),bb[:-20]]-0.05          # band: within 5c below the best bid 2 s ago
        dep_now=np.where(P/1000>=thr[:,None],Q,0).sum(1)
        Pold=np.r_[np.zeros((20,5)),P[:-20]]; Qold=np.r_[np.zeros((20,5)),Q[:-20]]
        dep_old=np.where(Pold/1000>=thr[:,None],Qold,0).sum(1)
        F={"dr":np.where(dep_old>0,dep_now/np.maximum(dep_old,1e-9),np.inf),
           "d1":bb-np.r_[np.full(10,np.nan),bb[:-10]],          # negative = fell
           "b1":(br-np.r_[np.full(10,np.nan),br[:-10]])/br*1e4*d,
           "b2":(br-np.r_[np.full(20,np.nan),br[:-20]])/br*1e4*d}
        lead=(br-K)/K*d
        for L in sl.itertuples():
            a=int(L.ent_s*10)+10
            if L.exit=="stopped":
                s0=(L.ex_s-1)*10; w=np.nonzero(bb[s0:s0+10]<=L.stop+1e-9)[0]; b=s0+(w[0] if len(w) else 9)
                V=L.exit_px-fee(L.exit_px)
            elif L.exit.startswith("sold"): b=int(L.ex_s*10); V=L.exit_px-0.0175*L.exit_px*(1-L.exit_px)
            else: b=9000; V=L.exit_px
            if b<=a: out.append((L.key,L.exit,L.n,V,[None]*len(RULES))); continue
            cand=a+np.nonzero(bb[a:b]<=L.stop+max(G)+1e-9)[0]
            res=[]
            for gg,(nm,f,x),ld in RULES:
                c=cand[bb[cand]<=L.stop+gg+1e-9]
                if f=="dr": c=c[F["dr"][c]<=x]
                elif f=="d1": c=c[F["d1"][c]<=-x]
                elif f in("b1","b2"): c=c[F[f][c]<=-x]
                if ld is not None: c=c[lead[c]<ld]
                if not len(c): res.append(None); continue
                k=c[0]; j=min(k+LAT//100,8999); px=walk(P[j],Q[j],L.n)
                res.append(None if px is None else (k/10,px-fee(px)))
            out.append((L.key,L.exit,L.n,V,res))
    return out

if __name__=="__main__":
    fs=glob.glob(f"{ROOT}/results/jh_{PRE}_*_entries.csv")+glob.glob(f"{ROOT}/results/v9_{PRE}_*_entries.csv")
    E=pd.concat([pd.read_csv(f) for f in fs]).drop_duplicates(["ticker","entry_no"],keep="last")
    Mk=pd.concat([pd.read_csv(f.replace("_entries","_markets")) for f in fs]).drop_duplicates("ticker",keep="last")
    E=E.merge(Mk[["ticker","strike"]],on="ticker")
    sec=lambda s:int(s.split(":")[0])*60+int(s.split(":")[1])
    E["ent_s"]=E.entry_time.map(sec); E["ex_s"]=E.exit_time.map(sec); E["key"]=E.ticker+"#"+E.entry_no.astype(str)
    IDX={os.path.basename(p)[:-4]:p for p in glob.glob(f"{ROOT}/data_her/reduced/*/KXBTC*.npz")}
    E=E[E.ticker.isin(IDX)]
    tasks=[(IDX[tk],os.path.basename(os.path.dirname(IDX[tk])),g) for tk,g in E.groupby("ticker")]
    tasks.sort(key=lambda x:x[1]); tasks=tasks[:int(os.environ.get("NMAX","999999"))]
    with Pool(16) as p: R=[r for rs in p.imap(do_market,tasks,chunksize=20) for r in rs]
    print(f"{len(R)} lots, {sum(r[1]=='stopped' for r in R)} stopped, {len(tasks)} markets")
    rows=[]
    for i,(gg,(nm,_,_),ld) in enumerate(RULES):
        dS=dW=0.0; nS=nW=0
        for key,ex,n,V,res in R:
            x=res[i]
            if x is None: continue
            dv=(x[1]-V)*n
            if ex=="stopped": dS+=dv; nS+=1
            else: dW+=dv; nW+=1
        rows.append(dict(near=f"stop+{gg*100:.0f}c",trigger=nm,lead="any" if ld is None else f"<{ld*100:.2f}%",
                         fires_stopped=nS,saved_on_stopped=round(dS),fires_winners=nW,cost_on_winners=round(dW),net=round(dS+dW)))
    D=pd.DataFrame(rows).sort_values("net",ascending=False); D.to_csv(f"{ROOT}/results/{PRE}_early_exit.csv",index=False)
    pd.set_option("display.width",200); print(D.head(25).to_string(index=False)); print("..."); print(D.tail(5).to_string(index=False))
