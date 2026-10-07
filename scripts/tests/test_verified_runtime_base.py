"""A reused image must never silently carry changed/deleted runtime inputs."""
import importlib.util, json, subprocess, sys
from pathlib import Path
import pytest

ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('runtime_base_gate',ROOT/'scripts/verify_verified_runtime_base.py')
gate=importlib.util.module_from_spec(spec);spec.loader.exec_module(gate)

def git(root,*args):
    return subprocess.check_output(['git','-C',str(root),*args],stderr=subprocess.DEVNULL).decode().strip()

@pytest.fixture
def candidate(tmp_path,monkeypatch):
    root=tmp_path/'checkout';root.mkdir()
    git(root,'init','-q');git(root,'config','user.email','fixture@invalid');git(root,'config','user.name','Fixture')
    git(root,'config','core.autocrlf','false')
    for source in gate.INPUTS:
        path=root/source;path.parent.mkdir(parents=True,exist_ok=True)
        path.write_bytes((ROOT/source).read_bytes().replace(b'\r\n',b'\n'))
    worker=root/'mh-redesign-lab/tenant-api/fixture_runtime.py'
    worker.parent.mkdir(parents=True,exist_ok=True);worker.write_text('ORIGINAL = True\n')
    git(root,'add','.');git(root,'commit','-qm','base')
    monkeypatch.setattr(gate,'BASE_REVISION',git(root,'rev-parse','HEAD'))
    original=(root/'mh-redesign-lab/Dockerfile.tenant-staging').read_text()
    (root/'mh-redesign-lab/Dockerfile.tenant-verified-base').write_bytes(gate.overlay_dockerfile(original).encode())
    git(root,'add','.');git(root,'commit','-qm','candidate')
    return root,tmp_path/'manifest.json',worker

def test_unchanged_committed_inputs_make_exact_hash_manifest(candidate):
    root,output,_=candidate;gate.prepare(root,output)
    p=json.loads(output.read_text())
    assert p['base_image']==gate.BASE_IMAGE
    assert p['candidate_revision']==git(root,'rev-parse','HEAD')
    assert '/app/tenant-app/requirements.lock' in p['hashes']

def test_changed_runtime_dependencies_refuse_reused_image(candidate):
    root,output,_=candidate
    with (root/'tenant-app/requirements.txt').open('a') as f:f.write('\nnew-library==1\n')
    git(root,'add','.');git(root,'commit','-qm','changed dependency')
    with pytest.raises(gate.RuntimeBaseError,match='Changed runtime input'):gate.prepare(root,output)
    assert not output.exists()

def test_deleted_module_refuses_overlay_leftovers(candidate):
    root,output,worker=candidate;worker.unlink()
    git(root,'add','-A');git(root,'commit','-qm','deleted module')
    with pytest.raises(gate.RuntimeBaseError,match='Deleted runtime source'):gate.prepare(root,output)
    assert not output.exists()

def test_mutable_base_identity_rejected_before_docker(tmp_path,monkeypatch):
    path=tmp_path/'manifest.json';path.write_text(json.dumps({'base_image':'mh-tenant:latest','base_revision':gate.BASE_REVISION}))
    monkeypatch.setattr(gate,'run',lambda *a,**kw:pytest.fail('Unverified manifest reached Docker'))
    with pytest.raises(gate.RuntimeBaseError):gate.verify(path)


def test_python_optimized_mode_cannot_bypass_manifest_gate(tmp_path):
    path=tmp_path/'manifest.json';path.write_text(json.dumps({'base_image':'mh-tenant:latest','base_revision':gate.BASE_REVISION}))
    result=subprocess.run([sys.executable,'-O',str(ROOT/'scripts/verify_verified_runtime_base.py'),'verify','--manifest',str(path)],capture_output=True,text=True)
    assert result.returncode!=0 and 'Unapproved immutable base' in result.stderr


@pytest.mark.parametrize('mutation',["RUN echo unsafe\n", "COPY mh-redesign-lab/tenant-api/unknown.py ./\n"])
def test_arbitrary_build_changes_are_rejected(candidate,mutation):
    root,output,_=candidate;dockerfile=root/'mh-redesign-lab/Dockerfile.tenant-staging'
    dockerfile.write_text(dockerfile.read_text().replace('COPY tenant-app/ ./','COPY tenant-app/ ./\n'+mutation))
    git(root,'add','.');git(root,'commit','-qm','unreviewed build change')
    with pytest.raises(gate.RuntimeBaseError,match='Changed runtime build contract'):gate.prepare(root,output)
    assert not output.exists()


def test_removing_existing_adapter_copy_cannot_leave_stale_base_code(candidate):
    root,output,_=candidate;dockerfile=root/'mh-redesign-lab/Dockerfile.tenant-staging'
    dockerfile.write_text(dockerfile.read_text().replace('COPY mh-redesign-lab/tenant-api/rnp_api.py ./rnp_api.py\n',''))
    git(root,'add','.');git(root,'commit','-qm','removed adapter COPY')
    with pytest.raises(gate.RuntimeBaseError,match='Removed runtime adapter COPY'):gate.prepare(root,output)


def test_optimized_prepare_still_rejects_changed_build_contract(candidate):
    root,output,_=candidate;dockerfile=root/'mh-redesign-lab/Dockerfile.tenant-staging'
    dockerfile.write_bytes(dockerfile.read_bytes().replace(b'COPY tenant-app/ ./',b'COPY tenant-app/ ./\nRUN echo unreviewed'))
    git(root,'add','.');git(root,'commit','-qm','unsafe build instruction')
    code="import importlib.util,pathlib;spec=importlib.util.spec_from_file_location('gate',"+repr(str(ROOT/'scripts/verify_verified_runtime_base.py'))+");gate=importlib.util.module_from_spec(spec);spec.loader.exec_module(gate);gate.BASE_REVISION="+repr(gate.BASE_REVISION)+";gate.prepare(pathlib.Path("+repr(str(root))+"),pathlib.Path("+repr(str(output))+"))"
    result=subprocess.run([sys.executable,'-O','-c',code],capture_output=True,text=True)
    assert result.returncode!=0 and 'Changed runtime build contract' in result.stderr
    assert not output.exists()


def test_embedded_image_byte_gate_is_not_removed_by_python_optimization(tmp_path,monkeypatch):
    manifest=tmp_path/'manifest.json'
    manifest.write_text(json.dumps({'base_image':gate.BASE_IMAGE,'base_revision':gate.BASE_REVISION,'base_reference':gate.BASE_REFERENCE,
        'candidate_revision':'a'*40,'hashes':{path:'0'*64 for path in gate.INPUTS.values() if path}}))
    monkeypatch.setattr(gate,'run',lambda *args,**kwargs:json.dumps([{'Id':gate.BASE_IMAGE,'RepoDigests':[gate.BASE_REFERENCE],'Config':{'Labels':{'org.opencontainers.image.revision':gate.BASE_REVISION}}}]).encode())
    source=tmp_path/'runtime-input';source.write_text('changed bytes')
    real_run=subprocess.run
    def optimized_inner(args,**kwargs):
        return real_run([sys.executable,'-O','-c',args[-1]],input=json.dumps({str(source):'0'*64}),text=True,capture_output=True)
    monkeypatch.setattr(gate.subprocess,'run',optimized_inner)
    with pytest.raises(gate.RuntimeBaseError,match='Exact runtime bytes'):gate.verify(manifest)
