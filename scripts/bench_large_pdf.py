"""Full PDF acceptance on a COPY of a synthetic database, local Linux Docker only.

Uses 768 MiB, no swap, 2 CPUs, no network or published ports. Does not start
nginx/API: the PDF parent and real report subprocess share this cgroup.
"""
from __future__ import annotations
import argparse
from contextlib import closing
import hashlib
import json
import shutil
import sqlite3
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

WORKER = r'''import json,time,hashlib,traceback,sys,io
from pathlib import Path
from datetime import date,timedelta
sys.path.insert(0,"/app/tenant-app")
from reports.pdf_builder import _render_report_pdf
from reports.metrics import METRIC_BUILDERS
from config import DB_PATH
import sqlite3

def memory():
    return {name:Path('/sys/fs/cgroup/'+name).read_text().strip() for name in ['memory.peak','memory.current','memory.max','memory.swap.max','memory.events']}
with sqlite3.connect(DB_PATH) as c:
    last=c.execute('select max(operation_date) from financial_report').fetchone()[0]
end=date.fromisoformat(last[:10]); start=end-timedelta(days=int(sys.argv[1])-1)
r={'period':[str(start),str(end)],'metric_codes':list(METRIC_BUILDERS),'before':memory()}
t=time.monotonic()
try:
    pdf=_render_report_pdf('Large synthetic PDF',list(METRIC_BUILDERS),start,end)
    Path('/work/full.pdf').write_bytes(pdf)
    r.update(ok=True,bytes=len(pdf),sha256=hashlib.sha256(pdf).hexdigest())
except BaseException as e:
    r.update(ok=False,error=str(e),traceback=traceback.format_exc())
r.update(seconds=round(time.monotonic()-t,2),after=memory())
Path('/work/result.json').write_text(json.dumps(r,indent=2))
print(json.dumps(r),flush=True)
if not r['ok']:sys.exit(1)
'''


def docker(*args, check=True):
    done=subprocess.run(['docker',*args],capture_output=True,text=True,timeout=60)
    if check and done.returncode:
        raise RuntimeError(done.stderr.strip())
    return done


def events(text):
    return {key:int(value) for key,value in (line.split() for line in text.splitlines())}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image',required=True)
    parser.add_argument('--database',type=Path,required=True)
    parser.add_argument('--days',type=int,default=30)
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args()
    if args.days<1:parser.error('days must be positive')
    context=docker('context','inspect').stdout
    endpoint=json.loads(context)[0]['Endpoints']['docker']['Host']
    if not (endpoint.startswith('npipe:') or endpoint.startswith('unix:')):
        raise RuntimeError('local Docker endpoint required')
    if docker('info','--format','{{.OSType}}').stdout.strip()!='linux':
        raise RuntimeError('Linux Docker required')
    image=json.loads(docker('image','inspect',args.image).stdout)[0]
    source=args.database.resolve(strict=True)
    with closing(sqlite3.connect(source.as_uri()+'?mode=ro',uri=True)) as conn:
        if conn.execute('PRAGMA quick_check').fetchone()[0]!='ok':raise RuntimeError('source quick_check failed')
        if not conn.execute("SELECT value FROM app_meta WHERE key='synthetic_scale_copies'").fetchone():
            raise RuntimeError('only explicitly marked synthetic databases accepted')
        counts={t:conn.execute('SELECT count(*) FROM '+t).fetchone()[0] for t in ('orders','sales','financial_report','products_catalog')}
    run=uuid.uuid4().hex
    name='codex-largepdf-'+run[:12]
    act={'run_id':run,'image_id':image['Id'],'image_labels':image['Config'].get('Labels'),
         'database_bytes':source.stat().st_size,'database_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),
         'row_counts':counts,'days':args.days,'memory_limit':768*2**20,'swap_limit':0,'cpus':2,
         'scope':'full PDF parent + report worker; no resident API/nginx or concurrent reports',
         'endpoint':endpoint,'cleanup_confirmed':False,'pass':False}
    args.out.parent.mkdir(parents=True,exist_ok=True)
    container=None
    try:
        with tempfile.TemporaryDirectory(prefix='codex-largepdf-') as tmp:
            folder=Path(tmp); (folder/'input').mkdir()
            # Online SQLite backup: never copy a live DB without its WAL.
            with closing(sqlite3.connect(source.as_uri()+'?mode=ro',uri=True)) as src, closing(sqlite3.connect(folder/'input/wb_dashboard.sqlite3')) as dst:
                src.backup(dst)
            (folder/'worker.py').write_text(WORKER,encoding='utf-8')
            container=docker('create','--name',name,'--label','codex.largepdf='+run,'--network','none',
                '--memory','768m','--memory-swap','768m','--cpus','2',
                '--mount',f'type=bind,src={folder},dst=/work',
                '-e','WB_SAAS_DATA_DIR=/work/input','-e','WB_SAAS_REPORT_ISOLATED_METRICS=1',
                '-e','MPLBACKEND=Agg','--entrypoint','python',image['Id'],'/work/worker.py',str(args.days)).stdout.strip()
            act['container_id']=container
            docker('start',container)
            until=time.monotonic()+660
            while True:
                inspect=json.loads(docker('inspect',container).stdout)[0]
                if not inspect['State']['Running']:break
                if time.monotonic()>until:raise TimeoutError('full PDF exceeded 660 seconds')
                time.sleep(1)
            act['state']=inspect['State']
            act['effective_host_config']={k:inspect['HostConfig'][k] for k in ('Memory','MemorySwap','NanoCpus','NetworkMode','PortBindings')}
            act['logs']=docker('logs',container).stdout
            if (folder/'result.json').exists():
                act['result']=json.loads((folder/'result.json').read_text())
            result=act.get('result',{})
            if (folder/'full.pdf').exists():
                target=args.out.with_suffix('.pdf')
                shutil.copyfile(folder/'full.pdf',target)
                from pypdf import PdfReader
                reader=PdfReader(str(target))
                result['pages']=len(reader.pages)
                result['extracted_text_chars']=sum(len(page.extract_text()) for page in reader.pages)
                result['pdf_sha256_verified']=hashlib.sha256(target.read_bytes()).hexdigest()==result.get('sha256')
            before=events(result.get('before',{}).get('memory.events',''))
            after=events(result.get('after',{}).get('memory.events',''))
            act['memory_events_delta']={k:after[k]-before[k] for k in after if k in before}
            act['pass']=bool(result.get('ok') and len(result.get('metric_codes',[]))==21 and
                result.get('pages',0)>0 and result.get('extracted_text_chars',0)>0 and result.get('pdf_sha256_verified') and
                inspect['State']['ExitCode']==0 and not inspect['State']['OOMKilled'] and
                int(result['after']['memory.peak'])<=768*2**20 and
                int(result['after']['memory.max'])==768*2**20 and int(result['after']['memory.swap.max'])==0 and
                all(act['memory_events_delta'].get(k,-1)==0 for k in ('high','max','oom','oom_kill')))
    except BaseException as exc:
        act.update(error=f'{type(exc).__name__}: {exc}',**{'pass':False})
    finally:
        if container:
            try:
                info=json.loads(docker('inspect',container).stdout)[0]
                if info['Id']!=container or info['Name']!='/'+name or info['Config']['Labels'].get('codex.largepdf')!=run:
                    raise RuntimeError('cleanup identity mismatch')
                docker('rm','-f',container)
                absent=docker('inspect',container,check=False)
                act['cleanup_confirmed']=absent.returncode!=0 and 'no such object' in absent.stderr.lower()
            except BaseException as exc:
                act['cleanup_error']=str(exc)
        act['pass']=act['pass'] and act['cleanup_confirmed']
        args.out.write_text(json.dumps(act,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'pass':act['pass'],'out':str(args.out),'cleanup':act['cleanup_confirmed'],
                      'result':act.get('result'),'error':act.get('error')}))
    return 0 if act['pass'] else 1


if __name__=='__main__':raise SystemExit(main())
