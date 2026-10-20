# Resting sell (TP) default 0.997 since 2026-10-19 (was 0.995). Entries default to a bid 0.5c under the ask, then market after 10 s (LIM/HYB). Per-entry trade log, working model (2026-10-19): every 10 s from min 2 to min 12, threshold 0.15% -> 0.05% (linear, min 2 -> 15) past the
# official strike; buy at market (real prints, 1 s latency) if ask < 98.5c: 200 contracts per entry before min 6, 100 after; <= 5,000 held;
# each entry its own stop: sold at market (first trades after the trigger) when our side trades <= 0.7 x its buy price AND BTC is within
# +0.01% of the strike; resting sell at 99.7c for everything open; else settle. Roll: once all open entries are closed, new entries may go
# on the other side.
# (run from data/ or data_recent/: python3 -P ../scripts/her_trades.py 67 composite <days or 0> <out_prefix>)
import sys,math,json,os
import numpy as np
from zoneinfo import ZoneInfo
src=open("../scripts/her_full.py").read(); exec(src[:src.index("def sim2")])
KS={ts(x["close_time"])-900:float(x["floor_strike"]) for x in meta if x.get("floor_strike")}
TK={ts(x["close_time"])-900:x["ticker"] for x in meta}
PT=ZoneInfo("America/Los_Angeles"); X=float(os.environ.get("STOPX","0.30")); CAP=0.985; TP=float(os.environ.get("TP","0.997")); START=0.0015; LEADSTOP=float(os.environ.get("LEADSTOP","0.0001")); LAST=720
STOPFRAC=float(os.environ.get("STOPFRAC","0"))
CONFIRM=os.environ.get("CONFIRM","off")
ROLLFILT=int(os.environ.get("ROLLFILT","0")); ROLLSKIP=[0]   # 1 = sweep filters also checked before a roll; 2 = and a hit blocks rolls for the market
JUMP=float(os.environ.get("JUMP","0"))   # >0: no new entries if any clock minute in the last 15 moved more than this (fraction, e.g. 0.004)
if JUMP>0:
    _a=(-T0)%60; _n=(len(ARR)-_a)//60; _cl=ARR[_a+59:_a+_n*60:60]          # minute closes (minute m starts at T0+_a+60m)
    _jm=np.abs(np.r_[np.nan,_cl[1:]/_cl[:-1]-1]); JR15=pd.Series(_jm).rolling(15,min_periods=1).max().to_numpy(); _JM0=T0+_a
def jumped(t):                  # t = unix second of the decision; uses the last COMPLETED minutes only
    if JUMP<=0: return False
    m=int((t-_JM0)//60)-1
    return 0<=m<len(JR15) and JR15[m]==JR15[m] and JR15[m]>JUMP   # off | bid | btc | both: re-check the stop at the sell second   # 0 = old model (first second's price); >0 = size-aware: per second sell at most this share of the traded volume
# Fair value from BTC (2026-10-20): P(final 60 s BRTI average >= strike) under a random walk with sigma = realized 1 s vol over the
# 30 min before the open. In the last 60 s the observed part of the average is locked in.
#   FVSTOP=1: a lot's stop triggers when OUR side's fair value <= its stop price (instead of the traded price), still needs lead < LEADSTOP
#   FVSTOP=2: either the traded price or the fair value <= the stop price
#   FVENT=x : skip an entry when the buy price is more than x above our side's fair value (0 = off)
# GAP=x, GAPW=w (2026-10-20): if a stop would sell more than x below a lot's stop price (a gap), wait w s and re-check that lot once.
GAP=float(os.environ.get("GAP","0")); GAPW=int(os.environ.get("GAPW","2")); GAPN=[0,0]   # [lots that waited, of those sold later]
FVSTOP=int(os.environ.get("FVSTOP","0")); FVENT=float(os.environ.get("FVENT","0")); FVBLK=[0]; FVD=[]
_W60=sum((k/60)**2 for k in range(1,61))
def sig_at(op):
    seg=ARR[op-1800-T0:op-T0]
    if len(seg)<1800: return float("nan")
    with np.errstate(all="ignore"): r=np.diff(np.log(seg))
    return float(np.nanstd(r)) if np.isfinite(r).sum()>600 else float("nan")
def fair_yes(path,u,K0,sg):
    p=path[60+u]; left=899-u
    if sg!=sg or sg<=0: return None
    if left>=60: mu=p; var=(left-59)+_W60
    else:
        mu=(sum(path[60+840:60+u+1])+p*left)/60; var=sum((k/60)**2 for k in range(1,left+1))
    sd=sg*p*math.sqrt(max(var,1e-9))
    return 0.5*(1+math.erf((mu-K0)/(sd*math.sqrt(2))))
def sell_sized(K,t,side,qty,frac):
    """Sell qty contracts of `side` from second t on: each second take at most frac x the contracts traded on the other side, at that second's
    price (1 - their VWAP), until filled (up to 60 s; any remainder at 1c). Returns the average sale price."""
    o="n" if side=="y" else "y"; left=qty; got=0.0
    for u in range(t,t+60):
        a=K.get(u)
        if a and a[o][1]>0:
            take=min(left,frac*a[o][1]); got+=take*(1-a[o][2]/a[o][1]); left-=take
            if left<=1e-9: break
    if left>1e-9: got+=left*0.01
    return got/qty
def sell_fast(K,t,side):
    o="n" if side=="y" else "y"
    for u in range(t,t+4):
        a=K.get(u)
        if a and a[o][1]>0: return 1-a[o][2]/a[o][1]
    return None
def swept(i,d,k):              # in the last 5 min, did BTC trade beyond the extreme of the k seconds before that, on side d?
    if i-k-300<0: return False
    prior=ARR[i-k-300:i-300]; last5=ARR[i-300:i+1]
    if np.isnan(prior).all() or np.isnan(last5).all(): return False
    return bool(d*((np.nanmax(last5) if d>0 else np.nanmin(last5))-(np.nanmax(prior) if d>0 else np.nanmin(prior)))>0)
SWEEPRULE=os.environ.get("SWEEPRULE","cur")   # cur = 2-day+ sweeps + 4h/stretched 1h; b314 = only 3- to 14-day sweeps (swept 3-day, not 30-day), no 4h rule
def skip_market(t,d):
    if SWEEPRULE=="b314":
        i=t-T0; p=ARR[i]
        if p!=p or not swept(i,d,3*86400) or swept(i,d,30*86400): return None
        for n in (14,7,5,3):
            if swept(i,d,n*86400): return f"{n}-day sweep"
    return skip_market_cur(t,d)
def skip_market_cur(t,d):           # filter 1: ran through the prior 48h high/low; filter 2: through the 4h level AND 1h move in the top 20%
    i=t-T0; p=ARR[i]
    if p!=p: return None
    if swept(i,d,172800):                                          # 2-day (or longer) high/low; 1-day-only breaks are kept (they continue)
        for n in (30,14,7,5,3):                                    # label with the longest level run through (5 and 7 days always fall in here)
            if swept(i,d,n*86400): return f"{n}-day sweep"
        return "2-day sweep"
    if swept(i,d,14400) and i>=3600 and d*(p-ARR[i-3600])/p*100>=M60_CUT: return "4h sweep + stretched 1h"
    return None
# 2-day high/low (window ending 5 min before) and ATR (mean 15-min high-low over the prior 24 h), precomputed per minute for the near-level filter
_m=len(ARR)//60; _mm=ARR[:_m*60].reshape(_m,60)
_mx=pd.Series(np.nanmax(_mm,1)); _mn=pd.Series(np.nanmin(_mm,1))
HI2=_mx.rolling(2880,min_periods=2000).max().to_numpy(); LO2=_mn.rolling(2880,min_periods=2000).min().to_numpy()
_b=_m//15; _r=np.nanmax(_mm[:_b*15].reshape(_b,900),1)-np.nanmin(_mm[:_b*15].reshape(_b,900),1)
ATR15=pd.Series(_r).rolling(96,min_periods=48).mean().to_numpy()
NEAR_ATR=0.5
NEAR_ON=os.environ.get("NEAR","off")=="on"
FIRST=int(os.environ.get("FIRST","120")); ANCHOR=int(os.environ.get("ANCHOR",str(FIRST)))   # second where the threshold equals MNY_START   # first entry check (s); the threshold falls from MNY_START here to MNY_FLOOR at 900
STEP=int(os.environ.get("STEP","10"))   # seconds between entry checks
SZ_E=int(os.environ.get("SIZE_E","200")); SZ_L=int(os.environ.get("SIZE_L","100")); MAXHELD=int(os.environ.get("MAXHELD","5000"))
MNY_START=float(os.environ.get("MNY_START","0.0015")); MNY_FLOOR=float(os.environ.get("MNY_FLOOR","0.0005"))
LIM=float(os.environ.get("LIM","0.005"))     # >0: enter with a resting bid LIM below the ask (maker fee), valid until the next check
HYB=os.environ.get("HYB","1")=="1"      # with LIM: if the bid has not filled by the next check, buy at market then   # near-level entry filter (part of FILT); NEAR=off for sweeps only
def near_level(t,d):            # True if BTC is within NEAR_ATR x ATR of its 2-day high (d>0) / low (d<0), or beyond it
    i=t-T0; k=(i-300)//60-1; b=i//900-1
    if k<0 or b<0 or k>=len(HI2) or b>=len(ATR15): return False
    L=HI2[k] if d>0 else LO2[k]; a=ATR15[b]; p=ARR[i]
    if L!=L or a!=a or p!=p or a<=0: return False
    return d*(L-p)<NEAR_ATR*a
MAXSPREAD=float(os.environ.get("MAXSPREAD","0.03"))   # her rule: no entry when the spread is wider than 3c
def spread_est(K,t,side):       # from trades: our side's lowest taker price (~ask) + other side's lowest taker price (~1 - bid) - 1, over t-3..t+1
    o="n" if side=="y" else "y"; a1=a2=9
    for u in range(t-3,t+2):
        a=K.get(u)
        if a:
            if a[side][1]>0: a1=min(a1,a[side][0])
            if a[o][1]>0: a2=min(a2,a[o][0])
    return None if a1>=9 or a2>=9 else a1+a2-1
def mfee(p,n): return math.ceil(0.0175*n*p*(1-p)*100-1e-9)/100
DAYS=float(sys.argv[3]); PRE=sys.argv[4]; SIZING=sys.argv[5] if len(sys.argv)>5 else "mixed"   # mixed = 200 before min 6 then 100; equal = 100
FILT=(sys.argv[6] if len(sys.argv)>6 else "on")=="on"   # liquidity-sweep filters (skip market at its first entry)
M60_CUT=0.3879
import os; MAXR=float(os.environ.get("MAXR","0.031"))     # skip windows whose prior 2h high-low range is above this        # top 20% of the 1h move our way at first entries, train period only (results/liq_features.csv)
last_op=max(op for _,op,_,_ in W); start=last_op+900-DAYS*86400 if DAYS>0 else 0
mm=lambda u:f"{int(u)//60}:{int(u)%60:02d}"
LOTS=[];MK=[];SKIPS=[];NEARSKIP=[0];WIDE=[0]
for y,op,path,K in W:
    if op<start: continue
    r=R[op]; K0=KS.get(op)
    if r!=r or r>MAXR or K0 is None: continue
    SG=sig_at(op) if (FVSTOP or FVENT) else float("nan")
    side=None; lots=[]; n=0; lastp=None; PEND=[]; rollblock=False; t=pd.Timestamp(op,unit="s",tz="UTC").tz_convert(PT); day=t.strftime("%a %b %d"); win_pt=t.strftime("%-I:%M %p")
    def close(L,how,u,px,feeamt):
        L.update(exit=how,exit_time=mm(u),exit_px=round(px,4),pnl=round(L["n"]*px-feeamt-L["cost"],2)); LOTS.append(L)
    for u in range(FIRST,900):
        a=K.get(op+u)
        if side and a:
            o="n" if side=="y" else "y"; q=a[side][1]+a[o][1]
            if q>0: lastp=(a[side][2]+(a[o][1]-a[o][2]))/q
            if a[side][1]>0 and a[side][2]/a[side][1]>=TP and lots:
                for L in lots: close(L,f"sold {TP*100:g}c",u,TP,mfee(TP,L["n"]))
                lots=[]
        if PEND:
            L_,s_,exp_,meta_=PEND[0]; o_="n" if s_=="y" else "y"; a_=K.get(op+u)
            if a_ and ((a_[s_][1]>0 and a_[s_][0]<L_-1e-9) or (a_[o_][1]>0 and a_[o_][2]/a_[o_][1]>1-L_+1e-9)):
                PEND.clear(); side=s_; n+=1; sz=meta_["n"]; lots.append(dict(meta_,entry_no=n,entry_time=mm(u),buy=round(L_,4),stop=round(L_*(1-X),4),cost=round(sz*L_+mfee(L_,sz),2),how="limit"))
            elif u>=exp_:
                PEND.clear()
                if HYB:
                    e_=buy_px(K,op+u+1,s_)
                    if e_ is not None and e_<CAP and (side is None or s_==side or not lots) and not jumped(op+u):
                        side=s_; n+=1; sz=meta_["n"]; lots.append(dict(meta_,entry_no=n,entry_time=mm(u+1),buy=round(e_,4),stop=round(e_*(1-X),4),cost=round(sz*e_+fee(e_,sz),2),how="market late"))
        if side and lots and lastp is not None:
            lead=(path[60+u]-K0)/K0*(1 if side=="y" else -1)
            if FVSTOP:
                fy=fair_yes(path,u,K0,SG); fv=None if fy is None else (fy if side=="y" else 1-fy)
                if fv is None: hit=[L for L in lots if lastp<=L["stop"] and lead<LEADSTOP]
                elif FVSTOP==1: hit=[L for L in lots if fv<=L["stop"] and lead<LEADSTOP]
                else: hit=[L for L in lots if (fv<=L["stop"] or lastp<=L["stop"]) and lead<LEADSTOP]
            else: hit=[L for L in lots if lastp<=L["stop"] and lead<LEADSTOP]
            hit=[L for L in hit if L.get("wu",-1)<=u]
            if hit:
                b=sell_fast(K,op+u+1,side)
                if b is not None and CONFIRM!="off":          # re-check when selling (second u+1): skip lots that recovered
                    if CONFIRM in ("bid","both"): hit=[L for L in hit if b<=L["stop"]+1e-9]
                    if CONFIRM in ("btc","both") and (path[min(61+u,len(path)-1)]-K0)/K0*(1 if side=="y" else -1)>=LEADSTOP: hit=[]
                    if not hit: b=None
                if b is not None and GAP>0:
                    gw=[L for L in hit if "wu" not in L and b<L["stop"]-GAP]
                    for L in gw: L["wu"]=u+GAPW; GAPN[0]+=1
                    for L in hit:
                        if "wu" in L and L not in gw and not L.get("wsold"): L["wsold"]=1; GAPN[1]+=1
                    hit=[L for L in hit if L not in gw]
                    if not hit: b=None
                if b is not None:
                    if STOPFRAC>0: b=sell_sized(K,op+u+1,side,sum(L["n"] for L in hit),STOPFRAC)
                    for L in hit: close(L,"stopped",u+1,b,fee(b,L["n"])); lots.remove(L)
        held=sum(L["n"] for L in lots)
        if u%STEP==0 and u<=LAST and held<MAXHELD and not jumped(op+u):
            th=MNY_START+(MNY_FLOOR-MNY_START)*(u-ANCHOR)/(900-ANCHOR); m=(path[60+u]-K0)/K0
            s2="y" if m>=th else ("n" if -m>=th else None)
            if s2 and (side is None or s2==side or not lots):
                if FILT and side is None and n==0:
                    why=skip_market(op+u,1 if s2=="y" else -1)
                    if why: SKIPS.append(dict(ticker=TK.get(op),day=day,window_pt=win_pt,why=why)); break
                if FILT and ROLLFILT and side is not None and s2!=side and not lots:   # a roll: run the sweep filters for the new side
                    if rollblock or skip_market(op+u,1 if s2=="y" else -1):
                        ROLLSKIP[0]+=1
                        if ROLLFILT==2: rollblock=True                                   # 2 = no more rolls in this market
                        continue
                if FILT and NEAR_ON and near_level(op+u,1 if s2=="y" else -1): NEARSKIP[0]+=1; continue
                sp=spread_est(K,op+u+1,s2)
                if sp is not None and sp>MAXSPREAD+1e-9: WIDE[0]+=1; continue
                e=buy_px(K,op+u+1,s2)
                if FVENT>0 and e is not None:
                    fy=fair_yes(path,u,K0,SG)
                    if fy is not None: FVD.append(e-(fy if s2=="y" else 1-fy))
                    if fy is not None and e-(fy if s2=="y" else 1-fy)>FVENT: FVBLK[0]+=1; continue
                if e is not None and e<CAP and LIM>0:
                    if not PEND:
                        sz=min((SZ_E if u<360 else SZ_L) if SIZING=="mixed" else SZ_L,MAXHELD-held)
                        if side and s2!=side and lots: pass
                        else:
                            if side and s2!=side: lastp=None
                            PEND.append((round(e-LIM,4),s2,u+STEP,dict(day=day,window_pt=win_pt,ticker=TK.get(op),side="YES (up)" if s2=="y" else "NO (down)",
                                moneyness_pct=round(abs(m)*100,3),threshold_pct=round(th*100,3),n=sz)))
                    continue
                if e is not None and e<CAP:
                    if side and s2!=side: lastp=None
                    side=s2; n+=1; sz=min((SZ_E if u<360 else SZ_L) if SIZING=="mixed" else SZ_L,MAXHELD-held)
                    lots.append(dict(day=day,window_pt=win_pt,ticker=TK.get(op),side="YES (up)" if s2=="y" else "NO (down)",entry_no=n,
                        entry_time=mm(u+1),moneyness_pct=round(abs(m)*100,3),threshold_pct=round(th*100,3),buy=round(e,4),
                        n=sz,stop=round(e*(1-X),4),cost=round(sz*e+fee(e,sz),2)))
    if not side: continue
    for L in lots:
        w_=y if L["side"].startswith("YES") else not y; close(L,"settled $1" if w_ else "settled $0",900,1.0 if w_ else 0.0,0.0)
    ml=[L for L in LOTS if L["ticker"]==TK.get(op)]
    MK.append(dict(day=day,window_pt=win_pt,ticker=TK.get(op),range2h=round(r*100,3),side=" then ".join(dict.fromkeys(L["side"] for L in sorted(ml,key=lambda z:z["entry_no"]))),strike=K0,result="YES" if y else "NO",entries=len(ml),
        contracts=sum(L["n"] for L in ml),avg_buy=round(sum(L["buy"]*L["n"] for L in ml)/sum(L["n"] for L in ml),4),first_entry=ml[0]["entry_time"] if ml else None,
        stopped=sum(L["exit"]=="stopped" for L in ml),sold995=sum(L["exit"].startswith("sold") for L in ml),settled=sum(L["exit"].startswith("settled") for L in ml),
        pnl=round(sum(L["pnl"] for L in ml),2)))
pd.DataFrame(SKIPS).to_csv(PRE+"_skipped.csv",index=False); print("roll checks blocked by the sweep filters:",ROLLSKIP[0],"| entries blocked near a 2-day level:",NEARSKIP[0],"| blocked by spread > %.0fc:"%(MAXSPREAD*100),WIDE[0]); print("markets skipped by the filters:",len(SKIPS),pd.Series([x["why"] for x in SKIPS]).value_counts().to_dict() if SKIPS else "")
for L in LOTS: L.pop("wu",None); L.pop("wsold",None)
if GAP>0: print(f"gap waits: {GAPN[0]} lots waited, {GAPN[1]} sold after the wait, {GAPN[0]-GAPN[1]} kept")
pd.DataFrame(LOTS).to_csv(PRE+"_entries.csv",index=False); M=pd.DataFrame(MK); M.to_csv(PRE+"_markets.csv",index=False)
print(f"{len(M)} markets, {len(LOTS)} entries, P&L ${M.pnl.sum():+,.0f}" + (f" | entries blocked by fair value: {FVBLK[0]}; buy - fair (c) p10/50/90/99: "+" ".join(f"{np.quantile(FVD,q)*100:+.1f}" for q in (.1,.5,.9,.99)) if FVENT and FVD else ""))
for d,x in M.groupby("day",sort=False): print(f"  {d}: {len(x)} markets, {x.entries.sum()} entries, ${x.pnl.sum():+,.0f}")
