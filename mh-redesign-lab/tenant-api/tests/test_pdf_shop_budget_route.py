"""Real private child returns retryable PDF budget refusal without committing its copy."""
import uuid
import base64
import pytest
from test_shop_workspaces_api import client, shops

def test_secondary_pdf_busy_is_503_retryable_and_original_snapshot_is_untouched(client,monkeypatch,tmp_path):
    shop=shops.create('secondary PDF','unused-token','pdf-seller')
    before=shops.read(shop)
    bad=tmp_path/'unavailable-mount';bad.write_bytes(b'dummy')
    monkeypatch.setenv('WB_SAAS_PDF_SLOT_DIR',str(bad/'slots'))
    monkeypatch.setenv('WB_SAAS_PDF_SLOTS','2')
    response=client.post('/api/v1/operations/reports/quick',
        headers={'x-mh-shop-id':str(shop),'x-mh-shop-revision':'0','x-mh-request-key':str(uuid.uuid4())},
        json={'name':'private busy report','days':2,'metrics':['stocks']})
    assert response.status_code==503,response.text
    assert response.headers['retry-after']=='30'
    assert shops.read(shop)==before

@pytest.mark.parametrize('retry',['30\r\nInjected: yes','999'])
def test_private_response_refuses_unsafe_retry_header(retry):
    from shop_dispatch import response_of
    result={'status':503,'body':base64.b64encode(b'{"detail":"busy"}').decode(),
            'content_type':'application/json','retry_after':retry}
    response=response_of(result,0,None)
    assert 'retry-after' not in response.headers
