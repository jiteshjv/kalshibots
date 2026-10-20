# Gold 15-minute markets (KXGOLD15M) into data_gold/, same layout as data/ so the backtests run unchanged.
# Kalshi: settled markets (historical endpoint before Kalshi's cutoff, regular after) + per-second trade tape.
# Gold price per second: Binance PAXGUSDT 1s klines (PAXG = gold-backed token), scaled to Kalshi's gold strikes with a trailing median of
# floor_strike / PAXG-at-open over the previous 20 markets (no look-ahead). Kalshi settles on the Pyth gold 1-minute close.
# Run from data_gold/: python3 -P -u ../scripts/gold_dl.py <markets|ktr|paxg|build>
import json,subprocess,time,datetime as dt,os,sys,threading,zipfile,io,csv,glob
from concurrent.futures import ThreadPoolExecutor
PART=sys.argv[1]; SER=os.environ.get("SER","KXGOLD15M"); KAL="https://api.elections.kalshi.com/trade-api/v2"
for d in ("ktr","bn"): os.makedirs(d,exist_ok=True)
def tsof(s): return dt.datetime.fromisoformat(s.replace("Z","+00:00")).timestamp()
def log(*a): print(dt.datetime.now(dt.timezone.utc).strftime("%FT%TZ"),*a,flush=True)
lk=threading.Lock(); last=[0.0]
def get(u,gap=0.08):
    for i in range(12):
        with lk:
            w=last[0]+gap-time.time()
            if w>0: time.sleep(w)
            last[0]=time.time()
        try:
            r=subprocess.run(["curl","-s","-w","\n%{http_code}",u],capture_output=True,text=True,timeout=40).stdout
            b,c=r.rsplit("\n",1)
            if c=="200": return json.loads(b)
        except Exception: pass
        time.sleep(1+i)
    return None
F=("ticker","close_time","floor_strike","expiration_value","result","volume_fp")
if PART=="markets":
    out={}
    for path in (f"historical/markets?series_ticker={SER}",f"markets?series_ticker={SER}&status=settled"):
        cur=""
        while True:
            d=get(f"{KAL}/{path}&limit=1000"+(f"&cursor={cur}" if cur else ""))
            if not d: break
            for m in d["markets"]:
                if m.get("result") in ("yes","no"): out[m["ticker"]]=dict({k:m.get(k) for k in F},hist=path.startswith("historical"))
            cur=d.get("cursor")
            if not cur or not d["markets"]: break
    M=sorted(out.values(),key=lambda x:x["close_time"]); json.dump(M,open("settled.json","w"))
    log("FINISHED markets",len(M),M[0]["close_time"],"->",M[-1]["close_time"])
if PART=="ktr":
    M=json.load(open("settled.json"))
    def ktr(x):
        fn=f"ktr/{x['ticker']}.json"
        if os.path.exists(fn): return 1
        agg={}; cur=""; base=f"{KAL}/historical/trades" if x.get("hist") else f"{KAL}/markets/trades"
        while True:
            d=get(f"{base}?ticker={x['ticker']}&limit=1000"+(f"&cursor={cur}" if cur else ""))
            if d is None: return 0
            for t in d["trades"]:
                s=int(tsof(t["created_time"])); side=t["taker_side"]; q=float(t["count_fp"])
                p=float(t["yes_price_dollars"]) if side=="yes" else float(t["no_price_dollars"])
                a=agg.setdefault(s,{"y":[9,0,0],"n":[9,0,0]})[side[0]]; a[0]=min(a[0],p); a[1]+=q; a[2]+=q*p
            cur=d.get("cursor")
            if not cur or not d["trades"]: break
        json.dump(agg,open(fn+".tmp","w")); os.replace(fn+".tmp",fn); return 1
    done=0
    with ThreadPoolExecutor(3) as ex:
        for r in ex.map(ktr,M[::-1]):
            done+=r
            if done%100==0: log("gold trades",done,"/",len(M))
    log("FINISHED ktr",done,"/",len(M))
if PART=="paxg":
    M=json.load(open("settled.json")); a=dt.date.fromtimestamp(tsof(M[0]["close_time"])-86400); b=dt.date.fromtimestamp(tsof(M[-1]["close_time"]))
    m=a.replace(day=1)
    while m<=b:
        nxt=(m.replace(day=28)+dt.timedelta(days=4)).replace(day=1)
        f=f"bn/PAXGUSDT-1s-{m:%Y-%m}.zip"
        if not os.path.exists(f):
            r=subprocess.run(["curl","-s","-f","-o",f,f"https://data.binance.vision/data/spot/monthly/klines/PAXGUSDT/1s/PAXGUSDT-1s-{m:%Y-%m}.zip"])
            if r.returncode:                                        # current month: daily files
                if os.path.exists(f): os.remove(f)
                d=max(m,a)
                while d<min(nxt,b+dt.timedelta(days=1)):
                    g=f"bn/PAXGUSDT-1s-{d}.zip"
                    if not os.path.exists(g):
                        rr=subprocess.run(["curl","-s","-f","-o",g,f"https://data.binance.vision/data/spot/daily/klines/PAXGUSDT/1s/PAXGUSDT-1s-{d}.zip"])
                        if rr.returncode and os.path.exists(g): os.remove(g)
                    d+=dt.timedelta(days=1)
            log("paxg",m.strftime("%Y-%m"),"monthly" if r.returncode==0 else "daily files")
        m=nxt
    log("FINISHED paxg")
if PART=="build":
    import numpy as np,pandas as pd
    M=json.load(open("settled.json")); T0=int(tsof(M[0]["close_time"]))-900-4*3600; T1=int(tsof(M[-1]["close_time"]))+600; n=T1-T0
    px=np.full(n,np.nan)
    for f in sorted(glob.glob("bn/PAXGUSDT-1s-*.zip")):
        z=zipfile.ZipFile(f)
        for r in csv.reader(io.TextIOWrapper(z.open(z.namelist()[0]))):
            t=int(r[0]); t=t//1000000 if t>1e14 else t//1000; i=t-T0
            if 0<=i<n: px[i]=float(r[4])
    idx=np.where(~np.isnan(px),np.arange(n),0); np.maximum.accumulate(idx,out=idx); age=np.arange(n)-idx; px=px[idx]; px[age>120]=np.nan
    # scale PAXG to the Kalshi/Pyth gold level: trailing median of strike / PAXG(open) over the previous 20 markets
    rat=[]; scale=np.full(n,np.nan); prev_i=0; s_cur=np.nan
    for x in M:
        op=int(tsof(x["close_time"]))-900; i=op-T0
        if not x.get("floor_strike") or not (0<=i<n): continue
        scale[prev_i:i]=s_cur; v=px[i-30:i+30]; v=v[~np.isnan(v)]
        if len(v): rat.append(float(x["floor_strike"])/np.median(v))
        s_cur=np.median(rat[-20:]) if rat else np.nan; prev_i=i
    scale[prev_i:]=s_cur
    comp=px*scale
    np.savez_compressed("prices_1s.npz",T0=T0,cb=comp,kr=comp,bn=px); np.save("composite_1s.npy",comp)
    pd.DataFrame({"op":sorted(int(tsof(x["close_time"]))-900 for x in M)}).to_pickle("cands.pkl")
    # check: our gold vs Kalshi's settlement value (Pyth 1-min close at expiry)
    err=[];ok=[]
    for x in M:
        cl=int(tsof(x["close_time"]))-T0
        if x.get("expiration_value") and x.get("floor_strike") and 0<cl<n and comp[cl-1]==comp[cl-1]:
            ev=float(x["expiration_value"]); err.append(comp[cl-1]-ev); ok.append((comp[cl-1]>=float(x["floor_strike"]))==(x["result"]=="yes"))
    err=np.array(err); log(f"FINISHED build: {n} s, coverage {np.mean(~np.isnan(comp)):.3f} | vs official settle: median abs err ${np.median(np.abs(err)):.2f}, outcome match {np.mean(ok)*100:.1f}%")
if PART=="duka":
    # Gold per second from Dukascopy XAUUSD ticks (spot bid/ask): reads the gold project's cache READ-ONLY
    # (/Users/jvidhani/trading/gold/data/cache/XAUUSD) and fetches missing hours into data_gold/duka/ (same .bi5 format).
    import lzma,struct,calendar,numpy as np,pandas as pd
    SRC="/Users/jvidhani/trading/gold/data/cache/XAUUSD"; os.makedirs("duka",exist_ok=True)
    M=json.load(open("settled.json")); T0=int(tsof(M[0]["close_time"]))-900-4*3600; T1=int(tsof(M[-1]["close_time"]))+600; n=T1-T0
    def path_for(d,h):
        a=f"{SRC}/{d:%Y}/{d:%Y-%m-%d}/{h:02d}.bi5"; b=f"duka/{d:%Y-%m-%d}_{h:02d}.bi5"
        if os.path.exists(a): return a
        if not os.path.exists(b):
            u=f"https://datafeed.dukascopy.com/datafeed/XAUUSD/{d.year}/{d.month-1:02d}/{d.day:02d}/{h:02d}h_ticks.bi5"
            code=None
            for i in range(10):                                        # 429 = rate limited: back off; never record it as "no ticks"
                try: r=subprocess.run(["curl","-s","--max-time","50","-o",b+".tmp","-w","%{http_code}",u],timeout=120,capture_output=True,text=True); code=r.stdout.strip()
                except subprocess.TimeoutExpired: code="timeout"
                if code in ("200","404"): break
                time.sleep(min(300,15*(i+1)))
            if code=="200": os.replace(b+".tmp",b)                    # (an empty 200 body = an hour with no ticks)
            else:
                if os.path.exists(b+".tmp"): os.remove(b+".tmp")
                if code=="404": open(b,"wb").close()
                else: return None                                      # still failing: leave unfetched, try again next run
            time.sleep(float(os.environ.get("DUKA_GAP","1.0")))
        return b
    mid=np.full(n,np.nan); fetched=0
    t=T0-T0%3600
    while t<T1:
        d=dt.datetime.fromtimestamp(t,dt.timezone.utc); p=path_for(d.date(),d.hour)
        raw=open(p,"rb").read() if p else b""
        if raw:
            try: data=lzma.decompress(raw)
            except Exception: data=b""
            for i in range(0,len(data)-19,20):
                ms,ask,bid,_,_=struct.unpack(">IIIff",data[i:i+20]); s=t+ms//1000-T0
                if 0<=s<n: mid[s]=(ask+bid)/2/1000.0
        t+=3600
    idx=np.where(~np.isnan(mid),np.arange(n),0); np.maximum.accumulate(idx,out=idx); age=np.arange(n)-idx; mid=mid[idx]; mid[age>300]=np.nan
    # offset to Kalshi/Pyth level: trailing median of (floor_strike - our price at open) over the previous 20 markets (no look-ahead)
    offs=[]; adj=np.zeros(n); prev=0; cur=0.0
    for x in M:
        i=int(tsof(x["close_time"]))-900-T0
        if not x.get("floor_strike") or not (60<=i<n): continue
        adj[prev:i]=cur; v=mid[i-60:i]; v=v[~np.isnan(v)]
        if len(v): offs.append(float(x["floor_strike"])-v[-1])
        cur=float(np.median(offs[-20:])) if offs else 0.0; prev=i
    adj[prev:]=cur; comp=mid+adj
    np.savez_compressed("prices_1s.npz",T0=T0,cb=comp,kr=comp,bn=mid); np.save("composite_1s.npy",comp)
    pd.DataFrame({"op":sorted(int(tsof(x["close_time"]))-900 for x in M)}).to_pickle("cands.pkl")
    err=[];ok=[]
    for x in M:
        cl=int(tsof(x["close_time"]))-T0
        if x.get("expiration_value") and x.get("floor_strike") and 0<cl<n and comp[cl-1]==comp[cl-1]:
            ev=float(x["expiration_value"]); err.append(comp[cl-1]-ev); ok.append((comp[cl-1]>=float(x["floor_strike"]))==(x["result"]=="yes"))
    err=np.array(err)
    log(f"FINISHED duka: coverage {np.mean(~np.isnan(comp)):.3f}, median offset ${np.median(offs):+.2f} | vs official settle: median abs err ${np.median(np.abs(err)):.2f}, p90 ${np.percentile(np.abs(err),90):.2f}, outcome match {np.mean(ok)*100:.1f}% ({len(ok)} mkts)")

if PART=="binance":
    # Per-second price from Binance 1s klines (env SYM, e.g. ETHUSDT), scaled to Kalshi's strikes with a trailing median of
    # floor_strike / price-at-open over the previous 20 markets (no look-ahead). Used for ETH (CF ETHUSD_RTI settles on a 60 s average).
    import numpy as np,pandas as pd
    SYM=os.environ.get("SYM","ETHUSDT"); M=json.load(open("settled.json"))
    a=dt.date.fromtimestamp(tsof(M[0]["close_time"])-2*86400); b=dt.date.fromtimestamp(tsof(M[-1]["close_time"]))
    m=a.replace(day=1)
    while m<=b:
        nxt=(m.replace(day=28)+dt.timedelta(days=4)).replace(day=1); f=f"bn/{SYM}-1s-{m:%Y-%m}.zip"
        if not os.path.exists(f):
            r=subprocess.run(["curl","-s","-f","-o",f,f"https://data.binance.vision/data/spot/monthly/klines/{SYM}/1s/{SYM}-1s-{m:%Y-%m}.zip"])
            if r.returncode:
                if os.path.exists(f): os.remove(f)
                d=max(m,a)
                while d<min(nxt,b+dt.timedelta(days=1)):
                    g=f"bn/{SYM}-1s-{d}.zip"
                    if not os.path.exists(g):
                        rr=subprocess.run(["curl","-s","-f","-o",g,f"https://data.binance.vision/data/spot/daily/klines/{SYM}/1s/{SYM}-1s-{d}.zip"])
                        if rr.returncode and os.path.exists(g): os.remove(g)
                    d+=dt.timedelta(days=1)
        log("binance",SYM,m.strftime("%Y-%m"))
        m=nxt
    T0=int(tsof(M[0]["close_time"]))-900-4*3600; T1=int(tsof(M[-1]["close_time"]))+600; n=T1-T0; px=np.full(n,np.nan)
    for f in sorted(glob.glob(f"bn/{SYM}-1s-*.zip")):
        z=zipfile.ZipFile(f)
        for r in csv.reader(io.TextIOWrapper(z.open(z.namelist()[0]))):
            t=int(r[0]); t=t//1000000 if t>1e14 else t//1000; i=t-T0
            if 0<=i<n: px[i]=float(r[4])
    idx=np.where(~np.isnan(px),np.arange(n),0); np.maximum.accumulate(idx,out=idx); age=np.arange(n)-idx; px=px[idx]; px[age>120]=np.nan
    rat=[]; scale=np.full(n,np.nan); prev=0; cur=np.nan
    for x in M:
        i=int(tsof(x["close_time"]))-900-T0
        if not x.get("floor_strike") or not (60<=i<n): continue
        scale[prev:i]=cur; v=px[i-60:i]; v=v[~np.isnan(v)]
        if len(v): rat.append(float(x["floor_strike"])/np.mean(v))
        cur=float(np.median(rat[-20:])) if rat else np.nan; prev=i
    scale[prev:]=cur; comp=px*scale
    np.savez_compressed("prices_1s.npz",T0=T0,cb=comp,kr=comp,bn=px); np.save("composite_1s.npy",comp)
    pd.DataFrame({"op":sorted(int(tsof(x["close_time"]))-900 for x in M)}).to_pickle("cands.pkl")
    err=[];ok=[]
    for x in M:
        cl=int(tsof(x["close_time"]))-T0
        if x.get("expiration_value") and x.get("floor_strike") and 60<cl<n and not np.isnan(comp[cl-60:cl]).any():
            ev=float(str(x["expiration_value"]).replace(",","")); avg=comp[cl-60:cl].mean(); err.append(avg-ev); ok.append((avg>=float(x["floor_strike"]))==(x["result"]=="yes"))
    err=np.array(err); log(f"FINISHED binance build: coverage {np.mean(~np.isnan(comp)):.3f} | 60s avg vs official settle: median abs err ${np.median(np.abs(err)):.2f}, p90 ${np.percentile(np.abs(err),90):.2f}, outcome match {np.mean(ok)*100:.1f}% ({len(ok)} mkts)")
