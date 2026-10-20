# Compare candidate composites against official BRTI 60s settlement averages (run from data/).
import json,datetime as dt,numpy as np
Z=np.load("prices_1s.npz"); T0=int(Z["T0"]); cb,kr,bn=Z["cb"],Z["kr"],Z["bn"]; cbv,krv=Z["cbv"],Z["krv"]
# Binance is USDT: convert with trailing 1h median of cb/bn ratio (no look-ahead)
ratio=cb/bn; k=3600
r=np.copy(ratio); 
import numpy.lib.stride_tricks as S
med=np.full_like(r,np.nan); blk=np.array([np.nanmedian(r[max(0,i-k):i]) if i>0 else np.nan for i in range(0,len(r),60)])
med=np.repeat(blk,60)[:len(r)]; bnu=bn*med
cands={"Coinbase":cb,"Kraken":kr,"Binance (USD-adj)":bnu,
       "mean(CB,KR)":(cb+kr)/2,"mean(CB,KR,BN)":(cb+kr+bnu)/3,"median(CB,KR,BN)":np.median(np.vstack([cb,kr,bnu]),0)}
m=sorted(json.load(open("settled.json")),key=lambda x:x["close_time"])
def ts(s): return int(dt.datetime.fromisoformat(s.replace("Z","+00:00")).timestamp())
rows=[]
for x in m:
    if x["result"] not in("yes","no") or not x["expiration_value"] or not x["floor_strike"]: continue
    cl=ts(x["close_time"])-T0; ev=float(str(x["expiration_value"]).replace(",","")); fs=float(x["floor_strike"])
    if cl-960<0 or cl>len(cb): continue
    rows.append((cl,ev,fs,x["result"]=="yes"))
print("settled windows usable:",len(rows))
def avg60(a,cl,off): return a[cl-60+off:cl+off].mean()
print("\nAlignment check (Coinbase): MAE vs official by offset of the 60s averaging window")
for off in (-3,-2,-1,0,1,2):
    e=[avg60(cb,cl,off)-ev for cl,ev,_,_ in rows]; print(f"  offset {off:+d}s: MAE ${np.nanmean(np.abs(e)):.2f}")
def evaluate(a,off,train=None,label=""):
    e=np.array([avg60(a,cl,off)-ev for cl,ev,_,_ in rows])
    # outcome reconstructed ENTIRELY from candidate: final avg vs start avg (avg of 60s before window open)
    agree=[]; close=[]
    for cl,ev,fs,y in rows:
        fin=avg60(a,cl,off); st=avg60(a,cl-900,off); ok=((fin>=st)==y); agree.append(ok)
        if abs(ev-fs)<25: close.append(ok)
    return e,np.mean(agree),np.mean(close),len(close)
print(f"\n{'candidate':20s} | bias    MAE    p90 |err|  | outcome match  | close windows (|move|<$25)")
best=None
for name,a in cands.items():
    off=0
    e,ag,cg,nc=evaluate(a,off)
    print(f"{name:20s} | {np.nanmean(e):+6.2f}  {np.nanmean(np.abs(e)):5.2f}  {np.nanpercentile(np.abs(e),90):6.2f}   |   {ag*100:6.2f}%     |  {cg*100:6.2f}%  (n={nc})")
# least-squares weights on first 2/3 (train), evaluate on last 1/3 (test)
X=np.array([[avg60(cb,cl,0),avg60(kr,cl,0),avg60(bnu,cl,0)] for cl,_,_,_ in rows]); y=np.array([ev for _,ev,_,_ in rows])
ok=~np.isnan(X).any(1); n=len(rows); tr=np.arange(n)<int(n*2/3)
D=X-X[:,[0]]   # fit on spreads vs Coinbase so weights sum to 1
A=np.column_stack([D[:,1],D[:,2],np.ones(n)])
w,*_=np.linalg.lstsq(A[ok&tr],(y-X[:,0])[ok&tr],rcond=None)
wk,wb,c=w; wc=1-wk-wb
print(f"\nFitted on first 2/3 of dates: weights CB {wc:.2f}, KR {wk:.2f}, BN {wb:.2f}, offset ${c:+.2f}")
fit=wc*cb+wk*kr+wb*bnu+c
e=np.array([avg60(fit,cl,0)-ev for cl,ev,_,_ in rows])[~tr]
ag=[];cl_=[]
for i,(cl,ev,fs,yy) in enumerate(rows):
    if tr[i]: continue
    fin=avg60(fit,cl,0); st=avg60(fit,cl-900,0); ok2=((fin>=st)==yy); ag.append(ok2)
    if abs(ev-fs)<25: cl_.append(ok2)
print(f"Fitted composite on UNSEEN last 1/3: bias {np.nanmean(e):+.2f}  MAE ${np.nanmean(np.abs(e)):.2f}  p90 ${np.nanpercentile(np.abs(e),90):.2f} | outcome match {np.mean(ag)*100:.2f}% | close windows {np.mean(cl_)*100:.2f}% (n={len(cl_)})")
np.save("composite_1s.npy",fit); json.dump({"w_cb":wc,"w_kr":wk,"w_bn":wb,"offset":c},open("composite_weights.json","w"))
