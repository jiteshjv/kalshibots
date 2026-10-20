# Search for rules that improve the friend-style trade, with a date split (run from data/: python3 -P ../scripts/rule_search.py)
# train = first 60% of windows by date, val = next 20%, test = last 20% (looked at once, at the end).
# A rule trades the FIRST 5-second candidate in each window that passes it; P&L uses the resting 95c sell.
import math,sys,itertools,warnings
import numpy as np,pandas as pd
warnings.filterwarnings("ignore")
D=pd.read_pickle("cands.pkl")
ops=np.sort(D.op.unique()); a,b=ops[int(.6*len(ops))],ops[int(.8*len(ops))]
D["part"]=np.where(D.op<a,"train",np.where(D.op<b,"val","test"))
days={p:(D[D.part==p].op.max()-D[D.part==p].op.min())/86400 for p in ("train","val","test")}
fmt=lambda t:pd.to_datetime(t,unit="s").strftime("%b %d")
print(f"train {fmt(ops[0])}-{fmt(a)} ({days['train']:.0f}d) | val {fmt(a)}-{fmt(b)} ({days['val']:.0f}d) | test {fmt(b)}-{fmt(ops[-1])} ({days['test']:.0f}d)")
FEAT=["s","m","z","fair","vol2h","vol15","volin","volratio","mom5","mom15","mom30","mom60","maxm","dd","minm","hold_s","flips",
      "kp","kmove30","kmove60","flow15","flow60","kvol60","kvol_all","cb3","bn3","kr3","bn_cb3","cbv60","bnv60","hour","prev_same"]
D["edge_fair"]=D.fair-D.e
FEAT.append("edge_fair")
def trade(sub,mask,col="pnl_opt"):
    t=sub[mask].groupby("op",sort=False).head(1)
    return t
def stats(t,part,col="pnl_opt"):
    if len(t)<10: return None
    v=t[col].to_numpy(); return dict(n=len(v),perday=len(v)/days[part],win=t.win.mean(),entry=t.e.mean(),
        pnl=v.mean()*100,se=1.96*v.std()/math.sqrt(len(v))*100)
def show(tag,res):
    s=" | ".join(f"{p}: {r['perday']:4.1f}/d win {r['win']*100:4.1f}% @{r['entry']*100:.0f}c {r['pnl']:+5.2f}c±{r['se']:.2f}" if r else f"{p}: --" for p,r in res.items())
    print(f"{tag:58s} {s}")
def evalrule(mask_fn,col="pnl_opt"):
    out={}
    for p in ("train","val","test"):
        sub=D[D.part==p]; out[p]=stats(trade(sub,mask_fn(sub)),p,col)
    return out
base=lambda d:(d.m>=0.0010)&(d.e>=0.65)&(d.e<=0.75)
print("\n== Baseline (her rule: >=0.10% past start, buy 65-75c taker, sell 95c) ==")
for col in ("pnl_opt","pnl_cons"):
    r=evalrule(base,col); show(f"baseline {col}",r)

# ---------- A) single-feature screen, on all candidates in her universe (0.05%+ past start, entry 65-80c)
U=D[(D.m>=0.0005)&(D.e>=0.65)&(D.e<=0.80)]
print(f"\n== A) Single-feature screen: mean (win - price) per candidate by train quintile; {len(U)} candidates ==")
print("   'excess' = actual win rate minus price paid (market mispricing). Consistent if same sign in train and val.")
rows=[]
for f in FEAT:
    tr=U[U.part=="train"]; va=U[U.part=="val"]
    x=tr[f].dropna()
    if x.nunique()<5: qs=sorted(x.unique()); cut=None
    edges=np.unique(np.quantile(x,[0,.2,.4,.6,.8,1]))
    if len(edges)<3: continue
    def bucket(d):
        return pd.cut(d[f],edges,include_lowest=True,labels=False,duplicates="drop")
    gt=(tr.win-tr.e).groupby(bucket(tr)).mean()*100; gv=(va.win-va.e).groupby(bucket(va)).mean()*100
    spread_t=gt.iloc[-1]-gt.iloc[0]; spread_v=gv.iloc[-1]-gv.iloc[0] if len(gv)>1 else np.nan
    rows.append((f,spread_t,spread_v,gt.round(1).tolist(),gv.round(1).tolist()))
rows.sort(key=lambda r:-abs(r[1]))
print(f"{'feature':10s} {'top-bottom train':>16s} {'val':>6s}   train quintiles (low->high)        | val quintiles")
for f,st_,sv,gt,gv in rows:
    flag="  <- consistent" if st_*sv>0 and abs(sv)>1.5 and abs(st_)>1.5 else ""
    print(f"{f:10s} {st_:+16.1f} {sv:+6.1f}   {str(gt):34s} | {str(gv)}{flag}")

# ---------- B) rule search: her rule + up to 2 extra conditions, scored on train, filtered on val
print("\n== B) Rule search: base universe (>=0.05% past start, entry band) + up to 2 feature thresholds ==")
tr=D[D.part=="train"]
cands=[]
for f in FEAT:
    x=tr[f].dropna()
    for q in (.2,.4,.6,.8):
        t=float(np.quantile(x,q))
        cands.append((f,">=",t)); cands.append((f,"<=",t))
bands=[(0.65,0.75),(0.70,0.80),(0.60,0.70),(0.75,0.85)]
def cmask(d,c):
    f,op,t=c; return (d[f]>=t) if op==">=" else (d[f]<=t)
def score(d,part,band,conds):
    m=(d.m>=0.0005)&(d.e>=band[0])&(d.e<=band[1])
    for c in conds: m&=cmask(d,c)
    return stats(trade(d,m),part)
res=[]
for band in bands:
    for c in cands:
        r=score(tr,"train",band,[c])
        if r and r["perday"]>=5: res.append((r["pnl"],band,(c,),r))
res.sort(key=lambda z:-z[0])
top1=res[:40]
for _,band,conds,_ in list(top1):
    for c in cands:
        if c[0]==conds[0][0]: continue
        r=score(tr,"train",band,list(conds)+[c])
        if r and r["perday"]>=5: res.append((r["pnl"],band,conds+(c,),r))
res.sort(key=lambda z:-z[0])
print(f"tested {len(res)} rules with >=5 trades/day on train. Top 15 on train, with val:")
va=D[D.part=="val"]; scored=[]
seen=set()
for p,band,conds,rt in res:
    key=(band,conds)
    if key in seen: continue
    seen.add(key)
    rv=score(va,"val",band,conds)
    scored.append((band,conds,rt,rv))
    if len(scored)>=200: break
name=lambda band,conds:f"{band[0]:.2f}-{band[1]:.2f} "+" & ".join(f"{f}{o}{t:.4g}" for f,o,t in conds)
for band,conds,rt,rv in scored[:15]:
    show(name(band,conds),{"train":rt,"val":rv})
best=[z for z in scored if z[3] and z[3]["perday"]>=3]
best.sort(key=lambda z:-(min(z[2]["pnl"],z[3]["pnl"])))
print("\nBest 5 by the WORSE of train and val (robustness), then the test period (first look):")
for band,conds,rt,rv in best[:5]:
    rte=score(D[D.part=="test"],"test",band,conds)
    show(name(band,conds),{"train":rt,"val":rv,"test":rte})
    rc={p:score(D[D.part==p].assign(pnl_opt=D[D.part==p].pnl_cons),p,band,conds) for p in ("train","val","test")}
    show("   same, conservative 95c fills",rc)

# ---------- C) model: predict win from features + market price; trade when predicted edge > threshold
print("\n== C) Models: predict win from all features incl. market price; trade first candidate with predicted edge > k ==")
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.impute import SimpleImputer
from sklearn.metrics import brier_score_loss,log_loss
M=D[(D.m>=0.0005)&(D.e>=0.60)&(D.e<=0.85)].copy()
M["logit_e"]=np.log(M.e/(1-M.e))
X=FEAT+["logit_e"]
trM,vaM,teM=(M[M.part==p] for p in ("train","val","test"))
models={"logistic":make_pipeline(SimpleImputer(),StandardScaler(),LogisticRegression(C=0.1,max_iter=2000)),
        "boosted trees":HistGradientBoostingClassifier(max_depth=3,learning_rate=0.05,max_iter=300,min_samples_leaf=400,l2_regularization=1.0)}
for nm,mdl in models.items():
    mdl.fit(trM[X],trM.win)
    for p,d in (("val",vaM),("test",teM)):
        ph=mdl.predict_proba(d[X])[:,1]
        print(f"  {nm:13s} {p}: Brier model {brier_score_loss(d.win,ph):.4f} vs market price {brier_score_loss(d.win,d.e):.4f}  (lower = better)")
    vaM2=vaM.assign(edge=mdl.predict_proba(vaM[X])[:,1]-vaM.e); teM2=teM.assign(edge=mdl.predict_proba(teM[X])[:,1]-teM.e)
    for k in (0.00,0.02,0.04,0.06):
        rv=stats(trade(vaM2,vaM2.edge>k),"val"); rt=stats(trade(teM2,teM2.edge>k),"test")
        rvc=stats(trade(vaM2,vaM2.edge>k),"val","pnl_cons"); rtc=stats(trade(teM2,teM2.edge>k),"test","pnl_cons")
        show(f"  {nm} edge>{k:.2f} (opt fills)",{"val":rv,"test":rt})
        show(f"  {nm} edge>{k:.2f} (cons fills)",{"val":rvc,"test":rtc})
