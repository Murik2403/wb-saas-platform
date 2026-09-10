from unittest.mock import Mock, patch
import provisioning
import config
from internal_auth import verify_credential


def test_container_gets_scoped_key_and_requested_image():
    client = Mock()
    with patch.object(config, "INTERNAL_API_SECRET", "test-master"), patch.object(provisioning, "ensure_tenant_network"), patch.object(provisioning, "_attach_traefik"), patch.object(provisioning, "ensure_volume", return_value=Mock(name="volume")):
        provisioning._run_tenant_container(client, "shop-a", "container-a", image="test-image:v2")
    args, kwargs = client.containers.run.call_args
    assert args[0] == "test-image:v2"
    token = kwargs["environment"]["WB_SAAS_INTERNAL_API_SECRET"]
    assert token != "test-master"
    assert verify_credential("test-master", "shop-a", token)
    assert not verify_credential("test-master", "shop-b", token)
