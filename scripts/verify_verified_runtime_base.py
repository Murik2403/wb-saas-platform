"""Fail closed before building from a previously verified immutable runtime.

Prepare a public hash manifest in the clean local Git checkout. Verify it on the
Docker host against the exact image, without production mounts or credentials.
"""
from __future__ import annotations
import argparse, hashlib, json, re, subprocess
from pathlib import Path

BASE_REVISION = 'ef616ed1712df28fd001f04a76b343eac386844b'
BASE_IMAGE = 'sha256:8ae1f0d9bd63a129ad6aee8bbf5d823742643d269876dc766409520180239115'
BASE_REFERENCE = 'mh-tenant@'+BASE_IMAGE
INPUTS = {
    'tenant-app/requirements.txt': '/app/tenant-app/requirements.txt',
    'tenant-app/requirements.lock': '/app/tenant-app/requirements.lock',
    'tenant-app/docker-entrypoint.sh': '/app/tenant-app/docker-entrypoint.sh',
    'mh-redesign-lab/deploy/tenant-staging-entrypoint.sh': '/usr/local/bin/tenant-staging-entrypoint',
    'mh-redesign-lab/deploy/nginx.tenant-staging.conf': '/etc/nginx/conf.d/default.conf',
    'mh-redesign-lab/Dockerfile.tenant-staging': None,
}

def need(condition, message):
    """Explicit gate check: unlike assert, survives python -O."""
    if not condition:
        raise RuntimeError(message)

_MODULE_COPY = re.compile(r'COPY ((?:mh-redesign-lab/tenant-api/\w+\.py )+)(\./\w+\.py|\./)')

def _strip_added_module_copies(old, current):
    """Drop COPY lines (new vs old) that only add tenant-api modules."""
    remaining = old.split(b'\n')
    kept = []
    for line in current.split(b'\n'):
        if line in remaining:
            remaining.remove(line); kept.append(line); continue
        m = _MODULE_COPY.fullmatch(line.decode('utf-8', 'replace').rstrip('\r'))
        if m:
            sources = m.group(1).split()
            dest = m.group(2)
            if dest == './' or (len(sources) == 1 and dest == './' + sources[0].rsplit('/', 1)[1]):
                continue
        kept.append(line)
    return b'\n'.join(kept)

def run(*args, **kwargs):
    return subprocess.check_output(list(args), **kwargs)

def prepare(root, output):
    def git(*args): return run('git', '-C', str(root), *args)
    need(not git('status','--porcelain').strip(), 'Committed clean source required')
    revision=git('rev-parse','HEAD').decode().strip()
    need(re.fullmatch('[0-9a-f]{40}', revision), 'Verification failed')
    hashes={}
    for source, destination in INPUTS.items():
        old=git('show',BASE_REVISION+':'+source)
        current=git('show',revision+':'+source)
        if source == 'mh-redesign-lab/Dockerfile.tenant-staging':
            # Only additive application-module COPY lines are approved here; all
            # dependency, entrypoint, nginx and runtime instructions stay exact.
            need(_strip_added_module_copies(old, current) == old, 'Changed runtime build contract')
        else:
            need(current==old, 'Changed runtime input: '+source)
        if destination: hashes[destination]=hashlib.sha256(current).hexdigest()
    deleted=git('diff','--name-only','--diff-filter=D',BASE_REVISION,revision,'--','tenant-app','mh-redesign-lab/tenant-api').strip()
    need(not deleted, 'Deleted runtime source needs a full clean build')
    original=git('show',revision+':mh-redesign-lab/Dockerfile.tenant-staging').decode()
    expected=('# Use only after verify_verified_runtime_base.py passes for an immutable base.\n'
              'ARG VERIFIED_TENANT_RUNTIME_BASE\n'+original.split('FROM python:3.12-slim')[0]+
              'FROM ${VERIFIED_TENANT_RUNTIME_BASE}\nWORKDIR /app/tenant-app\n\n'+original[original.index('COPY tenant-app/ ./'):].replace('COPY --from=frontend-build /frontend/dist /usr/share/nginx/html', 'RUN python -c "import shutil; from pathlib import Path; p=Path(\'/usr/share/nginx/html\'); shutil.rmtree(p); p.mkdir()"\nCOPY --from=frontend-build /frontend/dist /usr/share/nginx/html'))
    actual=git('show',revision+':mh-redesign-lab/Dockerfile.tenant-verified-base').decode()
    need(actual==expected, 'Build file differs from reviewed full COPY contract')
    output.write_text(json.dumps(dict(base_image=BASE_IMAGE,base_reference=BASE_REFERENCE,base_revision=BASE_REVISION,
                                     candidate_revision=revision,hashes=hashes),indent=2)+'\n')
    print('VERIFIED_BASE_SOURCE_MANIFEST_OK '+revision)

def verify(manifest):
    p=json.loads(manifest.read_text())
    need(p['base_image']==BASE_IMAGE and p['base_revision']==BASE_REVISION, 'Verification failed')
    need(p['base_reference']==BASE_REFERENCE, 'Verification failed')
    need(re.fullmatch('[0-9a-f]{40}',p['candidate_revision']), 'Verification failed')
    need(set(p['hashes'])=={v for v in INPUTS.values() if v}, 'Verification failed')
    need(all(re.fullmatch('[0-9a-f]{64}',v) for v in p['hashes'].values()), 'Verification failed')
    info=json.loads(run('docker','image','inspect',BASE_IMAGE))[0]
    need(info['Id']==BASE_IMAGE, 'Verification failed')
    need(BASE_REFERENCE in info.get('RepoDigests',[]), 'Digest reference missing from local image store')
    need(info['Config']['Labels']['org.opencontainers.image.revision']==BASE_REVISION, 'Verification failed')
    code='import hashlib,json,sys;from pathlib import Path;p=json.load(sys.stdin);assert all(hashlib.sha256(Path(k).read_bytes()).hexdigest()==v for k,v in p.items());print("VERIFIED_BASE_IMAGE_BYTES_OK")'
    result=subprocess.run(['docker','run','--rm','-i','--network','none','--read-only',
                           '--cap-drop','ALL','--security-opt','no-new-privileges',
                           '--memory','128m','--pids-limit','32','--entrypoint','python',BASE_IMAGE,'-c',code],
                          input=json.dumps(p['hashes']),text=True,capture_output=True,timeout=40)
    need(result.returncode==0, 'Exact runtime bytes could not be verified')
    print(result.stdout.strip())

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('mode',choices=['prepare','verify'])
    parser.add_argument('--root',type=Path,default=Path.cwd());parser.add_argument('--manifest',type=Path,required=True)
    args=parser.parse_args()
    if args.mode=='prepare':prepare(args.root.resolve(),args.manifest)
    else:verify(args.manifest)
