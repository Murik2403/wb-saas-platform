import sys, sqlite3
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0,str(Path(__file__).resolve().parents[1]));sys.path.insert(0,str(Path(__file__).resolve().parent))
import pytest
from base import DbTestCase
from db.core import connect
from db.wb_incidents import create_wb_incident_case
from db.incident_workflow import attach, evidence, hold, release, workspace, save_details
from db.fifo_finished_goods import _create_finished_goods_layer,_move_finished_goods_fifo,_reverse_finished_goods_source_layer

class WorkflowTests(DbTestCase):
    def seed(self,location='wb'):
        case=create_wb_incident_case('Проверка','2026-09-01',document_ref='акт')
        with connect() as conn:
            layer=_create_finished_goods_layer(conn,10,'a','p','opening','source','2026-01-01',10,12.5,location)
        return case,layer
    def test_hold_blocks_fifo_and_sql_decrements_then_release_restores(self):
        case,layer=self.seed();identifier=hold(case,layer,'wb',8,'request-one','owner')
        with connect() as conn:
            with pytest.raises(ValueError,match='QUARANTINE'):_move_finished_goods_fifo(conn,1,10,'a','p',3,'wb',None)
            with pytest.raises(sqlite3.IntegrityError,match='QUARANTINE'):conn.execute('UPDATE finished_goods_cost_layers SET wb_units=7 WHERE id=?',(layer,))
            amount,_=_move_finished_goods_fifo(conn,1,10,'a','p',2,'wb',None)
            assert amount==25
        release(case,identifier,'Проверка завершена','manager')
        with connect() as conn:
            amount,_=_move_finished_goods_fifo(conn,2,10,'a','p',8,'wb',None)
            assert amount==100
    def test_duplicate_and_conflicting_request(self):
        case,layer=self.seed();first=hold(case,layer,'wb',3,'request-one','owner')
        assert hold(case,layer,'wb',3,'request-one','owner')==first
        with pytest.raises(ValueError):hold(case,layer,'wb',4,'request-one','owner')
        assert len(workspace(case)['holds'])==1
    def test_concurrent_holds_cannot_overreserve(self):
        case,layer=self.seed()
        def attempt(key):
            try:return hold(case,layer,'wb',7,key,'owner')
            except ValueError:return None
        with ThreadPoolExecutor(max_workers=2) as executor:results=list(executor.map(attempt,['request-a','request-b']))
        assert sum(row is not None for row in results)==1
    def test_journal_and_evidence_are_append_only_and_deduplicated(self):
        case,_=self.seed();save_details(case,'Иван','2026-10-01','Проверить акт','owner');save_details(case,'Иван','2026-10-01','Проверить акт','owner')
        identifier=attach(case,'акт.txt','text/plain','доказательство'.encode(),'owner')
        assert attach(case,'copy.txt','text/plain','доказательство'.encode(),'owner')==identifier
        assert evidence(case,identifier)['content']=='доказательство'.encode()
        report=workspace(case);assert len(report['history'])==2
        with connect() as conn:
            with pytest.raises(sqlite3.IntegrityError):conn.execute('DELETE FROM incident_workflow_history')
            with pytest.raises(sqlite3.IntegrityError):conn.execute('UPDATE incident_evidence SET content=?',(b'corrupt',))
    def test_unsafe_files_and_invalid_size_rejected_without_journal(self):
        case,_=self.seed()
        for filename,mime,data in [('../x.txt','text/plain',b'x'),('x.html','text/html',b'<script>'),('x.pdf','application/pdf',b'notpdf'),('x.txt','text/plain',b'x'*2097153)]:
            with pytest.raises(ValueError):attach(case,filename,mime,data,'owner')
        assert workspace(case)['evidence']==[]
    def test_holds_are_local_not_financial_movements(self):
        case,layer=self.seed('ready');hold(case,layer,'ready',2,'request-one','owner')
        with connect() as conn:
            assert conn.execute('SELECT COUNT(*) FROM inventory_movements').fetchone()[0]==0
            assert conn.execute('SELECT COUNT(*) FROM wb_incident_loss_lines').fetchone()[0]==0
            assert conn.execute('SELECT ready_units FROM finished_goods_cost_layers WHERE id=?',(layer,)).fetchone()[0]==10
    def test_hold_release_idempotent_and_wrong_case_rejected(self):
        case,layer=self.seed();identifier=hold(case,layer,'wb',3,'request-one','owner')
        other=create_wb_incident_case('Другой','2026-09-01')
        with pytest.raises(ValueError):release(other,identifier,'акт','owner')
        release(case,identifier,'акт','owner');release(case,identifier,'акт','owner')
        assert [r['action'] for r in workspace(case)['history']]==['quarantine','release']
    def test_variant_hold_does_not_block_other_variant(self):
        case,layer=self.seed();hold(case,layer,'wb',10,'request-one','owner')
        with connect() as conn:
            _create_finished_goods_layer(conn,10,'a','p','opening','variant','2026-01-01',2,20,'wb',chrt_id=7)
            amount,_=_move_finished_goods_fifo(conn,1,10,'a','p',2,'wb',None,chrt_id=7)
            assert amount==40

    def test_closure_requires_outcome_and_released_quarantine(self):
        case,layer=self.seed();identifier=hold(case,layer,'wb',3,'request-one','owner')
        with pytest.raises(ValueError):save_details(case,'Иван',None,'Решено','owner','closed')
        release(case,identifier,'Сверено','owner')
        with pytest.raises(ValueError):save_details(case,'Иван',None,'','owner','closed')
        save_details(case,'Иван',None,'Решено','owner','closed')
        assert workspace(case)['details']['status']=='closed'
        with pytest.raises(ValueError):hold(case,layer,'wb',1,'request-two','owner')

    def test_native_sqlite_backup_preserves_blobs_history_and_holds(self):
        case,layer=self.seed();identifier=attach(case,'proof.txt','text/plain',b'proof','owner');hold(case,layer,'wb',4,'request-one','owner')
        with connect() as source:
            backup=sqlite3.connect(str(self.db_path.parent/'backup.sqlite3'))
            try:
                source.backup(backup)
                assert backup.execute('SELECT content FROM incident_evidence WHERE id=?',(identifier,)).fetchone()[0]==b'proof'
                assert backup.execute('SELECT COUNT(*) FROM incident_workflow_history').fetchone()[0]==2
                assert backup.execute("SELECT units FROM incident_holds WHERE status='held'").fetchone()[0]==4
                with pytest.raises(sqlite3.IntegrityError):backup.execute('UPDATE finished_goods_cost_layers SET wb_units=0')
            finally:backup.close()

    def test_direct_movement_guard_becomes_domain_error_and_rolls_back(self):
        case,layer=self.seed();hold(case,layer,'wb',4,'request-one','owner')
        with pytest.raises(ValueError,match='QUARANTINE'):
            with connect() as conn:
                conn.execute('UPDATE finished_goods_cost_layers SET wb_units=0 WHERE id=?',(layer,))
        with connect() as conn:assert conn.execute('SELECT wb_units FROM finished_goods_cost_layers WHERE id=?',(layer,)).fetchone()[0]==10
