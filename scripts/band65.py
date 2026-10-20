# User's clarified rule: once one side is "validated" (BRTI proxy >= mny past start), buy that side at 65-75c, sell at 95c.
# (run from data/: python3 -P ../scripts/band65.py 67 composite)
# A) taker: buy at the ask whenever the signal holds and the side's taker VWAP is in [lo,hi].
# B) maker: once validated and the side trades above L, rest a bid at L; cancel on timer or when the signal lapses
#    (BTC back inside mny). Fill rules as in limit_sim.py (cons = trade-through, touch = trade at L).
import sys,statistics as st,math
src=open("../scripts/friend_v3.py").read(); exec(src[:src.index('print("Rules:')])
def mfee(p,n): return math.ceil(0.0175*n*p*(1-p)*100-1e-9)/100
def sig(path,ref,s,mny):
    m=(path[60+s]-ref)/ref
    return "y" if m>=mny else ("n" if -m>=mny else None)
def exit95(K,op,S,start,cost,win,mode,N,fe):
    for u in range(start,900):
        b=K.get(op+u)
        if b and b[S][1]>0 and b[S][2]/b[S][1]>=(0.96 if mode=="cons" else 0.95):
            return (0.95*N-cost-fe(0.95,N))/N,"tp"
    return ((N if win else 0)-cost)/N,"settle"
def maker(mny,L,mode,cancel,lapse,N=5,lat=1):
    out=[]
    for y,op,path,K in W:
        ref=sum(path[:60])/60; posted=None; S=None
        for s in range(120,870):
            if posted is None:
                if s>720: break
                side=sig(path,ref,s,mny)
                if not side: continue
                e=buy_px(K,op+s+lat,side)
                if e is None or e<=L: continue
                posted=s+lat; S=side; continue
            if s-posted>cancel: break
            if lapse and sig(path,ref,s-lat,mny)!=S: break     # we see the lapse 1s late
            a=K.get(op+s)
            if not a: continue
            o="n" if S=="y" else "y"
            lo=a[S][0] if a[S][1]>0 else 9; ovw=a[o][2]/a[o][1] if a[o][1]>0 else -1
            hit=(lo<L-1e-9 or ovw>1-L+1e-9) if mode=="cons" else (lo<=L+1e-9 or ovw>=1-L-1e-9)
            if not hit: continue
            win=y if S=="y" else not y; cost=L*N+mfee(L,N)
            p,k=exit95(K,op,S,s+1,cost,win,mode,N,mfee); out.append((p,win,k,s)); break
    return out
def taker(mny,lo,hi,mode,N=5,lat=1):
    out=[]
    for y,op,path,K in W:
        ref=sum(path[:60])/60
        for s in range(120,870):
            side=sig(path,ref,s,mny)
            if not side: continue
            e=buy_px(K,op+s+lat,side)
            if e is None or not (lo<=e<=hi): continue
            win=y if side=="y" else not y; cost=e*N+fee(e,N)
            p,k=exit95(K,op,side,s+lat+3,cost,win,mode,N,fee); out.append((p,win,k,s,e)); break
    return out
def line(tag,r):
    v=[x[0] for x in r]; h=len(v)//2; se=1.96*st.pstdev(v)/math.sqrt(len(v))
    mins=sorted(x[3]/60 for x in r)
    print(f"{tag} | {len(v)/days:5.1f}/day  win {sum(x[1] for x in r)/len(r)*100:5.1f}%  tp {sum(x[2]=='tp' for x in r)/len(r)*100:3.0f}%"
          f"  median entry min {mins[len(mins)//2]:4.1f} | P&L/ct {st.mean(v)*100:+5.2f}c ±{se*100:4.2f} | halves {st.mean(v[:h])*100:+.2f}/{st.mean(v[h:])*100:+.2f}")
print("A) TAKER: signal holds + ask in band -> buy; resting 95c sell; else settle. 5 contracts, taker fees.")
for mny in (0.0010,0.0015,0.0020):
    for lo,hi in ((0.65,0.75),(0.65,0.70),(0.70,0.75)):
        for mode in ("cons","opt"):
            r=taker(mny,lo,hi,mode)
            if len(r)>=20: line(f"  mny {mny*100:.2f}% buy {lo:.2f}-{hi:.2f} 95c-{mode:5s}",r)
print("\nB) MAKER: rest bid at L once validated side trades above L; cancel on timer or when BTC falls back inside mny. Maker fees.")
for mny in (0.0010,0.0015,0.0020):
    for L in (0.65,0.70,0.75):
        for cancel,lapse in ((60,False),(180,False),(600,True),(180,True)):
            for mode in ("cons","touch"):
                r=maker(mny,L,mode,cancel,lapse)
                if len(r)>=20: line(f"  mny {mny*100:.2f}% bid {L:.2f} cancel {cancel:3d}s{'+lapse' if lapse else '      '} {mode:5s}",r)
