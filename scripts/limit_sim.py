# Resting-bid entry for the friend-style strategy (run from data/: python3 -P ../scripts/limit_sim.py 67 composite).
# Signal: after min 2, BRTI proxy >= mny past the window's start price. Once the leading side trades ABOVE the limit L,
# rest a bid at L on that side until filled or cancelled. Tape per second per side: [min taker price, qty, notional].
# Fill (cons): side traded strictly below L, or opposite-side takers' VWAP > 1-L (they sold our side below L).
# Fill (touch): same with <= / >= (assumes front of queue). Then rest a 95c sell as in friend_v3; else hold to settlement.
import sys,statistics as st,math
src=open("../scripts/friend_v3.py").read(); exec(src[:src.index('print("Rules:')])
def mfee(p,n): return math.ceil(0.0175*n*p*(1-p)*100-1e-9)/100     # Kalshi maker fee, per order rounded up
def side_low(a,S,mode):          # did side S trade at/through L this second?
    o="n" if S=="y" else "y"
    lo=a[S][0] if a[S][1]>0 else 9
    ovw=a[o][2]/a[o][1] if a[o][1]>0 else -1
    return lo,ovw
def run(mny,L,mode,cancel_after,last_post=720,N=5,lat=1):
    out=[]
    for y,op,path,K in W:
        ref=sum(path[:60])/60; posted=None; S=None
        for s in range(120,870):
            if posted is None:
                if s>last_post: break
                m=(path[60+s]-ref)/ref
                side="y" if m>=mny else ("n" if -m>=mny else None)
                if not side: continue
                e=buy_px(K,op+s+lat,side)
                if e is None or e<=L: continue           # wait until the leading side is priced above L
                posted=s+lat; S=side; continue
            if s-posted>cancel_after: break
            a=K.get(op+s)
            if not a: continue
            lo,ovw=side_low(a,S,mode)
            hit=(lo<L-1e-9 or ovw>1-L+1e-9) if mode=="cons" else (lo<=L+1e-9 or ovw>=1-L-1e-9)
            if not hit: continue
            win=y if S=="y" else not y; cost=L*N+mfee(L,N); res=None
            for u in range(s+1,900):
                b=K.get(op+u)
                if b and b[S][1]>0 and b[S][2]/b[S][1]>=(0.96 if mode=="cons" else 0.95):
                    res=0.95*N-cost-mfee(0.95,N); kind="tp"; break
            if res is None: res=(N if win else 0)-cost; kind="settle"
            out.append(dict(pnl=res/N,win=win,kind=kind,s=s,posted=posted)); break
    return out
def posted_count(mny,L,last_post=720,lat=1):
    n=0; wins=0
    for y,op,path,K in W:
        ref=sum(path[:60])/60
        for s in range(120,min(870,last_post+1)):
            m=(path[60+s]-ref)/ref
            side="y" if m>=mny else ("n" if -m>=mny else None)
            if not side: continue
            e=buy_px(K,op+s+lat,side)
            if e is None or e<=L: continue
            n+=1; wins+=(y if side=="y" else not y); break
    return n,wins
print("Resting bid at L once the leading side trades above L; 5 contracts, maker fees, resting 95c sell, else settle.")
print("cons = needs trade-through at L (back of queue) and 96c+ for the 95c exit; touch = trade at L is enough.\n")
print(f"{'mny':>5s} {'L':>4s} {'cancel':>6s} {'fill':5s} | posted/d fill%  win% filled  win% all-posted | P&L/ct ±95%    tp% | 1st/2nd half")
for mny in (0.0015,0.0020):
  for L in (0.70,0.72,0.75,0.78):
    npost,wpost=posted_count(mny,L)
    for cancel in (60,180,600):
      for mode in ("cons","touch"):
        r=run(mny,L,mode,cancel)
        if len(r)<20: continue
        v=[x["pnl"] for x in r]; h=len(v)//2; se=1.96*st.pstdev(v)/math.sqrt(len(v))
        print(f"{mny*100:4.2f}% {L:.2f} {cancel:5d}s {mode:5s} | {npost/days:7.1f} {len(r)/npost*100:5.1f}%  {sum(x['win'] for x in r)/len(r)*100:5.1f}%       {wpost/npost*100:5.1f}%       | {st.mean(v)*100:+5.2f}c ±{se*100:4.2f}  {sum(x['kind']=='tp' for x in r)/len(r)*100:3.0f}% | {st.mean(v[:h])*100:+.2f}/{st.mean(v[h:])*100:+.2f}")
