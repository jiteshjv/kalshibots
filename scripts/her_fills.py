# Replay the backtest's entry bids against her real order book (data_her/reduced) with queue position.
#   python3 -P scripts/her_fills.py <prefix> [wait_s=5]     reads results/{jh,v9}_<prefix>_*_bids.csv -> results/<prefix>_fillsim.csv
# Per bid: queue ahead = contracts already resting at our price on our side when we bid. Fills come only from takers selling into
# our side: at our price after the queue ahead is used up, or below our price (our level traded through -> all filled).
#   pess: nobody ahead of us cancels.  opt: when the level shrinks without trades, the cancels were ahead of us.
# Fallback market buy for the rest: walk the real ask levels (top 5) for the size left.
import glob,os,sys,json,numpy as np,pandas as pd
PRE=sys.argv[1]; WAIT=float(sys.argv[2]) if len(sys.argv)>2 else 5.0
fs=sorted(glob.glob(f"results/jh_{PRE}_*_bids.csv")+glob.glob(f"results/v9_{PRE}_*_bids.csv"))
B=pd.concat([pd.read_csv(f) for f in fs]).drop_duplicates(["ticker","placed_s"],keep="last")
IDX={os.path.basename(p)[:-4]:p for p in glob.glob("data_her/reduced/*/KXBTC*.npz")}
B=B[B.ticker.isin(IDX)]; print(len(B),"bids with her book data", B.ticker.nunique(),"markets")
cache={}
def load(tk):
    if tk not in cache:
        if len(cache)>64: cache.clear()
        z=np.load(IDX[tk]); cache[tk]={k:z[k] for k in z.files if k!="meta"}
    return cache[tk]
def level(row_p,row_q,b):
    m=row_p==b; return float(row_q[m].sum())
out=[]
for r in B.itertuples():
    z=load(r.ticker); T=int((r.open_ts+r.placed_s)*1000); b=int(round(r.bid*1000)); n=float(r.n)
    t=z["t"]; i0=np.searchsorted(t,T,side="right")-1
    if i0<0: continue
    our_p,our_q,opp_p,opp_q=(z["yp"],z["yq"],z["np"],z["nq"]) if r.side=="y" else (z["np"],z["nq"],z["yp"],z["yq"])
    best_bid=int(our_p[i0][0]); ask=1000-int(opp_p[i0][0]) if opp_p[i0][0]>0 else None
    Q0=level(our_p[i0],our_q[i0],b) if b<=best_bid else 0.0
    end=T+int(WAIT*1000)
    tt,tp,ts,tq=z["tt"],z["tp"],z["ts"],z["tq"]; j=(tt>T+100)&(tt<=end)
    if r.side=="y": hit=j&(ts==0); px=tp[hit]
    else: hit=j&(ts==1); px=1000-tp[hit]
    q=tq[hit]; ttt=tt[hit]
    # book rows in the window for the optimistic cancel rule
    k0,k1=i0+1,np.searchsorted(t,end,side="right")
    res={}
    for mode in ("pess","opt"):
        Q=Q0; got=0.0; rows=iter(range(k0,k1)); nxt=next(rows,None)
        for tm,p_,v in zip(ttt,px,q):
            if mode=="opt":
                while nxt is not None and t[nxt]<tm:
                    Q=min(Q,level(our_p[nxt],our_q[nxt],b)); nxt=next(rows,None)
            if p_<b: got=n; break
            if p_==b:
                use=min(Q,v); Q-=use; got=min(n,got+v-use)
                if got>=n: break
        res[mode]=got
    # market buy for the rest at expiry (her book at the expiry second)
    ie=np.searchsorted(t,end+1000,side="right")-1; mk=None; depth=0.0
    if ie>=0:
        lv=[(1000-int(p_),float(qq)) for p_,qq in zip(opp_p[ie],opp_q[ie]) if p_>0]; lv.sort()
        depth=sum(qq for _,qq in lv)
        rest=n-res["pess"]; cost=0.0; left=rest
        for a_,qq in lv:
            u=min(left,qq); cost+=u*a_; left-=u
            if left<=0: break
        mk=(cost/(rest-left))/1000 if rest-left>0 else None
        mk_best=lv[0][0]/1000 if lv else None
    out.append(dict(ticker=r.ticker,placed_s=r.placed_s,side=r.side,bid=r.bid,ask_bt=r.ask,ask_book=None if ask is None else ask/1000,n=n,
                    queue_ahead=Q0,bt_result=r.result,fill_pess=res["pess"],fill_opt=res["opt"],bt_market_px=getattr(r,"market_px",np.nan),
                    book_market_px=mk,book_best_ask_at_expiry=mk_best if ie>=0 else None,ask_depth5=depth))
D=pd.DataFrame(out); D.to_csv(f"results/{PRE}_fillsim.csv",index=False)
D["above90"]=D.bid>=0.90
for g,x in [("all",D)]+[(f"bid {'>=' if k else '<'}90c",x) for k,x in D.groupby("above90")]:
    bt=(x.bt_result=="filled").mean()
    print(f"{g:10s} bids {len(x):6d} | backtest filled {bt:.1%} | real book full fill: pess {(x.fill_pess>=x.n).mean():.1%}  opt {(x.fill_opt>=x.n).mean():.1%}"
          f" | any fill: pess {(x.fill_pess>0).mean():.1%} opt {(x.fill_opt>0).mean():.1%} | contracts filled pess {x.fill_pess.sum()/x.n.sum():.1%} opt {x.fill_opt.sum()/x.n.sum():.1%}"
          f" | median queue ahead {x.queue_ahead.median():.0f}")
f=D[D.bt_result=="filled"]; print("backtest-filled bids: real book full fill pess %.1f%% opt %.1f%%"%((f.fill_pess>=f.n).mean()*100,(f.fill_opt>=f.n).mean()*100))
e=D[(D.bt_result=="expired")&D.bt_market_px.notna()&D.book_market_px.notna()]
print("fallback market buys: %d | backtest price - real book price (c): median %+.2f mean %+.2f | real avg vs best ask (c) mean %+.2f | rest > top-5 depth %.1f%%"%(
    len(e),((e.bt_market_px-e.book_market_px)*100).median(),((e.bt_market_px-e.book_market_px)*100).mean(),((e.book_market_px-e.book_best_ask_at_expiry)*100).mean(),(e.n>e.ask_depth5).mean()*100))
print("ask check (backtest ask - book ask, c): median %+.2f, |diff|>1c %.1f%%"%(((D.ask_bt-D.ask_book)*100).median(),((D.ask_bt-D.ask_book).abs()>0.01).mean()*100))
