"""Python-allocation peak (tracemalloc) of each stage of calculations.build_dashboard, split at the table reads and the financial/FIFO calls.

    WB_SAAS_DATA_DIR=<dir> python scripts/profile_dashboard_stages.py
"""
import sys,os,json,tracemalloc,time
from pathlib import Path
sys.path.insert(0,str(Path(os.environ.get("WB_SAAS_APP_DIR") or Path(__file__).resolve().parents[1]/"tenant-app")))
from datetime import date,timedelta
import calculations
end=date.today();start=end-timedelta(days=29)
marks=[];last=["start"]
def mark(label):
    cur,peak=tracemalloc.get_traced_memory()
    marks.append({"segment":f"{last[0]} -> {label}","peak_mib":round(peak/2**20),"after_mib":round(cur/2**20)})
    last[0]=label; tracemalloc.reset_peak()
def wrap(name):
    fn=getattr(calculations,name)
    def w(*a,**k):
        mark("enter "+name+(":"+a[0] if a and isinstance(a[0],str) else ""))
        try: return fn(*a,**k)
        finally: mark("exit "+name)
    setattr(calculations,name,w)
for n in ("read_table","read_financial","read_financial_fifo_reconciliation","project_historical_cogs","summarize_financial_fifo_reconciliation"):
    wrap(n)
tracemalloc.start()
calculations.build_dashboard(start,end)
mark("end")
for m in marks:
    print(json.dumps(m)) if m["peak_mib"]>=190 or "enter read_table:orders" in m["segment"] else None
