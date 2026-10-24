# ETH sheet data: the BTC bot's rules on KXETH15M (scripts/run_eth.sh -> results/eth_<run>_all_*), Dec 10 2025 - Oct 21 2026,
# into <out>/<run>.z.txt = base64(gzip(json)) in the same shape as build_btc_sheet.py (skipped markets carry skip=1).
# python3 -P scripts/build_eth_sheet.py <out_dir>
import pandas as pd,json,sys,os,math,gzip,base64,datetime as _dt
from zoneinfo import ZoneInfo as _Z
OUT=sys.argv[1]; sec=lambda s:int(s.split(":")[0])*60+int(s.split(":")[1])
def clean(o):
    if isinstance(o,float): return None if math.isnan(o) else round(o,4)
    if isinstance(o,list): return [clean(x) for x in o]
    if isinstance(o,dict): return {k:clean(v) for k,v in o.items()}
    return o
RUNS=["bot","s5","s20","s100","r10","r20","vs15","sf25","sf10","s100sf25"]
_C=pd.read_csv("data_news/calendar.csv"); _ET=_Z("America/New_York")
NEWS={int(_dt.datetime.fromisoformat(f"{a}T{b}").replace(tzinfo=_ET).timestamp())+900*k for a,b,e in zip(_C.date,_C.time_et,_C.event) if e in ("jobs","cpi","fomc") for k in range(4)}
yr=lambda day:"2025" if day.split(" ")[1]=="Dec" else "2026"          # the data runs Dec 10 2025 - Oct 21 2026
for name in RUNS:
    f=f"results/eth_{name}_all"
    M=pd.read_csv(f+"_markets.csv").drop_duplicates("ticker",keep="last"); E=pd.read_csv(f+"_entries.csv").drop_duplicates(["ticker","entry_no"],keep="last")
    K=pd.read_csv(f+"_skipped.csv") if os.path.getsize(f+"_skipped.csv")>2 else pd.DataFrame(columns=["ticker","day","window_pt","why"])
    K=K.drop_duplicates("ticker",keep="last"); K=K[~K.ticker.isin(M.ticker)]
    K=K.assign(side="skipped: "+K.why,strike=None,result=None,entries=0,contracts=0,avg_buy=None,first_entry=None,stopped=0,sold995=0,settled=0,pnl=0.0,skip=1).drop(columns="why")
    M=pd.concat([M.assign(skip=0),K],ignore_index=True)
    M["o"]=pd.to_datetime(M.day+" "+M.day.map(yr)+" "+M.window_pt,format="%a %b %d %Y %I:%M %p"); M=M.sort_values("o")
    nw=(M.o.dt.tz_localize("America/Los_Angeles",ambiguous="NaT",nonexistent="NaT").astype("int64")//10**9).isin(NEWS)&(M.skip==0)   # the bot's NFP/CPI/FOMC hour off
    M.loc[nw,["side","entries","contracts","stopped","sold995","settled","pnl","skip"]]=["skipped: NFP/CPI/FOMC hour",0,0,0,0,0,0.0,1]
    M.loc[nw,["avg_buy","first_entry"]]=None
    M["d"]=M.o.dt.strftime("%Y-%m-%d"); M=M.drop(columns="o")
    E=E[E.ticker.isin(M.ticker[M.skip==0])].copy(); E["side_s"]=E.side.str[0]
    ent={k:g[["entry_no","entry_time","side_s","n","moneyness_pct","threshold_pct","buy","stop","exit","exit_time","exit_px","pnl"]].values.tolist() for k,g in E.groupby("ticker")}
    caps={};held={};peak={}
    for k,g in E.groupby("ticker"):
        ev=sorted([(sec(a),n*b,n) for a,n,b in zip(g.entry_time,g.n,g.buy)]+[(sec(a),-n*b,-n) for a,n,b in zip(g.exit_time,g.n,g.buy)],key=lambda z:(z[0],z[1]))
        run=mx=0.0; rn=pk=0
        for _,c,q in ev: run+=c; mx=max(mx,run); rn+=q; pk=max(pk,rn)
        caps[k]=round(mx,2); peak[k]=int(pk); held[k]=max(map(sec,g.exit_time))-min(map(sec,g.entry_time))
    M["cap"]=M.ticker.map(caps).fillna(0); M["held_s"]=M.ticker.map(held).fillna(0); M["peak_n"]=M.ticker.map(peak).fillna(0).astype(int)
    body=json.dumps(clean({"m":M.to_dict("records"),"e":ent}),separators=(",",":"),allow_nan=False)
    open(f"{OUT}/{name}.z.txt","w").write(base64.b64encode(gzip.compress(body.encode(),compresslevel=9)).decode())
    T=M[M.skip==0]; c=T.pnl.cumsum(); dd=(c-c.cummax()).min(); j=T[T.d>="2026-06-01"]
    print(f"{name:9s} traded {len(T)}, skipped {int(M.skip.sum())}, total ${T.pnl.sum():+,.0f} (Jun-Oct ${j.pnl.sum():+,.0f}), max DD ${dd:,.0f}, {T.d.min()}..{T.d.max()}, MB {os.path.getsize(f'{OUT}/{name}.z.txt')/1e6:.1f}")
