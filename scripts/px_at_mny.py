# Kalshi price of the leading side the first time BTC reaches each moneyness (after min 2), by minute of the window.
# (run from data/: python3 -P ../scripts/px_at_mny.py 67 composite)
import numpy as np
src=open("../scripts/friend_v3.py").read(); exec(src[:src.index('print("Rules:')])
for mny in (0.0010,0.0015,0.0020,0.0025):
    rows=[]
    for y,op,path,K in W:
        ref=sum(path[:60])/60
        for s in range(120,870):
            m=(path[60+s]-ref)/ref
            if abs(m)>=mny:
                e=buy_px(K,op+s+1,"y" if m>0 else "n")
                if e is not None: rows.append((s,e))
                break
    a=np.array(rows); px=a[:,1]
    q=np.percentile(px,[10,25,50,75,90])*100
    print(f"\n>= {mny*100:.2f}% past start: reached in {len(a)/len(W)*100:.0f}% of windows | price paid: 10%-ile {q[0]:.0f}c  25% {q[1]:.0f}c  median {q[2]:.0f}c  75% {q[3]:.0f}c  90% {q[4]:.0f}c")
    print(f"   share <65c {np.mean(px<0.65)*100:3.0f}% | 65-75c {np.mean((px>=0.65)&(px<=0.75))*100:3.0f}% | 76-85c {np.mean((px>0.75)&(px<=0.85))*100:3.0f}% | 86-92c {np.mean((px>0.85)&(px<=0.92))*100:3.0f}% | >92c {np.mean(px>0.92)*100:3.0f}%")
    for lo,hi in ((2,5),(5,8),(8,11),(11,14.5)):
        g=px[(a[:,0]>=lo*60)&(a[:,0]<hi*60)]
        if len(g): print(f"   first reached in min {lo:>4}-{hi:<4}: {len(g)/len(W)*100:3.0f}% of windows, median price {np.median(g)*100:.0f}c, >85c {np.mean(g>0.85)*100:3.0f}%")
