# Reduce her tick data (s3://.../btc/<day>/) to small per-day files for fill-model backtests.
#   python3 -P scripts/her_reduce.py <src_day_dir_or_s3_url> <out_dir>      (one day; s3:// paths stream via the aws cli)
# Output <out_dir>/<day>/:
#   <ticker>.npz  book  = top-5 levels each side at the end of every 100 ms step where they changed:
#                         t (int64 ms, step end), yp/np (int16, price in 0.1c), yq/nq (float32 contracts); "yes"/"no" are BIDS
#                         (yes ask = 1 - best no bid)
#                 trades = per 100 ms step, yes price and taker side: tt (int64 ms, step end), tp (int16 0.1c), ts (int8 1 = taker
#                         bought YES, 0 = bought NO), tq (float32 contracts), tn (int32 number of trades)
#                 meta  = metadata.json as a string
#   prices.npz    brti 1/s (bt, bv, bavg60), brti 5 Hz (ft, fv), exchanges last per second (et, cb, bs, cc, vwap); t in ms
import json,os,subprocess,sys,time
import numpy as np
SRC,OUT=sys.argv[1].rstrip("/"),sys.argv[2]; DAY=os.path.basename(SRC); N=5; STEP=100
REGION=os.environ.get("REGION","us-east-2")
def lines(path):
    if path.startswith("s3://"):
        p=subprocess.Popen(["aws","s3","cp",path,"-","--region",REGION,"--only-show-errors"],stdout=subprocess.PIPE,bufsize=1<<20)
        yield from p.stdout; p.wait()
    else:
        with open(path,"rb") as f: yield from f
def listing(path):
    if path.startswith("s3://"):
        o=subprocess.run(["aws","s3","ls",path+"/","--recursive","--region",REGION],capture_output=True,text=True).stdout
        b=path.split("/",3)[2]; return [f"s3://{b}/"+l.split(None,3)[3] for l in o.splitlines() if len(l.split(None,3))==4]
    return [os.path.join(dp,f) for dp,_,fs in os.walk(path) for f in fs]
loads=json.loads
try:
    import orjson; loads=orjson.loads
except ImportError: pass
def top(b,rev=True):
    k=sorted(b,reverse=rev)[:N]; p=np.zeros(N,np.int16); q=np.zeros(N,np.float32)
    for i,x in enumerate(k): p[i]=x; q[i]=b[x]
    return p,q
def market(d,files,force_sc=None):
    meta=next((f for f in files if f.endswith("metadata.json")),None); ob=next((f for f in files if f.endswith("orderbook.jsonl")),None)
    tr=next((f for f in files if f.endswith("trades.jsonl")),None)
    if not (meta and ob and tr): return None,"missing files"
    m=b"".join(lines(meta)).decode()
    sc=force_sc or (1000 if json.loads(m).get("capture",{}).get("tick_format")=="dollars" else 10)   # early April (collector 3.x): book in cents
    yes,no={},{}; rows=[]; cur=None; prev=None; neg=0
    def flush(step):
        nonlocal prev
        s=(top(yes),top(no)); key=(s[0][0].tobytes(),s[0][1].tobytes(),s[1][0].tobytes(),s[1][1].tobytes())
        if key!=prev: rows.append(((step+1)*STEP,)+s[0]+s[1]); prev=key
    for l in lines(ob):
        r=loads(l); step=r["t"]//1_000_000//STEP
        if cur is not None and step!=cur: flush(cur)
        cur=step
        if r["type"]=="snapshot":
            yes={round(p*sc):q for p,q in r["yes"]}; no={round(p*sc):q for p,q in r["no"]}; continue
        if r["type"]!="delta": continue
        b=yes if r["side"]=="yes" else no; k=round(r["p"]*sc); q=b.get(k,0.0)+r["d"]
        if q<=1e-9:
            if q<-1e-6: neg+=1
            b.pop(k,None)
        else: b[k]=q
    if cur is not None: flush(cur)
    agg={}
    for l in lines(tr):
        r=loads(l)
        if r.get("type")!="trade": continue
        k=(r["t"]//1_000_000//STEP,round(r["yes"]*1000),1 if r["taker_side"]=="yes" else 0); a=agg.get(k)
        agg[k]=(a[0]+r["count"],a[1]+1) if a else (r["count"],1)
    ks=sorted(agg)
    out=dict(t=np.array([x[0] for x in rows],np.int64),yp=np.array([x[1] for x in rows]),yq=np.array([x[2] for x in rows]),
             np=np.array([x[3] for x in rows]),nq=np.array([x[4] for x in rows]),
             tt=np.array([(k[0]+1)*STEP for k in ks],np.int64),tp=np.array([k[1] for k in ks],np.int16),ts=np.array([k[2] for k in ks],np.int8),
             tq=np.array([agg[k][0] for k in ks],np.float32),tn=np.array([agg[k][1] for k in ks],np.int32),meta=np.array(m))
    out["scale"]=np.array(sc)
    return out,f"{len(rows)} book steps, {len(ks)} trade steps, neg {neg}"
def prices(files):
    o={}
    f=next((x for x in files if x.endswith("/brti.jsonl")),None)
    if f:
        R=[loads(l) for l in lines(f)]; o.update(bt=np.array([r["src_ts_ms"] for r in R],np.int64),bv=np.array([r["value"] for r in R]),
                                              bavg60=np.array([r.get("avg60",np.nan) or np.nan for r in R]))
    f=next((x for x in files if x.endswith("/brti_5hz.jsonl")),None)
    if f:
        R=[loads(l) for l in lines(f)]; o.update(ft=np.array([r["src_ts_ms"] for r in R],np.int64),fv=np.array([r["value"] for r in R]))
    f=next((x for x in files if x.endswith("/btc_prices.jsonl")),None)
    if f:
        last={}
        for l in lines(f):
            r=loads(l); s=r["t"]//1_000_000_000; last[s]=r
        S=sorted(last); g=lambda k:np.array([(last[s].get(k) or {}).get("price",np.nan) if isinstance(last[s].get(k),dict) or last[s].get(k) is None else np.nan for s in S])
        o.update(et=np.array([s*1000 for s in S],np.int64),cb=g("cb"),bs=g("bs"),cc=g("cc"),vwap=np.array([last[s].get("vwap",np.nan) or np.nan for s in S]))
    return o
t0=time.time(); files=listing(SRC); od=os.path.join(OUT,DAY); os.makedirs(od,exist_ok=True)
by={}
for f in files:
    rel=f[len(SRC)+1:] if not f.startswith("s3://") else f.split(f"/{DAY}/",1)[1]
    if "/" in rel: by.setdefault(rel.split("/")[0],[]).append(f)
for tk,fs in sorted(by.items()):
    if os.path.exists(os.path.join(od,tk+".npz")): continue
    try: o,msg=market(tk,fs)
    except OverflowError:                    # metadata says dollars but the book is in cents (collector change-over)
        try: o,msg=market(tk,fs,10); msg+=" (cents despite metadata)"
        except Exception as e: o,msg=None,f"ERROR {type(e).__name__}: {e}"
    except Exception as e: o,msg=None,f"ERROR {type(e).__name__}: {e}"
    if o is not None: np.savez_compressed(os.path.join(od,tk+".npz"+".tmp.npz"),**o); os.replace(os.path.join(od,tk+".npz.tmp.npz"),os.path.join(od,tk+".npz"))
    print(DAY,tk,msg,flush=True)
if not os.path.exists(os.path.join(od,"prices.npz")): np.savez_compressed(os.path.join(od,"prices.npz"),**prices(files))
print(DAY,"DONE",len(by),"markets",round(time.time()-t0),"s",flush=True)
