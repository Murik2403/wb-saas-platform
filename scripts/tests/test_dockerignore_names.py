"""BuildKit reads <Dockerfile>.dockerignore; any other name is silently ignored."""
from pathlib import Path

LAB=Path(__file__).resolve().parents[2]/'mh-redesign-lab'

def test_every_dockerfile_has_sibling_dockerignore():
    dockerfiles=sorted(LAB.glob('Dockerfile.*'))
    assert dockerfiles
    dockerfiles=[d for d in dockerfiles if not d.name.endswith('.dockerignore')]
    missing=[d.name for d in dockerfiles if not (LAB/(d.name+'.dockerignore')).is_file()]
    assert not missing, missing
    assert not (LAB/'.dockerignore.tenant-staging').exists()
