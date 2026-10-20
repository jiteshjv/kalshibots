# Older data (before the 66-day set) into data_hist/, same layout as data/ so the backtests run unchanged there.
# Kalshi markets/trades come from the /historical/ endpoints (data older than Kalshi's cutoff); BTC from Coinbase + Kraken trades
# (per-second VWAP) and Binance 1s klines (monthly zips). Every part resumes where it stopped.
# Run from data_hist/: python3 -P -u ../scripts/hist_dl.py <part> 2026-01-01 2026-08-11
#   parts: markets | ktr | coinbase | kraken | binance | build      (scripts/hist_start.sh runs them all in the background)
import json,subprocess,time,datetime as dt,os,sys,threading,zipfile,io,csv,glob
from concurrent.futures import ThreadPoolExecutor
PART=sys.argv[1]
S0=int(dt.datetime.fromisoformat(sys.argv[2]).replace(tzinfo=dt.timezone.utc).timestamp())
E0=int(dt.datetime.fromisoformat(sys.argv[3]).replace(tzinfo=dt.timezone.utc).timestamp())
START=S0-3*3600                                     # BTC prices start 3 h early (2h range + strike minute)
for d in ("ktr","cb","kr","bn"): os.makedirs(d,exist_ok=True)
def tsof(s): return dt.datetime.fromisoformat(s.replace("Z","+00:00")).timestamp()
def log(*a): print(dt.datetime.now(dt.timezone.utc).strftime("%FT%TZ"),*a,flush=True)
def limiter(gap):
    lk=threading.Lock(); last=[0.0]
    def get(u):
        for i in range(12):
            with lk:
                w=last[0]+gap-time.time()
                if w>0: time.sleep(w)
                last[0]=time.time()
            try:
                r=subprocess.run(["curl","-s","-w","\n%{http_code}",u],capture_output=True,text=True,timeout=40).stdout
                b,c=r.rsplit("\n",1)
                if c=="200": return json.loads(b)
                if c=="429": time.sleep(2+i)
            except Exception: pass
            time.sleep(1+i)
        return None
    return get
KAL="https://api.elections.kalshi.com/trade-api/v2"
if PART=="markets":
    kget=limiter(0.08); out=[]; cur=""
    while True:
        d=kget(f"{KAL}/historical/markets?series_ticker=KXBTC15M&limit=1000"+(f"&cursor={cur}" if cur else ""))
        if d is None: log("markets: request failed, retrying"); time.sleep(10); continue
        for m in d["markets"]:
            ct=tsof(m["close_time"])
            if S0+900<=ct<=E0: out.append({k:m.get(k) for k in ("ticker","close_time","floor_strike","expiration_value","result","volume_fp")})
        cur=d.get("cursor"); oldest=d["markets"][-1]["close_time"] if d["markets"] else None
        log("markets page, kept",len(out),"oldest seen",oldest)
        if not cur or not d["markets"] or (oldest and tsof(oldest)<S0): break
    json.dump(out,open("settled.json","w")); log("FINISHED markets",len(out))
if PART=="ktr":
    while not os.path.exists("settled.json"): time.sleep(30)
    M=json.load(open("settled.json")); kget=limiter(0.07)
    def ktr(x):
        fn=f"ktr/{x['ticker']}.json"
        if os.path.exists(fn): return 1
        agg={}; cur=""
        while True:
            d=kget(f"{KAL}/historical/trades?ticker={x['ticker']}&limit=1000"+(f"&cursor={cur}" if cur else ""))
            if d is None: return 0
            for t in d["trades"]:
                s=int(tsof(t["created_time"])); side=t["taker_side"]; q=float(t["count_fp"])
                p=float(t["yes_price_dollars"]) if side=="yes" else float(t["no_price_dollars"])
                a=agg.setdefault(s,{"y":[9,0,0],"n":[9,0,0]})[side[0]]; a[0]=min(a[0],p); a[1]+=q; a[2]+=q*p
            cur=d.get("cursor")
            if not cur or not d["trades"]: break
        json.dump(agg,open(fn+".tmp","w")); os.replace(fn+".tmp",fn); return 1
    done=0
    with ThreadPoolExecutor(4) as ex:
        for r in ex.map(ktr,M[::-1]):
            done+=r
            if done%50==0: log("kalshi trades",done,"/",len(M))
    log("FINISHED ktr",done,"/",len(M))
if PART=="coinbase":
    cget=limiter(float(os.environ.get("CB_GAP","0.17")))
    if os.path.exists("cb/_plan.json"): lo,hi=json.load(open("cb/_plan.json"))
    else:
        def first_id_at(T):
            top=cget("https://api.exchange.coinbase.com/products/BTC-USD/trades?limit=1")[0]["trade_id"]+1; a,b=1,top
            while b-a>1000:
                mid=(a+b)//2; t=cget(f"https://api.exchange.coinbase.com/products/BTC-USD/trades?limit=1&after={mid}")[0]
                if tsof(t["time"])<T: a=mid
                else: b=mid
            return a
        lo=first_id_at(START); hi=first_id_at(E0+3600); json.dump([lo,hi],open("cb/_plan.json","w"))
    N=200; step=(hi-lo)//N+1; log("coinbase trade ids",lo,hi,"=",hi-lo,"trades in",N,"chunks")
    def chunk(k):
        a=lo+k*step; b=min(lo+(k+1)*step,hi); fn=f"cb/{k:03d}.json"
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
        json.dump(agg,open(fn+".tmp","w")); os.replace(fn+".tmp",fn); log("coinbase chunk",k,"done")
    with ThreadPoolExecutor(int(os.environ.get("CB_THREADS","6"))) as ex: list(ex.map(chunk,range(N)))
    log("FINISHED coinbase")
if PART=="kraken":
    done=sorted(glob.glob("kr/*.json")); since=START*10**9; day=None; agg={}
    if done: since=int(dt.datetime.fromisoformat(done[-1][3:13]).replace(tzinfo=dt.timezone.utc).timestamp()+86400)*10**9; log("kraken resuming after",done[-1][3:13])
    def flush(d):
        if d: json.dump(agg,open(f"kr/{d}.json.tmp","w")); os.replace(f"kr/{d}.json.tmp",f"kr/{d}.json")
    while True:
        try: r=json.loads(subprocess.run(["curl","-s",f"https://api.kraken.com/0/public/Trades?pair=XBTUSD&count=1000&since={since}"],capture_output=True,text=True,timeout=40).stdout)
        except Exception: time.sleep(3); continue
        if r.get("error"): time.sleep(5); continue
        res=r["result"]; T=res[[k for k in res if k!="last"][0]]; stop=False
        for p,q,t,*_ in T:
            s=int(t)
            if s>=E0+3600: stop=True; break
            d=dt.datetime.fromtimestamp(s,dt.timezone.utc).strftime("%Y-%m-%d")
            if d!=day: flush(day); day=d; agg={}; log("kraken day",d)
            p=float(p); q=float(q); e=agg.setdefault(s,[0.0,0.0,p]); e[0]+=p*q; e[1]+=q
        since=int(res["last"])
        if stop or not T: break
        time.sleep(1.05)
    flush(day); log("FINISHED kraken")
if PART=="binance":
    m=dt.date.fromtimestamp(START).replace(day=1)
    while m<=dt.date.fromtimestamp(E0):
        f=f"bn/BTCUSDT-1s-{m:%Y-%m}.zip"
        if not os.path.exists(f):
            r=subprocess.run(["curl","-s","-f","-o",f,f"https://data.binance.vision/data/spot/monthly/klines/BTCUSDT/1s/BTCUSDT-1s-{m:%Y-%m}.zip"])
            log("binance",m.strftime("%Y-%m"),"ok" if r.returncode==0 else "missing")
            if r.returncode and os.path.exists(f): os.remove(f)
        m=(m.replace(day=28)+dt.timedelta(days=4)).replace(day=1)
    log("FINISHED binance")
if PART=="build":
    import numpy as np
    T0=START; n=E0+3600-T0
    def agg_arr(files):
        pv=np.zeros(n); v=np.zeros(n)
        for f in files:
            for k,(a,b,_) in json.load(open(f)).items():
                i=int(k)-T0
                if 0<=i<n: pv[i]+=a; v[i]+=b
        p=np.where(v>0,pv/np.where(v>0,v,1),np.nan); idx=np.where(~np.isnan(p),np.arange(n),0); np.maximum.accumulate(idx,out=idx)
        out=p[idx]; out[:np.argmax(v>0)]=np.nan; return out
    cb=agg_arr(glob.glob("cb/[0-9]*.json")); kr=agg_arr(glob.glob("kr/*.json"))
    bn=np.full(n,np.nan)
    for f in sorted(glob.glob("bn/*.zip")):
        z=zipfile.ZipFile(f)
        for r in csv.reader(io.TextIOWrapper(z.open(z.namelist()[0]))):
            t=int(r[0]); t=t//1000000 if t>1e14 else t//1000; i=t-T0
            if 0<=i<n: bn[i]=float(r[4])
    ratio=cb/bn; blk=np.array([np.nanmedian(ratio[max(0,i-3600):i]) if i>0 and np.isfinite(ratio[max(0,i-3600):i]).any() else np.nan for i in range(0,n,60)])
    bnu=bn*np.repeat(blk,60)[:n]
    CW=json.load(open("../data/composite_weights.json")); wc,wk,wb,off=CW["w_cb"],CW["w_kr"],CW["w_bn"],CW["offset"]
    comp=np.where(np.isnan(bnu),(wc*cb+wk*kr)/(wc+wk)+off,wc*cb+wk*kr+wb*bnu+off)
    np.savez_compressed("prices_1s.npz",T0=T0,cb=cb,kr=kr,bn=bn); np.save("composite_1s.npy",comp)
    import pandas as pd
    M=json.load(open("settled.json")); pd.DataFrame({"op":sorted(int(tsof(x["close_time"]))-900 for x in M)}).to_pickle("cands.pkl")
    log(f"FINISHED build: {n} s | coverage cb {np.mean(~np.isnan(cb)):.3f} kr {np.mean(~np.isnan(kr)):.3f} bn {np.mean(~np.isnan(bnu)):.3f}")
