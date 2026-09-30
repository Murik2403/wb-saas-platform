import sqlite3
import pytest
from tests.test_shared_warehouse import database,layer,mapping,reserve
from db import core,manufacturing as manufacturing,shared_warehouse as warehouse
from db.shared_manufacturing import totals

def specification():
    with core.connect() as conn:
        conn.execute("INSERT INTO products_catalog(nm_id,product_name,raw_json) VALUES(200,'Assembly','{\"sizes\":[] }')")
        conn.execute("INSERT INTO material_inventory_color(material_key,material_name,unit) VALUES('cloth','Cloth','м')")
    manufacturing.save_bom_version(100,[{'material_name':'Cloth','quantity_per_unit':1,'unit':'м'}],chrt_id=10)
    return manufacturing.save_bom_version(200,[{'component_type':'semi_finished','nm_id':100,'chrt_id':10,'quantity_per_unit':1,'unit':'шт'}])

def test_mapped_intermediate_reservation_and_fragmented_manufacturing_cost_conserve():
    identifier=layer(6,cost=.335);mapping();specification()
    claimed=reserve(quantity=3)
    releases=[manufacturing.post_manufacturing_release(200,1,'2026-09-10',f'assembly-{i}') for i in range(3)]
    assert [row['material_cost_rub'] for row in releases]==[.33,.34,.33]
    with core.connect() as conn:
        assert totals(conn,identifier)==(3,1.0)
        assert conn.execute('SELECT ready_units FROM finished_goods_cost_layers WHERE id=?',(identifier,)).fetchone()[0]==3
        amount=conn.execute('SELECT SUM(cost_rub) FROM warehouse_reservation_layers WHERE reservation_id=?',(claimed['id'],)).fetchone()[0]
        assert amount==1.01 and round(amount+sum(r['material_cost_rub'] for r in releases),2)==2.01
        for sql in ('UPDATE warehouse_manufacturing_inputs SET units=units','DELETE FROM warehouse_manufacturing_inputs','INSERT OR REPLACE INTO warehouse_manufacturing_inputs SELECT * FROM warehouse_manufacturing_inputs'):
            with pytest.raises(sqlite3.IntegrityError):conn.execute(sql)
    with pytest.raises(ValueError):manufacturing.post_manufacturing_release(200,1,'2026-09-10','shortage')
    with core.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM inventory_movements WHERE source_task_key='shortage'").fetchone()[0]==0
        assert conn.execute("SELECT COUNT(*) FROM manufacturing_releases WHERE source_key='shortage'").fetchone()[0]==0

def test_manufacturing_reversal_restores_exact_input_basis_before_next_shop_reserve():
    identifier=layer(3,cost=.335);mapping();specification()
    release=manufacturing.post_manufacturing_release(200,1,'2026-09-10','first')
    with core.connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        result=manufacturing._undo_manufacturing_conn(conn,release['movement_id'])
        assert result['ok'],result
    with core.connect() as conn:assert totals(conn,identifier)==(0,0.0)
    reservation=reserve(shop=1,quantity=3,key='after-undo')
    with core.connect() as conn:
        assert conn.execute('SELECT SUM(cost_rub) FROM warehouse_reservation_layers WHERE reservation_id=?',(reservation['id'],)).fetchone()[0]==1.01

def test_manufacturing_does_not_consume_free_quarantine_or_foreign_reservation():
    identifier=layer(4,cost=5);mapping();specification();reserve(shop=1,quantity=2)
    from db import incident_workflow
    from db.wb_incidents import create_wb_incident_case
    case=create_wb_incident_case('damaged','2026-09-10','','proof','')
    incident_workflow.hold(case,identifier,'ready',1,'hold-free','owner')
    result=manufacturing.post_manufacturing_release(200,1,'2026-09-10','only-free')
    assert result['material_cost_rub']==5
    with pytest.raises(ValueError):manufacturing.post_manufacturing_release(200,1,'2026-09-10','no-more-free')
    with core.connect() as conn:
        assert conn.execute('SELECT ready_units FROM finished_goods_cost_layers WHERE id=?',(identifier,)).fetchone()[0]==3

def test_reserved_output_blocks_reversal_and_duplicate_release_does_not_consume_again():
    identifier=layer(3,cost=.335);mapping();specification()
    release=manufacturing.post_manufacturing_release(200,1,'2026-09-10','final')
    repeated=manufacturing.post_manufacturing_release(200,1,'2026-09-10','final')
    assert repeated['id']==release['id'] and repeated['posted']==0
    item=warehouse.register_item(200,0,'Assembly');warehouse.map_sku(1,888,0,item['id'],'owner@test')
    preview=warehouse.preview(1,888,0,1,'2026-09-10','owner@test')
    reservation=warehouse.reserve(1,888,0,1,'2026-09-10','output-order','output-reserve',preview['fingerprint'],'owner@test')
    with core.connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        result=manufacturing._undo_manufacturing_conn(conn,release['movement_id'])
        assert not result['ok']
        assert totals(conn,identifier)==(1,.34)
        assert conn.execute('SELECT COUNT(*) FROM warehouse_manufacturing_inputs').fetchone()[0]==1
        assert conn.execute('SELECT ready_units FROM finished_goods_cost_layers WHERE id=?',(identifier,)).fetchone()[0]==2
    warehouse.transition(reservation['id'],'release','cancel-order','output-cancel','2026-09-10',reservation['revision'],'owner@test')
    with core.connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        assert manufacturing._undo_manufacturing_conn(conn,release['movement_id'])['ok']
        assert not manufacturing._undo_manufacturing_conn(conn,release['movement_id'])['ok']
        assert totals(conn,identifier)==(0,0.0)
        assert conn.execute('SELECT COUNT(*) FROM warehouse_manufacturing_inputs').fetchone()[0]==2
