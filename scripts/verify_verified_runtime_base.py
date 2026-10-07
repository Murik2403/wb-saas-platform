"""Fail closed before building from a previously verified immutable runtime.

Prepare a public hash manifest in the clean local Git checkout. Verify it on the
Docker host against the exact image, without production mounts or credentials.
"""
from __future__ import annotations
import argparse, hashlib, json, re, shlex, subprocess
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

# Reviewed additive adapters only. This does not authorize runtime dependency,
# Nginx, entrypoint, USER, package installation or arbitrary Dockerfile changes.
APPROVED_ADAPTERS = frozenset({'product_groups_api','rnp_api','fbs_api','self_buyouts_api',
    'dashboard_work_guard','marketplace_context_api','ozon_cabinet_api','ozon_prices_api',
    'ozon_normalization_api','ozon_insights_api','ozon_ads_api','gateway_request_auth'})


class RuntimeBaseError(ValueError):
    """A mandatory release check failed (also under python -O)."""


def require(condition, message):
    if not condition:
        raise RuntimeBaseError(message)


def build_contract(source):
    lines=source.decode().splitlines(keepends=True)
    start=next((i for i,line in enumerate(lines) if line.strip()=='COPY tenant-app/ ./'),-1)
    end=next((i for i,line in enumerate(lines) if line.strip()=='COPY --from=frontend-build /frontend/dist /usr/share/nginx/html'),-1)
    require(0<=start<end,'Missing runtime COPY boundaries')
    modules=set();normalized=[]
    for number,line in enumerate(lines):
        parts=shlex.split(line) if line.startswith('COPY ') else []
        names=[]
        if len(parts)>=3 and parts[0]=='COPY':
            sources=parts[1:-1]
            for name in sources:
                match=re.fullmatch(r'mh-redesign-lab/tenant-api/([a-z_]+)\.py',name)
                if not match or match[1] not in APPROVED_ADAPTERS:break
                names.append(match[1])
            else:
                valid_target=parts[-1]=='./' or len(names)==1 and parts[-1]=='./'+names[0]+'.py'
                require(valid_target and start<number<end,'Invalid approved adapter COPY target or stage')
                require(not (modules&set(names)) and len(set(names))==len(names),'Duplicate runtime adapter COPY')
                modules.update(names)
                continue
        # The frontend is rebuilt in either path: align its supported Node24
        # builder with CI, while preserving the exact Python runtime contract.
        normalized.append(line.replace('FROM node:24-alpine AS frontend-build','FROM node:22-alpine AS frontend-build'))
    return ''.join(normalized).encode(),modules


def overlay_dockerfile(original):
    require(original.count('FROM python:3.12-slim')==1 and 'COPY tenant-app/ ./' in original,'Unsupported full build layout')
    return ('# Use only after verify_verified_runtime_base.py passes for an immutable base.\n'
            'ARG VERIFIED_TENANT_RUNTIME_BASE\n'+original.split('FROM python:3.12-slim')[0]+
            'FROM ${VERIFIED_TENANT_RUNTIME_BASE}\nWORKDIR /app/tenant-app\n\n'+original[original.index('COPY tenant-app/ ./'):].replace('COPY --from=frontend-build /frontend/dist /usr/share/nginx/html', 'RUN python -c "import shutil; from pathlib import Path; p=Path(\'/usr/share/nginx/html\'); shutil.rmtree(p); p.mkdir()"\nCOPY --from=frontend-build /frontend/dist /usr/share/nginx/html'))

def run(*args, **kwargs):
    return subprocess.check_output(list(args), **kwargs)

def prepare(root, output):
    def git(*args): return run('git', '-C', str(root), *args)
    require(not git('status','--porcelain').strip(), 'Committed clean source required')
    revision=git('rev-parse','HEAD').decode().strip()
    require(re.fullmatch('[0-9a-f]{40}', revision), 'Invalid candidate revision')
    hashes={}
    for source, destination in INPUTS.items():
        old=git('show',BASE_REVISION+':'+source)
        current=git('show',revision+':'+source)
        if source == 'mh-redesign-lab/Dockerfile.tenant-staging':
            old_contract,old_modules=build_contract(old)
            current_contract,current_modules=build_contract(current)
            require(old_modules<=current_modules,'Removed runtime adapter COPY')
            require(current_contract==old_contract,'Changed runtime build contract')
        else:
            require(current==old, 'Changed runtime input: '+source)
        if destination: hashes[destination]=hashlib.sha256(current).hexdigest()
    deleted=git('diff','--name-only','--diff-filter=D',BASE_REVISION,revision,'--','tenant-app','mh-redesign-lab/tenant-api').strip()
    require(not deleted, 'Deleted runtime source needs a full clean build')
    original=git('show',revision+':mh-redesign-lab/Dockerfile.tenant-staging').decode()
    expected=overlay_dockerfile(original)
    actual=git('show',revision+':mh-redesign-lab/Dockerfile.tenant-verified-base').decode()
    require(actual==expected, 'Build file differs from reviewed full COPY contract')
    output.write_text(json.dumps(dict(base_image=BASE_IMAGE,base_reference=BASE_REFERENCE,base_revision=BASE_REVISION,
                                     candidate_revision=revision,hashes=hashes),indent=2)+'\n')
    print('VERIFIED_BASE_SOURCE_MANIFEST_OK '+revision)

def verify(manifest):
    p=json.loads(manifest.read_text())
    require(isinstance(p,dict),'Invalid manifest')
    require(p.get('base_image')==BASE_IMAGE and p.get('base_revision')==BASE_REVISION,'Unapproved immutable base')
    require(p.get('base_reference')==BASE_REFERENCE,'Unapproved immutable digest reference')
    require(isinstance(p.get('candidate_revision'),str) and re.fullmatch('[0-9a-f]{40}',p['candidate_revision']),'Invalid candidate revision')
    require(isinstance(p.get('hashes'),dict) and set(p['hashes'])=={v for v in INPUTS.values() if v},'Invalid runtime hash paths')
    require(all(isinstance(v,str) and re.fullmatch('[0-9a-f]{64}',v) for v in p['hashes'].values()),'Invalid runtime hash values')
    info=json.loads(run('docker','image','inspect',BASE_IMAGE))[0]
    require(info.get('Id')==BASE_IMAGE,'Inspected image identity mismatch')
    require(BASE_REFERENCE in info.get('RepoDigests',[]), 'Digest reference missing from local image store')
    require((info.get('Config',{}).get('Labels') or {}).get('org.opencontainers.image.revision')==BASE_REVISION,'Base image revision mismatch')
    code='import hashlib,json,sys\nfrom pathlib import Path\np=json.load(sys.stdin)\nif not all(hashlib.sha256(Path(k).read_bytes()).hexdigest()==v for k,v in p.items()):\n raise RuntimeError("Runtime byte verification failed")\nprint("VERIFIED_BASE_IMAGE_BYTES_OK")'
    result=subprocess.run(['docker','run','--rm','-i','--network','none','--read-only',
                           '--cap-drop','ALL','--security-opt','no-new-privileges',
                           '--memory','128m','--pids-limit','32','--entrypoint','python',BASE_IMAGE,'-c',code],
                          input=json.dumps(p['hashes']),text=True,capture_output=True,timeout=40)
    require(result.returncode==0, 'Exact runtime bytes could not be verified')
    print(result.stdout.strip())

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('mode',choices=['prepare','verify'])
    parser.add_argument('--root',type=Path,default=Path.cwd());parser.add_argument('--manifest',type=Path,required=True)
    args=parser.parse_args()
    if args.mode=='prepare':prepare(args.root.resolve(),args.manifest)
    else:verify(args.manifest)
