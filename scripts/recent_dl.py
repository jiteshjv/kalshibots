# Download recent data (after the 66-day set) into data_recent/ in the same layout as data/, so the backtest scripts run unchanged there.
# Kalshi settled KXBTC15M markets + per-second trade tape, Coinbase + Kraken trades -> per-second VWAP, Binance 1s klines (daily zips),
# then the composite BRTI proxy with the research weights (Binance's weight is spread over CB/KR where Binance is missing).
# Run from data_recent/: python3 -P ../scripts/recent_dl.py "2026-10-15T20:00"
import json,subprocess,time,datetime as dt,os,sys,threading,zipfile,io,csv,glob
from concurrent.futures import ThreadPoolExecutor
import numpy as np
START=int(dt.datetime.fromisoformat(sys.argv[1]).replace(tzinfo=dt.timezone.utc).timestamp()); END=int(time.time())
for d in ("ktr","cb","kr","bn"): os.makedirs(d,exist_ok=True)
def tsof(s): return dt.datetime.fromisoformat(s.replace("Z","+00:00")).timestamp()
def limiter(gap):
    lk=threading.Lock(); last=[0.0]
    def get(u):
        for i in range(10):
            with lk:
                w=last[0]+gap-time.time()
                if w>0: time.sleep(w)
                last[0]=time.time()
            try:
                r=subprocess.run(["curl","-s","-w","\n%{http_code}",u],capture_output=True,text=True,timeout=30).stdout
                b,c=r.rsplit("\n",1)
                if c=="200": return json.loads(b)
            except Exception: pass
            time.sleep(1+i)
        return None
    return get
kget=limiter(0.11); cget=limiter(0.17)
# 1) Kalshi settled markets
if not os.path.exists("settled.json"):
    out=[]; cur=""
    while True:
        d=kget(f"https://api.elections.kalshi.com/trade-api/v2/markets?series_ticker=KXBTC15M&status=settled&limit=1000&min_close_ts={START+900}"+(f"&cursor={cur}" if cur else ""))
        out+=[{k:m.get(k) for k in ("ticker","close_time","floor_strike","expiration_value","result","volume_fp")} for m in d["markets"]]
        cur=d.get("cursor")
        if not cur or not d["markets"]: break
    json.dump(out,open("settled.json","w")); print("settled markets",len(out),flush=True)
M=json.load(open("settled.json"))
# 2) Kalshi trade tape per window: second -> {"y":[min px, qty, notional],"n":[...]}
def ktr(x):
    fn=f"ktr/{x['ticker']}.json"
    if os.path.exists(fn): return
    agg={}; cur=""
    while True:
        d=kget(f"https://api.elections.kalshi.com/trade-api/v2/markets/trades?ticker={x['ticker']}&limit=1000"+(f"&cursor={cur}" if cur else ""))
        if d is None: return
        for t in d["trades"]:
            s=int(tsof(t["created_time"])); side=t["taker_side"]; q=float(t["count_fp"])
            p=float(t["yes_price_dollars"]) if side=="yes" else float(t["no_price_dollars"])
            a=agg.setdefault(s,{"y":[9,0,0],"n":[9,0,0]})[side[0]]; a[0]=min(a[0],p); a[1]+=q; a[2]+=q*p
        cur=d.get("cursor")
        if not cur or not d["trades"]: break
    json.dump(agg,open(fn+".tmp","w")); os.replace(fn+".tmp",fn)
def kalshi():
    with ThreadPoolExecutor(3) as ex: list(ex.map(ktr,M))
    print("kalshi trades done",len(glob.glob("ktr/*.json")),flush=True)
# 3) Coinbase trades -> per-second [sum p*q, sum q, last p]
def coinbase():
    if os.path.exists("cb/done"): return
    top=cget("https://api.exchange.coinbase.com/products/BTC-USD/trades?limit=1")[0]["trade_id"]+1
    lo,hi=top-6_000_000,top
    while hi-lo>1000:
        mid=(lo+hi)//2; t=cget(f"https://api.exchange.coinbase.com/products/BTC-USD/trades?limit=1&after={mid}")[0]
        if tsof(t["time"])<START: lo=mid
        else: hi=mid
    N=24; step=(top-lo)//N+1; print("coinbase trades",top-lo,flush=True)
    def chunk(k):
        a=lo+k*step; b=min(lo+(k+1)*step,top); fn=f"cb/{k:03d}.json"
        if os.path.exists(fn): return
        agg={}; cur=b
        while cur>a:
            d=cget(f"https://api.exchange.coinbase.com/products/BTC-USD/trades?limit=1000&after={cur}")
            if not d: break
            for t in d:
                if t["trade_id"]<a: continue
                s=int(tsof(t["time"])); p=float(t["price"]); q=float(t["size"])
                e=agg.setdefault(s,[0.0,0.0,p]); e[0]+=p*q; e[1]+=q
            cur=d[-1]["trade_id"]
        json.dump(agg,open(fn+".tmp","w")); os.replace(fn+".tmp",fn)
    with ThreadPoolExecutor(6) as ex: list(ex.map(chunk,range(N)))
    open("cb/done","w").write("1"); print("coinbase done",flush=True)
# 4) Kraken trades
def kraken():
    if os.path.exists("kr/all.json"): return
    since=START*10**9; agg={}
    while True:
        try: r=json.loads(subprocess.run(["curl","-s",f"https://api.kraken.com/0/public/Trades?pair=XBTUSD&count=1000&since={since}"],capture_output=True,text=True,timeout=30).stdout)
        except Exception: time.sleep(3); continue
        if r.get("error"): time.sleep(5); continue
        res=r["result"]; T=res[[k for k in res if k!="last"][0]]
        for p,q,t,*_ in T:
            s=int(t); p=float(p); q=float(q); e=agg.setdefault(s,[0.0,0.0,p]); e[0]+=p*q; e[1]+=q
        since=int(res["last"])
        if not T or since//10**9>=END-5: break
        time.sleep(1.05)
    json.dump(agg,open("kr/all.json.tmp","w")); os.replace("kr/all.json.tmp","kr/all.json"); print("kraken done",len(agg),"seconds",flush=True)
# 5) Binance 1s klines (daily files appear the next day)
def binance():
    d=dt.datetime.fromtimestamp(START,dt.timezone.utc).date()
    while d<=dt.datetime.fromtimestamp(END,dt.timezone.utc).date():
        f=f"bn/BTCUSDT-1s-{d}.zip"
        if not os.path.exists(f):
            r=subprocess.run(["curl","-s","-f","-o",f,f"https://data.binance.vision/data/spot/daily/klines/BTCUSDT/1s/BTCUSDT-1s-{d}.zip"])
            if r.returncode: (os.path.exists(f) and os.remove(f)); print("binance missing",d,flush=True)
        d+=dt.timedelta(days=1)
with ThreadPoolExecutor(4) as ex: [f.result() for f in [ex.submit(g) for g in (kalshi,coinbase,kraken,binance)]]
# 6) per-second prices + composite
T0=START; n=END-T0
def agg_arr(files):
    pv=np.zeros(n); v=np.zeros(n)
    for f in files:
        for k,(a,b,_) in json.load(open(f)).items():
            i=int(k)-T0
            if 0<=i<n: pv[i]+=a; v[i]+=b
    p=np.where(v>0,pv/np.where(v>0,v,1),np.nan); idx=np.where(~np.isnan(p),np.arange(n),0); np.maximum.accumulate(idx,out=idx)
    out=p[idx]; out[:np.argmax(v>0)]=np.nan; return out
cb=agg_arr(glob.glob("cb/[0-9]*.json")); kr=agg_arr(["kr/all.json"])
bn=np.full(n,np.nan)
for f in sorted(glob.glob("bn/*.zip")):
    z=zipfile.ZipFile(f)
    for r in csv.reader(io.TextIOWrapper(z.open(z.namelist()[0]))):
        t=int(r[0]); t=t//1000000 if t>1e14 else t//1000; i=t-T0
        if 0<=i<n: bn[i]=float(r[4])
ratio=cb/bn; blk=np.array([np.nanmedian(ratio[max(0,i-3600):i]) if i>0 and np.isfinite(ratio[max(0,i-3600):i]).any() else np.nan for i in range(0,n,60)])
bnu=bn*np.repeat(blk,60)[:n]
CW=json.load(open("../data/composite_weights.json")); wc,wk,wb,off=CW["w_cb"],CW["w_kr"],CW["w_bn"],CW["offset"]
full=wc*cb+wk*kr+wb*bnu+off; nobn=(wc*cb+wk*kr)/(wc+wk)+off
comp=np.where(np.isnan(bnu),nobn,full)
np.savez_compressed("prices_1s.npz",T0=T0,cb=cb,kr=kr,bn=bn); np.save("composite_1s.npy",comp)
print(f"prices {n} s from {dt.datetime.fromtimestamp(T0,dt.timezone.utc):%F %H:%M} UTC | coverage cb {np.mean(~np.isnan(cb)):.3f} kr {np.mean(~np.isnan(kr)):.3f} "
      f"bn {np.mean(~np.isnan(bnu)):.3f} composite {np.mean(~np.isnan(comp)):.3f}",flush=True)
print("FINISHED",flush=True)
