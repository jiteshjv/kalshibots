# Around each real-book stop trigger (Jun 7 - Oct 21, 5 Hz BRTI): does BRTI or our bid move first?  Average paths, -5 s .. +1 s,
# each scaled to its own move from -5 s to the trigger (0 = level 5 s before, 1 = level at the trigger).
#   python3 -P scripts/her_stop_leadlag.py <prefix>
import glob,os,sys,json,numpy as np,pandas as pd
PRE=sys.argv[1]
fs=glob.glob(f"results/jh_{PRE}_*_entries.csv")+glob.glob(f"results/v9_{PRE}_*_entries.csv")
E=pd.concat([pd.read_csv(f) for f in fs]).drop_duplicates(["ticker","entry_no"],keep="last"); S=E[E.exit=="stopped"]
IDX={os.path.basename(p)[:-4]:p for p in glob.glob("data_her/reduced/*/KXBTC*.npz")}
sec=lambda s:int(s.split(":")[0])*60+int(s.split(":")[1]); OFF=np.arange(-50,11)*100; PX={}; B=[];R=[]
for (tk,xt,sd),g in S.groupby(["ticker","exit_time","side"]):
    if tk not in IDX: continue
    day=os.path.basename(os.path.dirname(IDX[tk])); pf=f"data_her/reduced/{day}/prices.npz"
    if day not in PX:
        z=np.load(pf); PX.clear(); PX[day]=(z["ft"],z["fv"]) if "ft" in z.files else None
    if PX[day] is None: continue
    ft,fv=PX[day]; z=np.load(IDX[tk]); m=json.loads(str(z["meta"]))
    op=int(pd.Timestamp(m["close_time"]).timestamp()*1000)-900_000; y=sd.startswith("YES"); P=z["yp"] if y else z["np"]; t=z["t"]
    stop=int(round(g.stop.max()*1000)); s0=op+(sec(xt)-1)*1000
    w=np.nonzero((t>s0)&(t<=s0+1000)&(P[:,0]>0)&(P[:,0]<=stop))[0]; T=int(t[w[0]]) if len(w) else s0+1000
    ib=np.searchsorted(t,T+OFF,side="right")-1; ir=np.searchsorted(ft,T+OFF,side="right")-1
    if ib.min()<0 or ir.min()<0: continue
    b=P[ib,0]/1000.; r=fv[ir]*(1 if y else -1)
    db=b[50]-b[0]; dr=r[50]-r[0]
    if db>=-0.02 or dr>=0: continue           # keep the stops where both fell over the 5 s
    B.append((b-b[0])/db); R.append((r-r[0])/dr)
B=np.array(B); R=np.array(R); print(len(B),"stops with both the bid and BRTI falling over the 5 s before the trigger")
print("  t (s)   bid path   BRTI path   (share of the 5 s move done; median)")
for i in range(0,61,5): print(f"  {OFF[i]/1000:+5.1f}    {np.median(B[:,i]):6.2f}     {np.median(R[:,i]):6.2f}")
for q in (0.25,0.5,0.75):
    tb=np.array([OFF[np.argmax(x>=q)] for x in B]); tr=np.array([OFF[np.argmax(x>=q)] for x in R])
    print(f"{q:.0%} of the move: bid at {np.median(tb)/1000:+.1f} s, BRTI at {np.median(tr)/1000:+.1f} s; BRTI first in {(tr<tb).mean():.0%}, same 100 ms {(tr==tb).mean():.0%}")
