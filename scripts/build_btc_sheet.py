# BTC sheet data (Jun 1 - Oct 19): for each switch combination, merge Jun 1 - Aug 10 (data_hist runs jh_*) with Aug 10 - Oct 19
# (v8_/v9_ runs on data/, data_recent/, data_mon/) into <out>/<mode>.json. Skipped markets (filters on) carry skip=1.
# python3 -P scripts/build_btc_sheet.py <out_dir>
import pandas as pd,json,sys,os,math
OUT=sys.argv[1]; HM=["jun","jul","aug"]; sec=lambda s:int(s.split(":")[0])*60+int(s.split(":")[1])
def clean(o):
    if isinstance(o,float): return None if math.isnan(o) else round(o,4)
    if isinstance(o,list): return [clean(x) for x in o]
    if isinstance(o,dict): return {k:clean(v) for k,v in o.items()}
    return o
late=lambda p:[f"{p}_66d",f"{p}_recent"]+([f"{p}_oct20"] if os.path.exists(f"results/{p}_oct20_markets.csv") else [])+[f"{p}_mon"]+[f"{p}_{d}" for d in ("oct21","oct22") if os.path.exists(f"results/{p}_{d}_markets.csv")]   # mon wins on its overlap; oct21 (newest) last
MODES={"mixed":("jh_off",late("v8_mixed")),"mixed_f":("jh_now",late("v8_mixed_f")),
       "mixed_l3":("jh_l3off",late("v9_l3")),"mixed_l3_f":("jh_l3",late("v9_l3_f")),
       "mixed_s25l3":("jh_s25l3off",late("v9_s25l3")),"mixed_s25l3_f":("jh_s25l3",late("v9_s25l3_f")),
       "equal":("jh_eq",late("v8_equal")),"equal_f":("jh_eqf",late("v8_equal_f")),
       "equal_l3":("jh_eq_l3",late("v9_eq_l3")),"equal_l3_f":("jh_eq_l3_f",late("v9_eq_l3_f")),
       "equal_s25l3":("jh_eq_s25l3",late("v9_eq_s25l3")),"equal_s25l3_f":("jh_eq_s25l3_f",late("v9_eq_s25l3_f")),
       "mixed5":("jh_m5",late("v8_mixed5")),"mixed5_f":("jh_m5f",late("v8_mixed5_f")),
       "mixed_rec_f":("jh_j5L",late("v9_j5L")),"equal_rec_f":("jh_eqrecL",late("v9_eqrecL")),"mixed5_rec_f":("jh_m5recL",late("v9_m5recL")),
       "mixed_rec_b":("jh_j5B",late("v9_j5B")),"equal_rec_b":("jh_eqrecB",late("v9_eqrecB")),"mixed5_rec_b":("jh_m5recB",late("v9_m5recB")),"equal_recfv_b":("jh_fvs2Bt1",late("v9_fvs2Bt1")),
       "equal_recfv_f":("jh_fvs2L",late("v9_fvs2L")),
       "equal_recrisk_b":("jh_rkW5c",late("v9_rkW5c")),
       "equal_recriskhb_b":("jh_rkHBl100",late("v9_rkHBl100")),
       "equal_recriskhbc_b":("jh_ezrk_2x",late("v9_ezrk_2x")),
       "equal_recriskhbcs_b":("jh_ezCen12b",late("v9_ezCen12b"))}   # = size C + 12% stop for markets starting 3 PM - 3 AM PT (live Oct 23 8:08 PM PT)   # = the same + size C (200 at risk <= 5%, else 100, 50 when the ask >= 97c; ez_ research 2026-10-23, live from Oct 23 6:30 PM PT)   # = the bot now on her real order book (asks, queue, stops), stops sold 100 ms after the trigger   # = the BTC paper bot from 2026-10-21: her risk <= 15%, no moneyness line, bids 1c/0.3c, 5 s wait, stop re-check, valid prices   # *L = long price history in the Oct folders; *_b = 3- to 14-day sweeps only       # recommended: 25% + 0.03% stop, 0.5% jump pause, NFP/CPI/FOMC hour skipped
ONLY=sys.argv[2].split(",") if len(sys.argv)>2 else None
import datetime as _dt; from zoneinfo import ZoneInfo as _Z
_C=pd.read_csv("data_news/calendar.csv"); _ET=_Z("America/New_York")
NEWS={int(_dt.datetime.fromisoformat(f"{a}T{b}").replace(tzinfo=_ET).timestamp())+900*k for a,b,e in zip(_C.date,_C.time_et,_C.event) if e in ("jobs","cpi","fomc") for k in range(4)}
for name,(h,lt) in MODES.items():
    if ONLY and name not in ONLY: continue
    files=[f"{h}_{m}" for m in HM]+lt
    M=pd.concat([pd.read_csv(f"results/{f}_markets.csv") for f in files]).drop_duplicates("ticker",keep="last")
    E=pd.concat([pd.read_csv(f"results/{f}_entries.csv") for f in files]).drop_duplicates(["ticker","entry_no"],keep="last")
    sk=[f"results/{f}_skipped.csv" for f in files if os.path.exists(f"results/{f}_skipped.csv") and os.path.getsize(f"results/{f}_skipped.csv")>2]
    if (name.endswith("_f") or name.endswith("_b")) and sk:
        K=pd.concat([pd.read_csv(f) for f in sk]).drop_duplicates("ticker",keep="last"); K=K[~K.ticker.isin(M.ticker)]
        K=K.assign(side="skipped: "+K.why,strike=None,result=None,entries=0,contracts=0,avg_buy=None,first_entry=None,stopped=0,sold995=0,settled=0,pnl=0.0,skip=1).drop(columns="why")
        M=pd.concat([M.assign(skip=0),K],ignore_index=True)
    else: M=M.assign(skip=0)
    M["o"]=pd.to_datetime(M.day+" 2026 "+M.window_pt,format="%a %b %d %Y %I:%M %p"); M=M[M.o>="2026-06-01"].sort_values("o")
    if "_rec" in name:                     # news hour: shown as skipped (would-be values come from mixed_s25l3)
        nw=(M.o.dt.tz_localize("America/Los_Angeles").astype("int64")//10**9).isin(NEWS)&(M.skip==0)
        M.loc[nw,["side","entries","contracts","stopped","sold995","settled","pnl","skip"]]=["skipped: NFP/CPI/FOMC hour",0,0,0,0,0,0.0,1]
        M.loc[nw,["avg_buy","first_entry"]]=None
    M=M.drop(columns="o")
    E=E[E.ticker.isin(M.ticker)].copy()
    M["d"]=pd.to_datetime(M.day+" 2026",format="%a %b %d %Y").dt.strftime("%Y-%m-%d"); E["side_s"]=E.side.str[0]
    ent={k:g[["entry_no","entry_time","side_s","n","moneyness_pct","threshold_pct","buy","stop","exit","exit_time","exit_px","pnl"]].values.tolist() for k,g in E.groupby("ticker")}
    caps={};held={};peak={}
    for k,g in E.groupby("ticker"):
        ev=sorted([(sec(a),n*b,n) for a,n,b in zip(g.entry_time,g.n,g.buy)]+[(sec(a),-n*b,-n) for a,n,b in zip(g.exit_time,g.n,g.buy)],key=lambda z:(z[0],z[1]))
        run=mx=0.0; rn=pk=0
        for _,c,q in ev: run+=c; mx=max(mx,run); rn+=q; pk=max(pk,rn)
        caps[k]=round(mx,2); peak[k]=int(pk); held[k]=max(map(sec,g.exit_time))-min(map(sec,g.entry_time))
    M["cap"]=M.ticker.map(caps).fillna(0); M["held_s"]=M.ticker.map(held).fillna(0); M["peak_n"]=M.ticker.map(peak).fillna(0).astype(int)
    D=clean({"m":M.to_dict("records"),"e":ent}); body=json.dumps(D,separators=(",",":"),allow_nan=False)
    import gzip                                 # one gzip file per option (the page unpacks it); ~5x smaller than JSON
    import base64                               # served as text: <name>.z.txt = base64(gzip(json)); the page unpacks it
    open(f"{OUT}/{name}.z.txt","w").write(base64.b64encode(gzip.compress(body.encode(),compresslevel=9)).decode())
    T=M[M.skip==0]; c=T.pnl.cumsum(); dd=(c-c.cummax()).min()
    print(f"{name:14s} traded {len(T)}, skipped {int(M.skip.sum())}, total ${T.pnl.sum():+,.0f}, max DD ${dd:,.0f}, max peak held {T.peak_n.max():,}, MB {os.path.getsize(f'{OUT}/{name}.z.txt')/1e6:.1f}")
