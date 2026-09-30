import {beforeEach,describe,expect,it,vi} from 'vitest';
import {WarehouseSubmission,warehouseRequest,warehouseWorkspace} from './shared-warehouse';
const transport=vi.hoisted(()=>({currentShop:vi.fn(),scopedFetch:vi.fn()}));
vi.mock('./selected-shop',()=>transport);
vi.mock('./data-source',()=>({API_BASE_URL:'',apiHeaders:()=>({'x-tenant-id':'synthetic'})}));
beforeEach(()=>{vi.resetAllMocks();transport.currentShop.mockReturnValue({id:2,verified_identity:'a'.repeat(64),snapshot_revision:4});});
describe('shared warehouse request provenance',()=>{
 it('uses selected target and normal scoped transport',async()=>{transport.scopedFetch.mockResolvedValue(new Response(JSON.stringify({shop_id:2}),{status:200}));await expect(warehouseWorkspace()).resolves.toEqual({shop_id:2});expect(transport.scopedFetch.mock.calls[0][0]).toBe('/api/v1/shared-warehouse/2/workspace');});
 it('preserves exact failed-action key and snapshots without caller mutation',()=>{const submit=new WarehouseSubmission(),body={quantity:3,document_date:'2026-09-01'};const first=submit.snapshot('/2/reserve/confirm',body);first.body.quantity=99;const retry=submit.snapshot('/2/reserve/confirm',body);expect(retry.key).toBe(first.key);expect(retry.body.quantity).toBe(3);expect(submit.snapshot('/2/reserve/confirm',{...body,quantity:4}).key).not.toBe(first.key);});
 it('invalidates receipt after shop revision or identity change',()=>{const submit=new WarehouseSubmission(),body={quantity:3};const first=submit.snapshot('/2/reserve/confirm',body);transport.currentShop.mockReturnValue({id:2,verified_identity:'a'.repeat(64),snapshot_revision:5});expect(submit.snapshot('/2/reserve/confirm',body).key).not.toBe(first.key);});
 it('renders structured conflict message and passes explicit UUID',async()=>{transport.scopedFetch.mockResolvedValue(new Response(JSON.stringify({detail:{code:'WAREHOUSE_CONFLICT',message:'Партия изменилась'}}),{status:409}));await expect(warehouseRequest('/2/reserve/confirm',{quantity:3},'receipt-uuid')).rejects.toThrow('Партия изменилась');expect(transport.scopedFetch.mock.calls[0][1].headers['X-MH-Request-Key']).toBe('receipt-uuid');});
 it('refuses a read before validated selected context',()=>{transport.currentShop.mockReturnValue(null);expect(()=>warehouseWorkspace()).toThrow('Магазин не подтверждён');});
});
