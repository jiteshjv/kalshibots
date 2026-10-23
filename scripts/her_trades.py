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
FALLRISK=os.environ.get("FALLRISK","0")=="1"   # 1 (2026-10-22): the market buy after an unfilled bid re-checks risk <= RISKMAX (and the side) first
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
# Her risk check (2026-10-21, her PDF "How the Bitcoin Trader Measures Risk"): SD of BTC's $ change between samples RSAMP s apart, over a
# short (RSHORT s) and a long (RLONG s) lookback ending now; the higher one, scaled to per minute. Time left minus RADJ s (the final 60 s
# average). Z = distance to the strike / (SD per min x sqrt(minutes left)); risk = 1 - Phi(Z). RISKMAX>0: enter only if risk <= RISKMAX.
# MNYSHAPE=fib: MNY_START x 0.618 at each Fibonacci minute passed (3, 5, 8, 13), never below MNY_FLOOR.
# MNYSHAPE=sqrt: the moneyness line falls with sqrt(time left) instead of linearly (MNY_START at ANCHOR -> MNY_FLOOR at 900).
RISKMAX=float(os.environ.get("RISKMAX","0")); RSAMP=int(os.environ.get("RSAMP","5")); RSHORT=int(os.environ.get("RSHORT","300"))
RLONG=int(os.environ.get("RLONG","900")); RADJ=int(os.environ.get("RADJ","40")); MNYSHAPE=os.environ.get("MNYSHAPE","linear"); RISKBLK=[0]
def risk_at(t,u,K0):
    p=ARR[t-T0]; sds=[]
    for w in (RSHORT,RLONG):
        seg=ARR[t-w-T0:t-T0+1:RSAMP]
        with np.errstate(all="ignore"): d=np.diff(seg)
        d=d[np.isfinite(d)]
        if len(d)>=5: sds.append(float(np.std(d))*math.sqrt(60/RSAMP))
    if not sds or max(sds)<=0: return None
    left=max(900-u-RADJ,1)/60
    z=abs(p-K0)/(max(sds)*math.sqrt(left))
    return 0.5*math.erfc(z/math.sqrt(2))
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
SWZONE=float(os.environ.get("SWZONE","0"))   # >0: skip the market if BTC is within this fraction of its SWZN-day high or low (either one, any side)
SWZN=int(os.environ.get("SWZN","5"))         #     (window includes now, so being at or beyond the level counts as inside the zone)
SWZLAG=int(os.environ.get("SWZLAG","0"))     # >0: zone around the level as it stood SWZLAG s ago (see zone_or_cool)
SWCOOL=int(os.environ.get("SWCOOL","0"))     # >0: skip the market if a 3- to 14-day sweep (either direction) happened in the last SWCOOL markets
def zone_or_cool(t):
    i=t-T0
    if i<0 or i>=len(ARR) or ARR[i]!=ARR[i]: return None
    p=ARR[i]
    if SWZONE>0 and i>SWZN*86400:
        if SWZLAG>0 and i<=SWZN*86400+SWZLAG: return _cool(i)
        if SWZLAG>0:      # fixed level (the n-day high/low as of SWZLAG s ago): skip only inside +-SWZONE of it; once BTC is
            w=ARR[i-SWZN*86400-SWZLAG:i-SWZLAG]; hi,lo=np.nanmax(w),np.nanmin(w)     # through and clear of the band, it qualifies again
            if abs(hi-p)/p<SWZONE or abs(p-lo)/p<SWZONE: return f"in {SWZN}-day sweep zone"
            return None if SWCOOL<=0 else _cool(i)
        w=ARR[i-SWZN*86400:i+1]; hi,lo=np.nanmax(w),np.nanmin(w)
        if (hi-p)/p<SWZONE or (p-lo)/p<SWZONE: return f"near {SWZN}-day level"
    return _cool(i)
def _cool(i):
    if SWCOOL>0:
        W=SWCOOL*900+300
        if i>W+3*86400:
            rec=ARR[i-W:i+1]; pr3=ARR[i-W-3*86400:i-W]; pr30=ARR[max(0,i-W-30*86400):i-W]
            for d,f in ((1,np.nanmax),(-1,np.nanmin)):
                if d*(f(rec)-f(pr3))>0 and not d*(f(rec)-f(pr30))>0: return f"sweep cool-down {SWCOOL}"
    return None
def skip_market(t,d):
    if SWZONE>0 or SWCOOL>0:
        z=zone_or_cool(t)
        if z: return z
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
TICK=os.environ.get("TICK","1")=="1"   # bids rounded down to Kalshi price steps: whole cents between 10c and 90c
def tickf(p):
    import math
    if not TICK: return round(p,4)
    st=0.01 if 0.10<p<0.90 else 0.001
    return round(math.floor(p/st+1e-6)*st,4)
TOUCH=2e-9 if os.environ.get("TOUCH","0")=="1" else 0.0   # 1: a print AT the bid fills it (front of the queue; optimistic)
LIM=float(os.environ.get("LIM","0.005"))     # >0: enter with a resting bid LIM below the ask (maker fee), valid until the next check
WAIT=int(os.environ.get("WAIT","0"))   # >0: an unfilled entry bid waits this many s before the market buy (default: until the next check, STEP)
MAXGAP=float(os.environ.get("MAXGAP","0"))   # >0: no bid in the whole-cent band (bid price 10-90c): buy at market at once; bids only above 90c / below 10c
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

# HERBOOK=1: her recorded Kalshi order book (data_her/reduced, Apr-Oct) for entry asks, the spread check, bid fills (queue
# position: contracts already at our price fill first; nobody ahead cancels), market buys and stop sells (walk the real levels).
# Markets without her data fall back to the trade-based model. Stop triggers, 99.7c sells and settlement are unchanged.
HERBOOK=os.environ.get("HERBOOK","0")=="1"; HBN=[0,0]
HBP=set(os.environ.get("HBPARTS","ask,fill,mkt,stop,spread").split(","))   # which parts use her book
if HERBOOK:
    import glob as _g
    _HBI={os.path.basename(x)[:-4]:x for x in _g.glob(os.path.join(os.path.dirname(os.path.abspath(sys.argv[0])),"..","data_her","reduced","*","KXBTC*.npz"))}
def hb_load(tk):
    if not HERBOOK or tk not in _HBI: HBN[1]+=1; return None
    z=np.load(_HBI[tk]); HBN[0]+=1; return {k:z[k] for k in ("t","yp","yq","np","nq","tt","tp","ts","tq")}
def _hb_row(H,T):
    i=np.searchsorted(H["t"],T,side="right")-1; return i if i>=0 else None
def _sides(H,side): return (H["yp"],H["yq"],H["np"],H["nq"]) if side=="y" else (H["np"],H["nq"],H["yp"],H["yq"])
def hb_walk(H,t_s,side,n,buy=True):     # VWAP for n contracts: buy = take the asks (1 - other side's bids), sell = hit our side's bids
    i=_hb_row(H,int(t_s*1000))
    if i is None: return None
    op_,oq,pp,pq=_sides(H,side)
    lv=[(1000-int(a),float(b)) for a,b in zip(pp[i],pq[i]) if a>0] if buy else [(int(a),float(b)) for a,b in zip(op_[i],oq[i]) if a>0]
    if not lv: return None
    lv.sort(reverse=not buy); left=n; cost=0.0
    for a,b in lv:
        u=min(left,b); cost+=u*a; left-=u
        if left<=0: break
    if left>0: cost+=left*lv[-1][0]        # deeper than the top 5: rest at the 5th level
    return cost/n/1000
STOPALL=os.environ.get("STOPALL","0")=="1"
STOPNEAR=float(os.environ.get("STOPNEAR","0"))
STOPLAT=int(os.environ.get("STOPLAT","0"))   # HERBOOK: >0 = stops sell STOPLAT ms after the 100 ms step where the price first hit the
def hb_trigger_ms(H,sec0,side,stop):           # stop inside the trigger second (trade at/below it or our best bid at/below it), not at the next second
    T0_,T1_=sec0*1000,sec0*1000+1000; s_=int(round(stop*1000)); best=None
    tt=H["tt"]; lo,hi=np.searchsorted(tt,T0_+1),np.searchsorted(tt,T1_+1)
    for k in range(lo,hi):
        px=int(H["tp"][k]) if side=="y" else 1000-int(H["tp"][k])
        if px<=s_: best=int(tt[k]); break
    t=H["t"]; P=H["yp"] if side=="y" else H["np"]; lo,hi=np.searchsorted(t,T0_+1),np.searchsorted(t,T1_+1)
    for k in range(lo,hi):
        if 0<P[k][0]<=s_:
            best=int(t[k]) if best is None else min(best,int(t[k])); break
    return best if best is not None else T0_+(1000 if FVHONEST else 0)   # FVHONEST: no print/bid hit -> a fair-value trigger, known only at the second's end
# FVHONEST=1: a stop set off by fair value alone sells after the second (its 1 s BTC price uses that second's trades), not 100 ms into it.
# FVFAST=1 (with HERBOOK, STOPLAT): also check the fair-value stop every 200 ms on her 5 Hz BRTI (from Jun 7), selling 100 ms later.
FVHONEST=os.environ.get("FVHONEST","0")=="1"; FVFAST=os.environ.get("FVFAST","0")=="1"; _B5={}; FVFN=[0]
def b5_load(tk):
    p=_HBI.get(tk) if HERBOOK else None
    if p is None: return None
    d=os.path.dirname(p)
    if d not in _B5:
        _B5.clear(); nd=os.path.join(os.path.dirname(d),(pd.Timestamp(os.path.basename(d))+pd.Timedelta(days=1)).strftime("%Y-%m-%d")); zs=[]
        for dd in (d,nd):
            f=os.path.join(dd,"prices.npz")
            if os.path.exists(f):
                z=np.load(f)
                if "ft" in z.files: zs.append((z["ft"],z["fv"]))
        _B5[d]=(np.concatenate([a for a,_ in zs]),np.concatenate([b for _,b in zs])) if zs else None
    return _B5[d]
def fair_px(p,path,u,K0,sg):        # fair_yes with the current price p instead of the 1 s path value
    left=899-u
    if sg!=sg or sg<=0: return None
    if left>=60: mu=p; var=(left-59)+_W60
    else: mu=(sum(path[60+840:60+u])+p*(left+1))/60; var=sum((k/60)**2 for k in range(1,left+1))
    sd=sg*p*math.sqrt(max(var,1e-9))
    return 0.5*(1+math.erf((mu-K0)/(sd*math.sqrt(2))))
def hb_ask(H,t_s,side):
    i=_hb_row(H,int(t_s*1000))
    if i is None: return None
    pp=(H["np"] if side=="y" else H["yp"])[i][0]
    return None if pp<=0 else (1000-int(pp))/1000
def hb_spread(H,t_s,side):
    i=_hb_row(H,int(t_s*1000))
    if i is None: return None
    a=(H["np"] if side=="y" else H["yp"])[i][0]; b=(H["yp"] if side=="y" else H["np"])[i][0]
    return None if a<=0 or b<=0 else (1000-int(a)-int(b))/1000
def hb_fill(H,T,side,bid,n,wait_s):    # ms when the whole bid has filled (queue ahead first), or None within the wait
    i=_hb_row(H,T)
    if i is None: return None
    b=int(round(bid*1000)); op_,oq,_,_=_sides(H,side)
    Q=float(oq[i][op_[i]==b].sum()) if b<=int(op_[i][0]) else 0.0
    tt,tp,ts_,tq=H["tt"],H["tp"],H["ts"],H["tq"]; lo=np.searchsorted(tt,T+101); hi=np.searchsorted(tt,T+int(wait_s*1000)+1)
    got=0.0
    for k in range(lo,hi):
        if side=="y":
            if ts_[k]!=0: continue
            px=int(tp[k])
        else:
            if ts_[k]!=1: continue
            px=1000-int(tp[k])
        if px<b: return int(tt[k])
        if px==b:
            v=float(tq[k]); use=min(Q,v); Q-=use; got+=v-use
            if got>=n: return int(tt[k])
    return None
# LADDER="o,w,d,k" (HERBOOK + STOPLAT, 2026-10-22): her way of stopping: instead of a market sell, a limit sell o c above our
# best bid (at most the best ask), wait w s, lower it d c, at most k prices, then sell what is left at market. Fills: taker buys
# of our side at or above our price, after the contracts already offered there (queue). Unfilled at the close: settles.
LADDER=os.environ.get("LADDER",""); LADN=[0,0,0,0]   # [stop events, contracts, filled by the limits, settled]
def _tk(a): return int(round(a/10))*10 if 100<=a<=900 else int(a)
def hb_ladder(H,T,side,n,end_ms,win):
    o,w,d,k=[float(x) for x in LADDER.split(",")]
    ob,_,pp,pq=_sides(H,side); tt,tp,ts_,tq=H["tt"],H["tp"],H["ts"],H["tq"]
    i=_hb_row(H,T)
    if i is None or ob[i][0]<=0: return None
    LADN[0]+=1; LADN[1]+=n
    a=_tk(int(ob[i][0])+o*10)
    if pp[i][0]>0: a=min(a,1000-int(pp[i][0]))
    left=float(n); got=0.0; fe=0.0
    for step in range(int(k)):
        i=_hb_row(H,T)
        if step>0 and i is not None and 0<int(ob[i][0])>=a: break        # our price reached the bid: sell the rest at market
        Q=float(pq[i][pp[i]==1000-a].sum()) if i is not None else 0.0
        T1=min(T+int(w*1000),end_ms); lo,hi=np.searchsorted(tt,T+1),np.searchsorted(tt,T1+1); f=0.0
        for j in range(lo,hi):
            if side=="y":
                if ts_[j]!=1: continue
                px=int(tp[j])
            else:
                if ts_[j]!=0: continue
                px=1000-int(tp[j])
            if px>a: f=left; break
            if px==a:
                v=float(tq[j]); use=min(Q,v); Q-=use; f=min(left,f+v-use)
                if f>=left: break
        if f>0: got+=f*a/1000; fe+=0.0175*f*(a/1000)*(1-a/1000); left-=f; LADN[2]+=f
        T=T1
        if left<=1e-9: return got,fe
        if T>=end_ms: LADN[3]+=left; return got+left*(1.0 if win else 0.0),fe
        a=_tk(a-d*10)
        if a<=0: break
    b=hb_walk(H,T/1000,side,left,buy=False)
    if b is None: b=a/1000
    return got+left*b,fe+fee(b,left)
def mfee(p,n): return math.ceil(0.0175*n*p*(1-p)*100-1e-9)/100
DAYS=float(sys.argv[3]); PRE=sys.argv[4]; SIZING=sys.argv[5] if len(sys.argv)>5 else "mixed"   # mixed = 200 before min 6 then 100; equal = 100
FILT=(sys.argv[6] if len(sys.argv)>6 else "on")=="on"   # liquidity-sweep filters (skip market at its first entry)
M60_CUT=0.3879
import os; MAXR=float(os.environ.get("MAXR","0.031"))     # skip windows whose prior 2h high-low range is above this        # top 20% of the 1h move our way at first entries, train period only (results/liq_features.csv)
last_op=max(op for _,op,_,_ in W); start=last_op+900-DAYS*86400 if DAYS>0 else 0
mm=lambda u:f"{int(u)//60}:{int(u)%60:02d}"
LOTS=[];MK=[];SKIPS=[];NEARSKIP=[0];WIDE=[0]
BIDLOG=os.environ.get("BIDLOG","0")=="1"; BIDS=[]   # BIDLOG=1: every entry bid (time, side, price, size, outcome) -> <prefix>_bids.csv
for y,op,path,K in W:
    if op<start: continue
    r=R[op]; K0=KS.get(op)
    if r!=r or r>MAXR or K0 is None: continue
    SG=sig_at(op) if (FVSTOP or FVENT) else float("nan")
    HB=hb_load(TK.get(op)) if HERBOOK else None
    B5=b5_load(TK.get(op)) if (FVFAST and HB is not None) else None
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
            if ((meta_.get("_fms") is not None and meta_["_fms"]<(op+u+1)*1000) if HB is not None and "fill" in HBP else (a_ and ((a_[s_][1]>0 and a_[s_][0]<L_-1e-9+TOUCH) or (a_[o_][1]>0 and a_[o_][2]/a_[o_][1]>1-L_+1e-9-TOUCH)))):
                PEND.clear(); side=s_; n+=1; sz=meta_["n"]; lots.append(dict(meta_,entry_no=n,entry_time=mm(u),buy=round(L_,4),stop=round(L_*(1-X),4),cost=round(sz*L_+mfee(L_,sz),2),how="limit"))
                if BIDLOG: BIDS[-1].update(result="filled",done_s=u,entry_no=n)
            elif u>=exp_:
                PEND.clear()
                if BIDLOG: BIDS[-1].update(result="expired",done_s=u)
                if HYB:
                    e_=hb_walk(HB,op+u+1,s_,meta_["n"]) if HB is not None and "mkt" in HBP else buy_px(K,op+u+1,s_)
                    if e_ is not None and e_<CAP and (side is None or s_==side or not lots) and not jumped(op+u) and not (FALLRISK and RISKMAX>0 and ((lambda r:r is None or r>RISKMAX)(risk_at(op+u,u,K0)) or ((path[60+u]-K0)*(1 if s_=="y" else -1)<0))):
                        side=s_; n+=1; sz=meta_["n"]; lots.append(dict(meta_,entry_no=n,entry_time=mm(u+1),buy=round(e_,4),stop=round(e_*(1-X),4),cost=round(sz*e_+fee(e_,sz),2),how="market late"))
                        if BIDLOG: BIDS[-1].update(market_px=round(e_,4),entry_no=n)
        if side and lots and lastp is not None:
            lead=(path[60+u]-K0)/K0*(1 if side=="y" else -1)
            if FVSTOP:
                fy=fair_yes(path,u,K0,SG); fv=None if fy is None else (fy if side=="y" else 1-fy)
                if fv is None: hit=[L for L in lots if lastp<=L["stop"] and lead<LEADSTOP]
                elif FVSTOP==1: hit=[L for L in lots if fv<=L["stop"] and lead<LEADSTOP]
                else: hit=[L for L in lots if (fv<=L["stop"] or lastp<=L["stop"]) and lead<LEADSTOP]
            else: hit=[L for L in lots if lastp<=L["stop"] and lead<LEADSTOP]
            hit=[L for L in hit if L.get("wu",-1)<=u]
            _gf=None
            if B5 is not None and STOPLAT>0 and SG==SG:
                _ft,_fv=B5; _d=1 if side=="y" else -1
                for _g in range((op+u)*1000,(op+u)*1000+1000,200):
                    _i=np.searchsorted(_ft,_g,side="right")-1
                    if _i<0 or _g-_ft[_i]>1000: continue
                    _p=float(_fv[_i]); _fy=fair_px(_p,path,u,K0,SG)
                    if _fy is None: continue
                    _f=_fy if side=="y" else 1-_fy
                    _h=[L for L in lots if _f<=L["stop"] and (_p-K0)/K0*_d<LEADSTOP and L.get("wu",-1)<=u]
                    if _h:
                        _gf=_g; hit=hit+[L for L in _h if L not in hit]; break
            if hit:
                if HB is not None and "stop" in HBP and STOPLAT>0:
                    _tm=hb_trigger_ms(HB,op+u,side,max(L["stop"] for L in hit))
                    if _gf is not None and _gf<_tm: _tm=_gf; FVFN[0]+=1
                    b=hb_walk(HB,(_tm+STOPLAT)/1000,side,sum(L["n"] for L in hit),buy=False)
                else: b=hb_walk(HB,op+u+1,side,sum(L["n"] for L in hit),buy=False) if HB is not None and "stop" in HBP else sell_fast(K,op+u+1,side)
                if b is not None and CONFIRM!="off":          # re-check when selling (second u+1): skip lots that recovered
                    if CONFIRM in ("bid","both"): hit=[L for L in hit if b<=L["stop"]+1e-9]
                    if CONFIRM in ("btc","both") and (path[min(61+u,len(path)-1)]-K0)/K0*(1 if side=="y" else -1)>=LEADSTOP: hit=[]
                    if not hit: b=None
                if b is not None and hit and STOPALL and len(hit)<len(lots):   # STOPALL=1: the first stop sells every open lot
                    hit=list(lots); b=hb_walk(HB,(_tm+STOPLAT)/1000 if (HB is not None and "stop" in HBP and STOPLAT>0) else op+u+1,side,sum(L["n"] for L in hit),buy=False) if HB is not None and "stop" in HBP else b
                if b is not None and hit and STOPNEAR>0:   # also sell lots whose own stop is within STOPNEAR of this sale price
                    more=[L for L in lots if L not in hit and L["stop"]>=b-STOPNEAR]
                    if more:
                        hit=hit+more; b=hb_walk(HB,(_tm+STOPLAT)/1000 if (HB is not None and "stop" in HBP and STOPLAT>0) else op+u+1,side,sum(L["n"] for L in hit),buy=False) if HB is not None and "stop" in HBP else b
                if b is not None and GAP>0:
                    gw=[L for L in hit if "wu" not in L and b<L["stop"]-GAP]
                    for L in gw: L["wu"]=u+GAPW; GAPN[0]+=1
                    for L in hit:
                        if "wu" in L and L not in gw and not L.get("wsold"): L["wsold"]=1; GAPN[1]+=1
                    hit=[L for L in hit if L not in gw]
                    if not hit: b=None
                if b is not None and LADDER and HB is not None and "stop" in HBP and STOPLAT>0:
                    nn=sum(L["n"] for L in hit); r_=hb_ladder(HB,_tm+STOPLAT,side,nn,(op+900)*1000,y if side=="y" else not y)
                    if r_ is not None:
                        for L in hit: close(L,"stopped",u+1,r_[0]/nn,r_[1]*L["n"]/nn); lots.remove(L)
                        b=None
                if b is not None:
                    if STOPFRAC>0: b=sell_sized(K,op+u+1,side,sum(L["n"] for L in hit),STOPFRAC)
                    for L in hit: close(L,"stopped",u+1,b,fee(b,L["n"])); lots.remove(L)
        held=sum(L["n"] for L in lots)
        if u%STEP==0 and u<=LAST and held<MAXHELD and not jumped(op+u):
            th=MNY_START+(MNY_FLOOR-MNY_START)*(u-ANCHOR)/(900-ANCHOR) if MNYSHAPE not in ("sqrt","fib") else (MNY_FLOOR+(MNY_START-MNY_FLOOR)*math.sqrt(max(900-u,0)/(900-ANCHOR)) if MNYSHAPE=="sqrt" else max(MNY_FLOOR,MNY_START*0.618**sum(u>=60*k for k in (3,5,8,13)))); m=(path[60+u]-K0)/K0
            s2="y" if m>=th else ("n" if -m>=th else None)
            if s2 and RISKMAX>0:
                rk=risk_at(op+u,u,K0)
                if rk is None or rk>RISKMAX: RISKBLK[0]+=1; s2=None
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
                sp=hb_spread(HB,op+u+1,s2) if HB is not None and "spread" in HBP else spread_est(K,op+u+1,s2)
                if sp is not None and sp>MAXSPREAD+1e-9: WIDE[0]+=1; continue
                e=hb_ask(HB,op+u+1,s2) if HB is not None and "ask" in HBP else buy_px(K,op+u+1,s2)
                if FVENT>0 and e is not None:
                    fy=fair_yes(path,u,K0,SG)
                    if fy is not None: FVD.append(e-(fy if s2=="y" else 1-fy))
                    if fy is not None and e-(fy if s2=="y" else 1-fy)>FVENT: FVBLK[0]+=1; continue
                if e is not None and e<CAP and LIM>0 and not (MAXGAP>0 and 0.10<e-LIM<0.90):
                    if not PEND:
                        sz=min((SZ_E if u<360 else SZ_L) if SIZING=="mixed" else SZ_L,MAXHELD-held)
                        if side and s2!=side and lots: pass
                        else:
                            if side and s2!=side: lastp=None
                            if BIDLOG: BIDS.append(dict(ticker=TK.get(op),open_ts=op,placed_s=u,side=s2,bid=round(tickf(e-LIM),4),ask=round(e,4),n=sz,result="open"))
                            PEND.append((tickf(e-LIM),s2,u+(WAIT or STEP),dict(day=day,window_pt=win_pt,ticker=TK.get(op),side="YES (up)" if s2=="y" else "NO (down)",
                                moneyness_pct=round(abs(m)*100,3),threshold_pct=round(th*100,3),n=sz)))
                            if HB is not None and "fill" in HBP: PEND[-1][3]["_fms"]=hb_fill(HB,(op+u+1)*1000,s2,tickf(e-LIM),sz,WAIT or STEP)
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
pd.DataFrame(SKIPS).to_csv(PRE+"_skipped.csv",index=False)
if BIDLOG: pd.DataFrame(BIDS).to_csv(PRE+"_bids.csv",index=False)
print("roll checks blocked by the sweep filters:",ROLLSKIP[0],"| entries blocked near a 2-day level:",NEARSKIP[0],"| blocked by spread > %.0fc:"%(MAXSPREAD*100),WIDE[0]); print("markets skipped by the filters:",len(SKIPS),pd.Series([x["why"] for x in SKIPS]).value_counts().to_dict() if SKIPS else "")
for L in LOTS: L.pop("wu",None); L.pop("wsold",None); L.pop("_fms",None)
if LADDER: print(f"ladder: {LADN[0]} stop events, {LADN[1]:.0f} contracts, {LADN[2]:.0f} filled by limits, {LADN[3]:.0f} settled")
if HERBOOK: print(f"her book: {HBN[0]} markets, trade model for {HBN[1]}"+(f"; 200 ms fair-value stop first in {FVFN[0]} stop events" if FVFAST else ""))
if GAP>0: print(f"gap waits: {GAPN[0]} lots waited, {GAPN[1]} sold after the wait, {GAPN[0]-GAPN[1]} kept")
pd.DataFrame(LOTS).to_csv(PRE+"_entries.csv",index=False); M=pd.DataFrame(MK); M.to_csv(PRE+"_markets.csv",index=False)
print(f"{len(M)} markets, {len(LOTS)} entries, P&L ${M.pnl.sum():+,.0f}" + (f" | entries blocked by fair value: {FVBLK[0]}; buy - fair (c) p10/50/90/99: "+" ".join(f"{np.quantile(FVD,q)*100:+.1f}" for q in (.1,.5,.9,.99)) if FVENT and FVD else ""))
for d,x in M.groupby("day",sort=False): print(f"  {d}: {len(x)} markets, {x.entries.sum()} entries, ${x.pnl.sum():+,.0f}")
