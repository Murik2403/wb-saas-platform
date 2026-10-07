from pathlib import Path
import yaml

def test_edge_gateway_and_tenants_cannot_reach_writable_docker_proxy():
    root=Path(__file__).resolve().parents[2]
    compose=yaml.safe_load((root/'docker-compose.yml').read_text());services=compose['services']
    def networks(name):return set(services[name]['networks'])
    assert networks('docker-socket-proxy')=={'docker_control_net'}
    assert networks('docker-discovery-proxy')=={'docker_discovery_net'}
    assert networks('provisioner')=={'control_api_net','docker_control_net'}
    assert networks('gateway')=={'wbsaas_net','control_api_net'}
    assert networks('traefik')=={'wbsaas_net','docker_discovery_net'}
    assert 'POST=0' in services['docker-discovery-proxy']['environment']
    assert 'POST=1' in services['docker-socket-proxy']['environment']
    assert services['provisioner']['networks']['control_api_net']['aliases']==['provisioner']
    assert 'docker-discovery-proxy' in (root/'traefik/traefik.yml').read_text()
    assert 'WB_SAAS_PROVISIONER_BEARER_KEY' not in str(services.get('tenant',{}))

def test_frontend_staging_is_loopback_nonroot_and_readonly():
    root=Path(__file__).resolve().parents[2]
    service=yaml.safe_load((root/'mh-redesign-lab/docker-compose.staging.yml').read_text())['services']['redesign-frontend']
    assert service['ports']==['127.0.0.1:8501:8501'] and service['user']=='10001:10001'
    assert service['read_only'] and service['cap_drop']==['ALL']
