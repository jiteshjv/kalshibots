# Candidate-entry table for rule search (run from data/: python3 -P ../scripts/features.py) -> data/cands.pkl
# Every 5s from min 2 to min 14 of each window: side = the side the BRTI proxy is on (needs >= 0.03% past start),
# entry = VWAP paid by side-takers in [t+1,t+3] (1s latency), kept if entry in [0.55,0.88].
# Features use only data up to t. Outcomes: settle win, and P&L with a resting 95c sell (opt: print >=95c; cons: >=96c).
import json,math,os,datetime as dt
import numpy as np, pandas as pd
from multiprocessing import Pool
Z=np.load("prices_1s.npz"); T0=int(Z["T0"]); C=np.load("composite_1s.npy")
CB,BN,KR=Z["cb"],Z["bn"],Z["kr"]; CBV,BNV,KRV=Z["cbv"],Z["bnv"],Z["krv"]
def ts(s): return int(dt.datetime.fromisoformat(s.replace("Z","+00:00")).timestamp())
def fee(p,n=5): return math.ceil(0.07*n*p*(1-p)*100-1e-9)/100/n
meta=sorted(json.load(open("settled.json")),key=lambda x:x["close_time"])
prev={}
for a,b in zip(meta,meta[1:]): prev[b["ticker"]]=a
def one(x):
    f=f"ktr/{x['ticker']}.json"
    if x["result"] not in("yes","no") or not os.path.exists(f): return []
    op=ts(x["close_time"])-900; i0=op-T0
    if i0-7200<0 or i0+900>len(C): return []
    path=C[i0-60:i0+900]
    if np.isnan(path).any(): return []
    pre=C[i0-7200:i0]; pre=pre[~np.isnan(pre)]
    if len(pre)<3600: return []
    r10=np.diff(np.log(pre[::10])); vol2h=np.std(r10)/math.sqrt(10)            # per-second sigma, 10s returns
    r10b=np.diff(np.log(C[i0-900:i0][::10])); vol15=np.nanstd(r10b)/math.sqrt(10)
    y=x["result"]=="yes"; ref=path[:60].mean()
    K={int(k):v for k,v in json.load(open(f)).items()}
    # per-second Kalshi YES price (ffill) and taker volumes per side
    yp=np.full(900,np.nan); vy=np.zeros(900); vn=np.zeros(900); usd=np.zeros(900)
    for u in range(900):
        a=K.get(op+u)
        if not a: continue
        vy[u]=a["y"][1]; vn[u]=a["n"][1]; usd[u]=a["y"][2]+a["n"][2]
        q=a["y"][1]+a["n"][1]
        if q>0: yp[u]=(a["y"][2]+(a["n"][1]-a["n"][2]))/q        # NO taker at q -> YES traded at 1-q
    yp=pd.Series(yp).ffill().to_numpy()
    pw=prev.get(x["ticker"]); prev_res=None if not pw else (pw["result"]=="yes")
    m_all=(path[60:]-ref)/ref
    sideall=np.where(m_all>=0.0003,1,np.where(m_all<=-0.0003,-1,0))
    out=[]
    for s in range(120,841,5):
        sg=int(np.sign(m_all[s]))
        if abs(m_all[s])<0.0003: continue
        side="y" if sg>0 else "n"
        q=n=0
        for u in range(s+1,s+4):
            a=K.get(op+u)
            if a and a[side][1]>0: q+=a[side][1]; n+=a[side][2]
        if not q: continue
        e=n/q
        if not (0.55<=e<=0.88): continue
        win=y if side=="y" else (not y)
        tp_o=tp_c=None
        for u in range(s+6,900):
            a=K.get(op+u)
            if a and a[side][1]>0:
                v=a[side][2]/a[side][1]
                if tp_o is None and v>=0.95: tp_o=u
                if v>=0.96: tp_c=u; break
        hold=(1 if win else 0)-e-fee(e)
        tp=0.95-e-fee(e)-fee(0.95)
        m=m_all[s]*sg; rem=900-s
        seg=path[60:60+s+1]; sm=(seg-ref)/ref*sg
        flips=int(np.sum(np.abs(np.diff(sideall[:s+1]))>0))
        h=0
        while h<s and sideall[s-h]==sg: h+=1
        rin=np.diff(np.log(seg)); volin=np.std(rin) if len(rin)>10 else np.nan
        def mom(k): return (path[60+s]-path[60+s-k])/ref*sg
        def sideflow(k):
            a=vy[s-k+1:s+1].sum(); b=vn[s-k+1:s+1].sum()
            if side=="n": a,b=b,a
            return (a-b)/(a+b) if a+b>0 else 0.0
        kp=yp if side=="y" else 1-yp
        kp_now=kp[s]; kp30=kp[s-30]; kp60=kp[s-60]
        sig=vol2h*math.sqrt(rem)
        z=m/sig if sig>0 else np.nan
        g=i0+s
        def xr(A,k): return (A[g]-A[g-k])/ref*sg if not (np.isnan(A[g]) or np.isnan(A[g-k])) else np.nan
        tm=dt.datetime.fromtimestamp(op,dt.timezone.utc)
        out.append(dict(ticker=x["ticker"],op=op,s=s,side=side,e=e,win=win,
            pnl_hold=hold,
            pnl_opt=tp if tp_o else hold, pnl_cons=tp if tp_c else hold,
            m=m,z=z,fair=0.5*(1+math.erf(z/math.sqrt(2))) if z==z else np.nan,
            vol2h=vol2h,vol15=vol15,volin=volin,volratio=volin/vol2h if vol2h>0 else np.nan,
            mom5=mom(5),mom15=mom(15),mom30=mom(30),mom60=mom(60),
            maxm=sm.max(),dd=sm.max()-m,minm=sm.min(),hold_s=h,flips=flips,
            kp=kp_now,kmove30=kp_now-kp30,kmove60=kp_now-kp60,
            flow15=sideflow(15),flow60=sideflow(60),
            kvol60=math.log1p(usd[s-59:s+1].sum()),kvol_all=math.log1p(usd[:s+1].sum()),
            cb3=xr(CB,3),bn3=xr(BN,3),kr3=xr(KR,3),bn_cb3=(xr(BN,3)-xr(CB,3)),
            cbv60=math.log1p(np.nansum(CBV[g-59:g+1])),bnv60=math.log1p(np.nansum(BNV[g-59:g+1])),
            hour=tm.hour,dow=tm.weekday(),
            prev_same=np.nan if prev_res is None else float(prev_res==(side=="y"))))
    return out
if __name__=="__main__":
    with Pool(16) as P: rows=[r for rs in P.imap_unordered(one,meta,chunksize=20) for r in rs]
    df=pd.DataFrame(rows).sort_values(["op","s"]).reset_index(drop=True)
    df.to_pickle("cands.pkl")
    print(len(df),"candidates from",df.ticker.nunique(),"windows")
