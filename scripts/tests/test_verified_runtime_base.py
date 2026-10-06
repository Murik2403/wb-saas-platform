"""A reused image must never silently carry changed/deleted runtime inputs."""
import importlib.util, json, subprocess
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
    (root/'mh-redesign-lab/Dockerfile.tenant-verified-base').write_bytes((ROOT/'mh-redesign-lab/Dockerfile.tenant-verified-base').read_bytes().replace(b'\r\n',b'\n'))
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
    with pytest.raises(RuntimeError,match='Changed runtime input'):gate.prepare(root,output)
    assert not output.exists()

def test_deleted_module_refuses_overlay_leftovers(candidate):
    root,output,worker=candidate;worker.unlink()
    git(root,'add','-A');git(root,'commit','-qm','deleted module')
    with pytest.raises(RuntimeError,match='Deleted runtime source'):gate.prepare(root,output)
    assert not output.exists()

def test_mutable_base_identity_rejected_before_docker(tmp_path,monkeypatch):
    path=tmp_path/'manifest.json';path.write_text(json.dumps({'base_image':'mh-tenant:latest','base_revision':gate.BASE_REVISION}))
    monkeypatch.setattr(gate,'run',lambda *a,**kw:pytest.fail('Unverified manifest reached Docker'))
    with pytest.raises(RuntimeError):gate.verify(path)


def test_added_module_copy_allowed_but_other_additions_refused(candidate):
    root,output,_=candidate
    docker=root/'mh-redesign-lab/Dockerfile.tenant-staging'
    text=docker.read_bytes().decode()
    anchor='COPY mh-redesign-lab/tenant-api/rnp_api.py ./rnp_api.py\n'
    assert anchor in text
    docker.write_bytes(text.replace(anchor,anchor+'COPY mh-redesign-lab/tenant-api/a_api.py mh-redesign-lab/tenant-api/b_api.py ./\n').encode())
    stripped=gate._strip_added_module_copies(text.encode(),docker.read_bytes())
    assert stripped==text.encode()
    bad=docker.read_bytes().decode().replace(anchor,anchor+'COPY tenant-app/evil.py ./\n')
    assert gate._strip_added_module_copies(text.encode(),bad.encode())!=text.encode()


def test_gate_refuses_without_assert_statements():
    import ast
    tree=ast.parse((ROOT/'scripts/verify_verified_runtime_base.py').read_text(encoding='utf-8'))
    assert not [n for n in ast.walk(tree) if isinstance(n,ast.Assert)]
