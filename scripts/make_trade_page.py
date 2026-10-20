# Build the trade-log page from v1_final CSVs: python3 -P scripts/make_trade_page.py out.html "note" a.csv [b.csv ...]
import pandas as pd,json,sys
out,note,files=sys.argv[1],sys.argv[2],sys.argv[3:]
d=pd.concat([pd.read_csv(f) for f in files],ignore_index=True)
d=d.drop_duplicates(["day","window_pt"],keep="last")
d=d.where(pd.notna(d),None); data=json.dumps(d.to_dict("records"),separators=(",",":"))
html=open("scripts/trade_page_template.html").read().replace("__DATA__",data).replace("__NOTE__",note)
open(out,"w").write(html); print("rows",len(d))
