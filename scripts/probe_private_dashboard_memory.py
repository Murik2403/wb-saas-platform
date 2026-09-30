"""Read-only API stress against an existing private restored container, never live."""
import argparse
import importlib.util
import json
import subprocess
import threading
import time
from urllib.parse import quote

PATHS = ["/api/v1/dashboard/production", "/api/overview?period=30d&compare_previous=false", "/api/v1/dashboard/today", "/api/v1/dashboard/finance?period=30d", "/api/v1/manufacturing-costs"]

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--container", required=True)
    parser.add_argument("--deploy-helper", required=True)
    args = parser.parse_args()
    spec = importlib.util.spec_from_file_location("release_helper", args.deploy_helper)
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    name = quote(args.container, safe="")
    inspected = helper.docker_api("GET", f"/containers/{name}/json")
    assert inspected["State"]["Running"]
    assert inspected["HostConfig"]["NetworkMode"] == "none", "Only private offline fixtures"
    assert (inspected["Config"].get("Labels") or {}).get("traefik.enable") == "false"
    assert inspected["HostConfig"]["Memory"] == 1536*1024**2, "Require exact production 1.5 GiB"
    assert args.container.startswith("release-rehearsal-"), "Only disposable restored fixture names"
    code = r"""
import json,time,urllib.request
from concurrent.futures import ThreadPoolExecutor
paths=PATHS_VALUE
warm='/api/v1/dashboard/finance?period=custom&start=2026-09-01&end=2026-09-19'
def probe(path):
    started=time.monotonic()
    try:
        response=urllib.request.urlopen('http://127.0.0.1:8001'+path,timeout=120)
        content=response.read()
        json.loads(content)
        return {'path':path,'status':response.status,'bytes':len(content),'seconds':round(time.monotonic()-started,3)}
    except Exception as exc:
        return {'path':path,'error':type(exc).__name__,'seconds':round(time.monotonic()-started,3)}
prewarm=probe(warm)
print(json.dumps({'prewarm':prewarm}),flush=True)
assert prewarm.get('status')==200
with ThreadPoolExecutor(max_workers=len(paths)) as pool:
    results=list(pool.map(probe,paths))
print(json.dumps({'parallel':results}),flush=True)
assert all(row.get('status')==200 for row in results)
""".replace("PATHS_VALUE", repr(PATHS))
    peak = 0
    samples = 0
    done = threading.Event()
    def monitor():
        nonlocal peak, samples
        while not done.is_set():
            try:
                stats = helper.docker_api("GET", f"/containers/{name}/stats?stream=false")
                peak=max(peak, int(stats.get("memory_stats",{}).get("usage",0)))
                samples+=1
            except Exception:
                pass
            done.wait(0.2)
    thread = threading.Thread(target=monitor, daemon=True)
    thread.start()
    try:
        run = subprocess.run(["docker","exec",args.container,"python","-c",code],capture_output=True,text=True,timeout=300)
    finally:
        done.set()
        thread.join(timeout=10)
    final = helper.docker_api("GET", f"/containers/{name}/json")
    output={'memory_limit_bytes':1536*1024**2,'observed_peak_bytes':peak,'samples':samples,'probe_returncode':run.returncode,'running':final['State']['Running'],'oom_killed':final['State'].get('OOMKilled',False),'exit_code':final['State'].get('ExitCode'),'restart_count':final.get('RestartCount',0),'api_evidence':run.stdout.splitlines()}
    print(json.dumps(output),flush=True)
    assert run.returncode==0 and final['State']['Running'] and not final['State'].get('OOMKilled'), 'Private exact-limit stress failed'

if __name__=='__main__':
    main()
