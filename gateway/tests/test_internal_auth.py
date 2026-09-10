from internal_auth import tenant_credential, verify_credential


def test_credentials_cannot_cross_tenants():
    token = tenant_credential("test-master", "shop-a")
    assert verify_credential("test-master", "shop-a", token)
    assert not verify_credential("test-master", "shop-b", token)
    assert not verify_credential("test-master", "shop-a", "test-master")
    assert not verify_credential("", "shop-a", "")
    assert not verify_credential("test-master", "../shop-a", token)
    assert not verify_credential("rotated-master", "shop-a", token)
