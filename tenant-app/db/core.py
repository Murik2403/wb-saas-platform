from __future__ import annotations

import json
import math
import sqlite3
import hashlib
from pathlib import Path
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Iterable
from zoneinfo import ZoneInfo

import pandas as pd

from config import DB_PATH


@contextmanager
def connect():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    # synchronous is a per-connection setting, unlike journal_mode -- it does
    # NOT persist in the database file, so init_db()'s one-time
    # "PRAGMA synchronous=NORMAL" only ever applied to that single init
    # connection. Every other connection (all ~200+ call sites across the
    # app) silently fell back to SQLite's FULL default, fsyncing on every
    # commit despite WAL mode making NORMAL safe. Set it here so it actually
    # applies everywhere.
    conn.execute("PRAGMA synchronous=NORMAL")
    try:
        yield conn
        conn.commit()
    except sqlite3.IntegrityError as exc:
        if "SHARED_WAREHOUSE:" in str(exc):
            from .shared_warehouse import WarehouseStockError
            raise WarehouseStockError('Изменение слоя нарушает резерв, принадлежность магазина или историю') from exc
        if "QUARANTINE:" in str(exc):
            raise ValueError("QUARANTINE: движение заблокировано карантином; сначала снимите резерв инцидента.") from exc
        raise
    finally:
        conn.close()

def init_db() -> None:
    from .fifo_sales import _initialize_sales_fifo_baseline_if_needed
    with connect() as conn:
        conn.executescript(
            """
            PRAGMA journal_mode=WAL;

            CREATE TABLE IF NOT EXISTS orders (
                id TEXT PRIMARY KEY,
                order_date TEXT,
                last_change TEXT,
                nm_id INTEGER,
                supplier_article TEXT,
                barcode TEXT,
                brand TEXT,
                subject TEXT,
                warehouse_name TEXT,
                total_price REAL DEFAULT 0,
                finished_price REAL DEFAULT 0,
                price_with_disc REAL DEFAULT 0,
                is_cancel INTEGER DEFAULT 0,
                cancel_date TEXT,
                raw_json TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_orders_date ON orders(order_date);
            CREATE INDEX IF NOT EXISTS idx_orders_nm ON orders(nm_id);

            CREATE TABLE IF NOT EXISTS sales (
                id TEXT PRIMARY KEY,
                sale_date TEXT,
                last_change TEXT,
                nm_id INTEGER,
                supplier_article TEXT,
                barcode TEXT,
                brand TEXT,
                subject TEXT,
                warehouse_name TEXT,
                sale_id TEXT,
                total_price REAL DEFAULT 0,
                finished_price REAL DEFAULT 0,
                price_with_disc REAL DEFAULT 0,
                for_pay REAL DEFAULT 0,
                is_return INTEGER DEFAULT 0,
                raw_json TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_sales_date ON sales(sale_date);
            CREATE INDEX IF NOT EXISTS idx_sales_nm ON sales(nm_id);

            CREATE TABLE IF NOT EXISTS stocks (
                snapshot_at TEXT NOT NULL,
                nm_id INTEGER NOT NULL,
                chrt_id INTEGER NOT NULL DEFAULT 0,
                warehouse_id INTEGER NOT NULL DEFAULT 0,
                warehouse_name TEXT,
                region_name TEXT,
                quantity INTEGER DEFAULT 0,
                in_way_to_client INTEGER DEFAULT 0,
                in_way_from_client INTEGER DEFAULT 0,
                raw_json TEXT NOT NULL,
                PRIMARY KEY (snapshot_at, nm_id, chrt_id, warehouse_id)
            );
            CREATE INDEX IF NOT EXISTS idx_stocks_snapshot ON stocks(snapshot_at);
            CREATE INDEX IF NOT EXISTS idx_stocks_nm ON stocks(nm_id);

            CREATE TABLE IF NOT EXISTS ads_daily (
                campaign_id INTEGER NOT NULL,
                day TEXT NOT NULL,
                nm_id INTEGER NOT NULL DEFAULT 0,
                product_name TEXT,
                views INTEGER DEFAULT 0,
                clicks INTEGER DEFAULT 0,
                spend REAL DEFAULT 0,
                atbs INTEGER DEFAULT 0,
                orders_count INTEGER DEFAULT 0,
                canceled INTEGER DEFAULT 0,
                shks INTEGER DEFAULT 0,
                ad_revenue REAL DEFAULT 0,
                raw_json TEXT NOT NULL,
                PRIMARY KEY (campaign_id, day, nm_id)
            );
            CREATE INDEX IF NOT EXISTS idx_ads_day ON ads_daily(day);
            CREATE INDEX IF NOT EXISTS idx_ads_nm ON ads_daily(nm_id);

            CREATE TABLE IF NOT EXISTS settings_change_history (
                id INTEGER PRIMARY KEY,
                kind TEXT NOT NULL CHECK(kind IN ('cost','production_norm')),
                nm_id INTEGER NOT NULL CHECK(nm_id > 0),
                before_json TEXT,
                after_json TEXT NOT NULL,
                changed_at TEXT NOT NULL,
                source TEXT NOT NULL,
                actor TEXT,
                reason TEXT CHECK(reason IS NULL OR length(reason)<=1000)
            );
            CREATE INDEX IF NOT EXISTS idx_settings_history_kind_nm_time
                ON settings_change_history(kind,nm_id,changed_at,id);
            CREATE TRIGGER IF NOT EXISTS settings_history_no_replace
                BEFORE INSERT ON settings_change_history
                WHEN EXISTS(SELECT 1 FROM settings_change_history WHERE id=NEW.id)
                BEGIN SELECT RAISE(ABORT, 'settings history is immutable'); END;
            CREATE TRIGGER IF NOT EXISTS settings_history_no_update
                BEFORE UPDATE ON settings_change_history BEGIN
                    SELECT RAISE(ABORT, 'settings history is immutable');
                END;
            CREATE TRIGGER IF NOT EXISTS settings_history_no_delete
                BEFORE DELETE ON settings_change_history BEGIN
                    SELECT RAISE(ABORT, 'settings history is immutable');
                END;

            CREATE TABLE IF NOT EXISTS costs (
                nm_id INTEGER PRIMARY KEY,
                supplier_article TEXT,
                cost_per_wb_unit REAL DEFAULT 0,
                auto_material_cost INTEGER DEFAULT 0,
                material_cost_rub REAL DEFAULT 0,
                packaging_cost_rub REAL DEFAULT 0,
                packaging_cost_status TEXT NOT NULL DEFAULT 'unknown',
                packaging_cost_source TEXT NOT NULL DEFAULT 'unknown',
                packaging_cost_confirmed_at TEXT,
                labor_cost_rub REAL DEFAULT 0,
                other_cost_rub REAL DEFAULT 0,
                forecast_material_cost_rub REAL DEFAULT 0,
                forecast_total_cost_rub REAL DEFAULT 0,
                forecast_rate_rub_m REAL DEFAULT 0,
                forecast_source TEXT DEFAULT '',
                note TEXT,
                updated_at TEXT
            );

            CREATE TABLE IF NOT EXISTS manual_expenses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nm_id INTEGER NOT NULL,
                article TEXT NOT NULL,
                amount_rub REAL NOT NULL CHECK (amount_rub >= 0),
                allocation_basis TEXT NOT NULL DEFAULT 'per_unit',
                source TEXT NOT NULL DEFAULT 'manual',
                period_start TEXT NOT NULL,
                period_end TEXT NOT NULL,
                note TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                CHECK (allocation_basis IN ('per_unit','total','revenue_pct')),
                CHECK (source IN ('manual','import')),
                CHECK (period_end >= period_start)
            );
            CREATE INDEX IF NOT EXISTS idx_manual_expenses_nm_period
                ON manual_expenses(nm_id, period_start, period_end);

            CREATE TABLE IF NOT EXISTS manual_store_expenses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                article TEXT NOT NULL CHECK (article IN (
                    'purchase_production','wb_commission','logistics_returns','storage',
                    'acceptance','advertising','packaging_labor','defects_losses','taxes'
                )),
                amount_rub REAL NOT NULL CHECK (amount_rub >= 0),
                source TEXT NOT NULL CHECK (source IN ('manual','import')),
                period_start TEXT NOT NULL,
                period_end TEXT NOT NULL,
                note TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                include_in_management_result INTEGER NOT NULL DEFAULT 0
                    CHECK (include_in_management_result IN (0,1)),
                row_fingerprint TEXT,
                CHECK (period_end >= period_start)
            );
            CREATE INDEX IF NOT EXISTS idx_manual_store_expenses_period
                ON manual_store_expenses(period_start, period_end);

            CREATE TABLE IF NOT EXISTS recurring_store_expense_templates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                article TEXT NOT NULL CHECK (article IN (
                    'purchase_production','wb_commission','logistics_returns','storage',
                    'acceptance','advertising','packaging_labor','defects_losses','taxes'
                )),
                amount_rub REAL NOT NULL CHECK (amount_rub >= 0),
                note TEXT NOT NULL DEFAULT '',
                active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0,1)),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_recurring_store_expense_templates_active
                ON recurring_store_expense_templates(active, id);

            CREATE TABLE IF NOT EXISTS recurring_store_expense_occurrences (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                template_id INTEGER NOT NULL,
                period_start TEXT NOT NULL,
                expense_id INTEGER UNIQUE,
                status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','cancelled')),
                created_at TEXT NOT NULL,
                UNIQUE(template_id, period_start),
                FOREIGN KEY(template_id) REFERENCES recurring_store_expense_templates(id) ON DELETE RESTRICT
            );
            CREATE INDEX IF NOT EXISTS idx_recurring_store_expense_occurrences_period
                ON recurring_store_expense_occurrences(period_start, template_id);

            CREATE TABLE IF NOT EXISTS store_expense_import_batches (
                id TEXT PRIMARY KEY,
                content_sha256 TEXT NOT NULL UNIQUE CHECK (length(content_sha256)=64),
                status TEXT NOT NULL DEFAULT 'preview' CHECK (status IN ('preview','confirmed')),
                rows_json TEXT NOT NULL,
                result_json TEXT,
                created_at TEXT NOT NULL,
                confirmed_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_store_expense_import_batches_status
                ON store_expense_import_batches(status, created_at);

            CREATE TABLE IF NOT EXISTS products_catalog (
                nm_id INTEGER PRIMARY KEY,
                supplier_article TEXT,
                product_name TEXT,
                brand TEXT,
                subject_name TEXT,
                updated_at TEXT,
                raw_json TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_products_catalog_article ON products_catalog(supplier_article);

            CREATE TABLE IF NOT EXISTS production_settings (
                nm_id INTEGER PRIMARY KEY,
                supplier_article TEXT,
                enabled INTEGER DEFAULT 0,
                material_per_unit REAL DEFAULT 0,
                target_days INTEGER DEFAULT 21,
                min_batch INTEGER DEFAULT 1,
                note TEXT,
                blank_type TEXT DEFAULT '',
                pack_size INTEGER DEFAULT 4,
                auto_rules INTEGER DEFAULT 0,
                material_name TEXT DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS material_inventory (
                material_key TEXT PRIMARY KEY,
                material_name TEXT NOT NULL,
                blank_type TEXT DEFAULT '',
                balance_known INTEGER DEFAULT 0,
                full_rolls INTEGER DEFAULT 0,
                partial_meters REAL DEFAULT 0,
                roll_length REAL DEFAULT 25.5,
                note TEXT,
                updated_at TEXT
            );

            CREATE TABLE IF NOT EXISTS material_inventory_color (
                material_key TEXT PRIMARY KEY,
                material_name TEXT NOT NULL,
                balance_known INTEGER DEFAULT 0,
                full_rolls INTEGER DEFAULT 0,
                partial_meters REAL DEFAULT 0,
                roll_length REAL DEFAULT 25.5,
                unit TEXT DEFAULT 'м',
                tracking_mode TEXT DEFAULT 'packaged',
                opening_rate_rub REAL DEFAULT 0,
                note TEXT,
                updated_at TEXT
            );

            CREATE TABLE IF NOT EXISTS material_cost_layers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                material_key TEXT NOT NULL,
                material_name TEXT NOT NULL,
                source_type TEXT NOT NULL DEFAULT 'opening',
                source_ref TEXT NOT NULL,
                source_date TEXT,
                original_meters REAL NOT NULL DEFAULT 0,
                remaining_meters REAL NOT NULL DEFAULT 0,
                unit_cost_rub_m REAL NOT NULL DEFAULT 0,
                original_amount_rub REAL NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'active',
                note TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(source_type,source_ref)
            );
            CREATE INDEX IF NOT EXISTS idx_material_cost_layers_fifo
                ON material_cost_layers(material_key,status,source_date,id);

            CREATE TABLE IF NOT EXISTS material_fifo_consumptions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                movement_id INTEGER NOT NULL,
                layer_id INTEGER NOT NULL,
                meters REAL NOT NULL DEFAULT 0,
                amount_rub REAL NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'applied',
                created_at TEXT NOT NULL,
                reversed_at TEXT,
                FOREIGN KEY(movement_id) REFERENCES inventory_movements(id),
                FOREIGN KEY(layer_id) REFERENCES material_cost_layers(id)
            );
            CREATE INDEX IF NOT EXISTS idx_material_fifo_consumptions_movement
                ON material_fifo_consumptions(movement_id,status);

            CREATE TABLE IF NOT EXISTS wip_blank_batches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                batch_key TEXT NOT NULL UNIQUE,
                issue_movement_id INTEGER UNIQUE,
                completion_movement_id INTEGER UNIQUE,
                batch_date TEXT,
                nm_id INTEGER NOT NULL DEFAULT 0,
                chrt_id INTEGER NOT NULL DEFAULT 0,
                material_key TEXT NOT NULL,
                material_name TEXT NOT NULL,
                blank_type TEXT NOT NULL,
                issued_meters REAL NOT NULL DEFAULT 0,
                material_cost_rub REAL NOT NULL DEFAULT 0,
                produced_units INTEGER NOT NULL DEFAULT 0,
                scrap_units INTEGER NOT NULL DEFAULT 0,
                remaining_units INTEGER NOT NULL DEFAULT 0,
                unit_cost_rub REAL NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'open',
                note TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                reversed_at TEXT,
                FOREIGN KEY(issue_movement_id) REFERENCES inventory_movements(id),
                FOREIGN KEY(completion_movement_id) REFERENCES inventory_movements(id)
            );
            CREATE INDEX IF NOT EXISTS idx_wip_blank_batches_fifo
                ON wip_blank_batches(material_key,blank_type,status,batch_date,id);

            CREATE TABLE IF NOT EXISTS wip_blank_allocations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                movement_id INTEGER NOT NULL,
                batch_id INTEGER NOT NULL,
                units INTEGER NOT NULL DEFAULT 0,
                amount_rub REAL NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'applied',
                created_at TEXT NOT NULL,
                reversed_at TEXT,
                FOREIGN KEY(movement_id) REFERENCES inventory_movements(id),
                FOREIGN KEY(batch_id) REFERENCES wip_blank_batches(id)
            );
            CREATE INDEX IF NOT EXISTS idx_wip_blank_alloc_movement
                ON wip_blank_allocations(movement_id,status);
            CREATE INDEX IF NOT EXISTS idx_wip_blank_alloc_batch
                ON wip_blank_allocations(batch_id,status);

            CREATE TABLE IF NOT EXISTS production_cost_batches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                movement_id INTEGER NOT NULL UNIQUE,
                nm_id INTEGER NOT NULL DEFAULT 0,
                supplier_article TEXT DEFAULT '',
                product_name TEXT DEFAULT '',
                batch_date TEXT,
                produced_units INTEGER NOT NULL DEFAULT 0,
                material_key TEXT DEFAULT '',
                material_name TEXT DEFAULT '',
                material_meters REAL NOT NULL DEFAULT 0,
                material_cost_rub REAL NOT NULL DEFAULT 0,
                packaging_cost_rub REAL NOT NULL DEFAULT 0,
                labor_cost_rub REAL NOT NULL DEFAULT 0,
                other_cost_rub REAL NOT NULL DEFAULT 0,
                total_cost_rub REAL NOT NULL DEFAULT 0,
                unit_cost_rub REAL NOT NULL DEFAULT 0,
                chrt_id INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'active',
                note TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                reversed_at TEXT,
                FOREIGN KEY(movement_id) REFERENCES inventory_movements(id)
            );
            CREATE INDEX IF NOT EXISTS idx_production_cost_batches_nm
                ON production_cost_batches(nm_id,batch_date,id);

            CREATE TABLE IF NOT EXISTS finished_goods_cost_layers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nm_id INTEGER NOT NULL DEFAULT 0,
                supplier_article TEXT DEFAULT '',
                product_name TEXT DEFAULT '',
                source_type TEXT NOT NULL DEFAULT 'opening',
                source_ref TEXT NOT NULL,
                source_date TEXT,
                original_units INTEGER NOT NULL DEFAULT 0,
                ready_units INTEGER NOT NULL DEFAULT 0,
                inbound_units INTEGER NOT NULL DEFAULT 0,
                wb_units INTEGER NOT NULL DEFAULT 0,
                unit_cost_rub REAL NOT NULL DEFAULT 0,
                original_amount_rub REAL NOT NULL DEFAULT 0,
                chrt_id INTEGER NOT NULL DEFAULT 0,
                producer_release_id INTEGER,
                status TEXT NOT NULL DEFAULT 'active',
                note TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(source_type,source_ref),
                FOREIGN KEY(producer_release_id) REFERENCES manufacturing_releases(id)
            );
            CREATE INDEX IF NOT EXISTS idx_finished_goods_layers_fifo
                ON finished_goods_cost_layers(nm_id,status,source_date,id);

            CREATE TABLE IF NOT EXISTS finished_goods_fifo_allocations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                movement_id INTEGER NOT NULL,
                layer_id INTEGER NOT NULL,
                units INTEGER NOT NULL DEFAULT 0,
                amount_rub REAL NOT NULL DEFAULT 0,
                from_location TEXT NOT NULL DEFAULT '',
                to_location TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'applied',
                created_at TEXT NOT NULL,
                reversed_at TEXT,
                FOREIGN KEY(movement_id) REFERENCES inventory_movements(id),
                FOREIGN KEY(layer_id) REFERENCES finished_goods_cost_layers(id)
            );
            CREATE INDEX IF NOT EXISTS idx_finished_goods_alloc_movement
                ON finished_goods_fifo_allocations(movement_id,status);
            CREATE INDEX IF NOT EXISTS idx_finished_goods_alloc_layer
                ON finished_goods_fifo_allocations(layer_id,status);

            CREATE TABLE IF NOT EXISTS sales_fifo_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sale_record_id TEXT NOT NULL UNIQUE,
                sale_id TEXT DEFAULT '',
                srid TEXT DEFAULT '',
                nm_id INTEGER NOT NULL DEFAULT 0,
                supplier_article TEXT DEFAULT '',
                product_name TEXT DEFAULT '',
                event_date TEXT,
                event_type TEXT NOT NULL DEFAULT 'sale',
                units INTEGER NOT NULL DEFAULT 1,
                fifo_cost_rub REAL NOT NULL DEFAULT 0,
                unit_cost_rub REAL NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'baseline',
                movement_id INTEGER,
                matched_sale_event_id INTEGER,
                note TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                chrt_id INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY(movement_id) REFERENCES inventory_movements(id),
                FOREIGN KEY(matched_sale_event_id) REFERENCES sales_fifo_events(id)
            );
            CREATE INDEX IF NOT EXISTS idx_sales_fifo_events_date
                ON sales_fifo_events(event_date,nm_id,status);
            CREATE INDEX IF NOT EXISTS idx_sales_fifo_events_srid
                ON sales_fifo_events(srid,nm_id,event_type,status);
            CREATE INDEX IF NOT EXISTS idx_sales_fifo_events_movement
                ON sales_fifo_events(movement_id);

            CREATE TABLE IF NOT EXISTS fifo_reconciliation_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_key TEXT NOT NULL UNIQUE,
                snapshot_at TEXT DEFAULT '',
                run_date TEXT,
                status TEXT NOT NULL DEFAULT 'applied',
                articles INTEGER NOT NULL DEFAULT 0,
                lines INTEGER NOT NULL DEFAULT 0,
                added_units INTEGER NOT NULL DEFAULT 0,
                removed_units INTEGER NOT NULL DEFAULT 0,
                adjustment_amount_rub REAL NOT NULL DEFAULT 0,
                note TEXT DEFAULT '',
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_fifo_reconciliation_runs_created
                ON fifo_reconciliation_runs(created_at,status);

            CREATE TABLE IF NOT EXISTS fifo_reconciliation_lines (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER NOT NULL,
                nm_id INTEGER NOT NULL DEFAULT 0,
                supplier_article TEXT DEFAULT '',
                product_name TEXT DEFAULT '',
                location TEXT NOT NULL DEFAULT 'wb',
                physical_units INTEGER NOT NULL DEFAULT 0,
                layered_units INTEGER NOT NULL DEFAULT 0,
                difference_units INTEGER NOT NULL DEFAULT 0,
                action TEXT NOT NULL DEFAULT '',
                unit_cost_rub REAL NOT NULL DEFAULT 0,
                amount_rub REAL NOT NULL DEFAULT 0,
                movement_id INTEGER,
                layer_id INTEGER,
                status TEXT NOT NULL DEFAULT 'applied',
                note TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                FOREIGN KEY(run_id) REFERENCES fifo_reconciliation_runs(id),
                FOREIGN KEY(movement_id) REFERENCES inventory_movements(id),
                FOREIGN KEY(layer_id) REFERENCES finished_goods_cost_layers(id)
            );
            CREATE INDEX IF NOT EXISTS idx_fifo_reconciliation_lines_run
                ON fifo_reconciliation_lines(run_id,location,status);
            CREATE INDEX IF NOT EXISTS idx_fifo_reconciliation_lines_nm
                ON fifo_reconciliation_lines(nm_id,location);

            -- v6.0: dedicated register for warehouse incidents/losses and later WB compensation.
            CREATE TABLE IF NOT EXISTS wb_incident_cases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                incident_key TEXT NOT NULL UNIQUE,
                incident_name TEXT NOT NULL,
                incident_date TEXT,
                warehouse_label TEXT DEFAULT '',
                document_ref TEXT DEFAULT '',
                status TEXT NOT NULL DEFAULT 'open',
                note TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_wb_incident_cases_date
                ON wb_incident_cases(incident_date,status);

            CREATE TABLE IF NOT EXISTS wb_incident_loss_lines (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                case_id INTEGER NOT NULL,
                nm_id INTEGER NOT NULL DEFAULT 0,
                supplier_article TEXT DEFAULT '',
                product_name TEXT DEFAULT '',
                confirmed_units INTEGER NOT NULL DEFAULT 0,
                fifo_cost_rub REAL NOT NULL DEFAULT 0,
                unit_cost_rub REAL NOT NULL DEFAULT 0,
                movement_id INTEGER,
                status TEXT NOT NULL DEFAULT 'applied',
                note TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                reversed_at TEXT,
                FOREIGN KEY(case_id) REFERENCES wb_incident_cases(id),
                FOREIGN KEY(movement_id) REFERENCES inventory_movements(id)
            );
            CREATE INDEX IF NOT EXISTS idx_wb_incident_loss_case
                ON wb_incident_loss_lines(case_id,status);
            CREATE INDEX IF NOT EXISTS idx_wb_incident_loss_nm
                ON wb_incident_loss_lines(nm_id,status);

            CREATE TABLE IF NOT EXISTS wb_incident_compensations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                case_id INTEGER NOT NULL,
                nm_id INTEGER NOT NULL DEFAULT 0,
                amount_rub REAL NOT NULL DEFAULT 0,
                compensation_date TEXT,
                source_ref TEXT DEFAULT '',
                note TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                FOREIGN KEY(case_id) REFERENCES wb_incident_cases(id)
            );
            CREATE INDEX IF NOT EXISTS idx_wb_incident_comp_case
                ON wb_incident_compensations(case_id,compensation_date,id);

            -- v6.3: verified FBW supplies used as evidence of incoming stock after
            -- the last detailed WB stock snapshot. These records are factual API
            -- evidence only; they do not create FIFO cost layers automatically.
            CREATE TABLE IF NOT EXISTS wb_fbw_supply_confirmations (
                supply_id INTEGER PRIMARY KEY,
                fact_date TEXT DEFAULT '',
                fact_date_msk TEXT DEFAULT '',
                create_date TEXT DEFAULT '',
                status_id INTEGER DEFAULT 0,
                warehouse_name TEXT DEFAULT '',
                accepted INTEGER NOT NULL DEFAULT 0,
                sku_count INTEGER NOT NULL DEFAULT 0,
                units INTEGER NOT NULL DEFAULT 0,
                raw_json TEXT DEFAULT '',
                verified_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_wb_fbw_supply_fact
                ON wb_fbw_supply_confirmations(fact_date_msk,status_id,accepted);

            CREATE TABLE IF NOT EXISTS wb_fbw_supply_goods (
                supply_id INTEGER NOT NULL,
                nm_id INTEGER NOT NULL DEFAULT 0,
                supplier_article TEXT DEFAULT '',
                quantity INTEGER NOT NULL DEFAULT 0,
                raw_json TEXT DEFAULT '',
                verified_at TEXT NOT NULL,
                PRIMARY KEY(supply_id,nm_id),
                FOREIGN KEY(supply_id) REFERENCES wb_fbw_supply_confirmations(supply_id)
            );
            CREATE INDEX IF NOT EXISTS idx_wb_fbw_supply_goods_nm
                ON wb_fbw_supply_goods(nm_id,supply_id);

            -- v6.7: auditable application of the historical FIFO reconstruction.
            -- A consistent SQLite backup is created before any FIFO rows are changed.
            CREATE TABLE IF NOT EXISTS fifo_reconstruction_apply_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_key TEXT NOT NULL UNIQUE,
                version TEXT NOT NULL DEFAULT '6.7',
                baseline_snapshot_at TEXT DEFAULT '',
                current_snapshot_at TEXT DEFAULT '',
                backup_path TEXT DEFAULT '',
                status TEXT NOT NULL DEFAULT 'preparing',
                expected_fifo_units INTEGER NOT NULL DEFAULT 0,
                applied_fifo_units INTEGER NOT NULL DEFAULT 0,
                candidate_loss_units INTEGER NOT NULL DEFAULT 0,
                bridge_units INTEGER NOT NULL DEFAULT 0,
                verified_supply_units INTEGER NOT NULL DEFAULT 0,
                replay_sales INTEGER NOT NULL DEFAULT 0,
                replay_returns INTEGER NOT NULL DEFAULT 0,
                cost_pending_units INTEGER NOT NULL DEFAULT 0,
                reconstructed_cost_rub REAL NOT NULL DEFAULT 0,
                plan_hash TEXT DEFAULT '',
                note TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                applied_at TEXT,
                rolled_back_at TEXT,
                error_text TEXT DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_fifo_reconstruction_apply_runs_status
                ON fifo_reconstruction_apply_runs(status,id);

            CREATE TABLE IF NOT EXISTS product_pipeline (
                nm_id INTEGER PRIMARY KEY,
                supplier_article TEXT,
                local_known INTEGER DEFAULT 0,
                ready_units INTEGER DEFAULT 0,
                inbound_known INTEGER DEFAULT 0,
                inbound_units INTEGER DEFAULT 0,
                inbound_date TEXT,
                note TEXT,
                updated_at TEXT
            );

            CREATE TABLE IF NOT EXISTS production_capacity (
                id INTEGER PRIMARY KEY CHECK(id=1),
                capacity_known INTEGER DEFAULT 0,
                pieces_per_day INTEGER DEFAULT 0,
                workdays TEXT DEFAULT '0,1,2,3,4,5',
                horizon_days INTEGER DEFAULT 14,
                fulfillment_lead_days INTEGER DEFAULT 0,
                emergency_cover_days INTEGER DEFAULT 7,
                expedited_fbo_lead_days INTEGER DEFAULT 3,
                fbs_lead_days INTEGER DEFAULT 0,
                note TEXT,
                updated_at TEXT
            );

            CREATE TABLE IF NOT EXISTS execution_tasks (
                task_key TEXT PRIMARY KEY,
                task_type TEXT NOT NULL,
                task_date TEXT,
                stage TEXT,
                nm_id INTEGER DEFAULT 0,
                chrt_id INTEGER NOT NULL DEFAULT 0,
                supplier_article TEXT,
                product_name TEXT,
                planned_units INTEGER DEFAULT 0,
                actual_units INTEGER DEFAULT 0,
                status TEXT DEFAULT 'Не начато',
                route TEXT DEFAULT '',
                dispatch_date TEXT,
                expected_arrival_date TEXT,
                note TEXT,
                updated_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_execution_tasks_type_date ON execution_tasks(task_type, task_date);
            CREATE INDEX IF NOT EXISTS idx_execution_tasks_nm ON execution_tasks(nm_id);

            CREATE TABLE IF NOT EXISTS inventory_movements (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_key TEXT NOT NULL UNIQUE,
                movement_type TEXT NOT NULL,
                nm_id INTEGER NOT NULL DEFAULT 0,
                supplier_article TEXT,
                product_name TEXT,
                quantity INTEGER NOT NULL DEFAULT 0,
                ready_delta INTEGER NOT NULL DEFAULT 0,
                inbound_delta INTEGER NOT NULL DEFAULT 0,
                route TEXT DEFAULT '',
                movement_date TEXT,
                expected_arrival_date TEXT,
                source_task_key TEXT,
                reference_movement_id INTEGER,
                status TEXT NOT NULL DEFAULT 'applied',
                note TEXT,
                created_at TEXT NOT NULL,
                reversed_at TEXT,
                chrt_id INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY(reference_movement_id) REFERENCES inventory_movements(id)
            );
            CREATE INDEX IF NOT EXISTS idx_inventory_movements_nm ON inventory_movements(nm_id);
            CREATE INDEX IF NOT EXISTS idx_inventory_movements_source ON inventory_movements(source_task_key);
            CREATE INDEX IF NOT EXISTS idx_inventory_movements_created ON inventory_movements(created_at);

            CREATE TABLE IF NOT EXISTS procurement_orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_number TEXT NOT NULL UNIQUE,
                procurement_type TEXT NOT NULL DEFAULT 'Сырьё',
                supplier_name TEXT DEFAULT '',
                supplier_id INTEGER,
                status TEXT NOT NULL DEFAULT 'Запланировано',
                order_date TEXT,
                payment_due_date TEXT,
                paid_date TEXT,
                expected_date TEXT,
                received_date TEXT,
                currency TEXT NOT NULL DEFAULT 'RUB',
                exchange_rate REAL NOT NULL DEFAULT 1,
                note TEXT DEFAULT '',
                source_key TEXT UNIQUE,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_procurement_orders_status ON procurement_orders(status);
            CREATE INDEX IF NOT EXISTS idx_procurement_orders_expected ON procurement_orders(expected_date);

            CREATE TABLE IF NOT EXISTS procurement_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id INTEGER NOT NULL,
                item_type TEXT NOT NULL,
                material_key TEXT DEFAULT '',
                material_name TEXT DEFAULT '',
                nm_id INTEGER DEFAULT 0,
                chrt_id INTEGER NOT NULL DEFAULT 0,
                supplier_article TEXT DEFAULT '',
                product_name TEXT DEFAULT '',
                quantity REAL NOT NULL DEFAULT 0,
                unit TEXT NOT NULL DEFAULT '',
                roll_length REAL DEFAULT 25.5,
                supplier_unit_price REAL DEFAULT 0,
                exchange_rate REAL DEFAULT 1,
                delivery_unit_foreign REAL DEFAULT 0,
                extra_unit_rub REAL DEFAULT 0,
                unit_price REAL DEFAULT 0,
                received_quantity REAL DEFAULT 0,
                posted_quantity REAL DEFAULT 0,
                note TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(order_id) REFERENCES procurement_orders(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_procurement_items_order ON procurement_items(order_id);
            CREATE INDEX IF NOT EXISTS idx_procurement_items_nm ON procurement_items(nm_id);
            CREATE INDEX IF NOT EXISTS idx_procurement_items_material ON procurement_items(material_key);

            CREATE TABLE IF NOT EXISTS suppliers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE COLLATE NOCASE,
                contact_person TEXT DEFAULT '',
                phone TEXT DEFAULT '',
                messenger TEXT DEFAULT '',
                email TEXT DEFAULT '',
                country TEXT DEFAULT '',
                default_currency TEXT NOT NULL DEFAULT 'RUB',
                payment_terms_days INTEGER DEFAULT 3,
                lead_time_days INTEGER DEFAULT 7,
                active INTEGER DEFAULT 1,
                note TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_suppliers_active ON suppliers(active,name);

            CREATE TABLE IF NOT EXISTS procurement_payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id INTEGER NOT NULL,
                payment_date TEXT NOT NULL,
                amount REAL NOT NULL DEFAULT 0,
                method TEXT DEFAULT '',
                note TEXT DEFAULT '',
                status TEXT NOT NULL DEFAULT 'applied',
                created_at TEXT NOT NULL,
                reversed_at TEXT,
                FOREIGN KEY(order_id) REFERENCES procurement_orders(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_procurement_payments_order ON procurement_payments(order_id,status);
            CREATE INDEX IF NOT EXISTS idx_procurement_payments_date ON procurement_payments(payment_date);

            CREATE TABLE IF NOT EXISTS app_meta (
                key TEXT PRIMARY KEY,
                value TEXT DEFAULT '',
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS sync_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                status TEXT NOT NULL,
                details TEXT
            );
            """
        )

        history_columns = {row[1] for row in conn.execute("PRAGMA table_info(settings_change_history)")}
        if "reason" not in history_columns:
            conn.execute("ALTER TABLE settings_change_history ADD COLUMN reason TEXT CHECK(reason IS NULL OR length(reason)<=1000)")

        # Manual expenses were originally all subtracted from management profit.
        # Existing rows cannot be assumed to be additional to WB/ad/COGS data,
        # so the new opt-in column intentionally defaults to excluded.
        manual_expense_columns = {str(row["name"]) for row in conn.execute("PRAGMA table_info(manual_expenses)").fetchall()}
        if "include_in_management_result" not in manual_expense_columns:
            conn.execute(
                "ALTER TABLE manual_expenses ADD COLUMN include_in_management_result INTEGER NOT NULL DEFAULT 0 CHECK (include_in_management_result IN (0,1))"
            )

        # Intermediate deployments may already have store expenses without
        # the explicit P&L opt-in or the idempotent import fingerprint.
        store_expense_columns = {
            str(row["name"])
            for row in conn.execute("PRAGMA table_info(manual_store_expenses)").fetchall()
        }
        if "include_in_management_result" not in store_expense_columns:
            conn.execute(
                "ALTER TABLE manual_store_expenses ADD COLUMN include_in_management_result INTEGER NOT NULL DEFAULT 0 CHECK (include_in_management_result IN (0,1))"
            )
        if "row_fingerprint" not in store_expense_columns:
            conn.execute("ALTER TABLE manual_store_expenses ADD COLUMN row_fingerprint TEXT")
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_manual_store_expenses_import_fingerprint "
            "ON manual_store_expenses(row_fingerprint) WHERE row_fingerprint IS NOT NULL"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_manual_store_expenses_inclusion "
            "ON manual_store_expenses(include_in_management_result, period_start, period_end)"
        )

        # Keep a tombstone when a generated monthly expense is deleted, so a
        # later generation request does not silently recreate it.
        occurrence_columns = {
            str(row["name"]): row
            for row in conn.execute("PRAGMA table_info(recurring_store_expense_occurrences)").fetchall()
        }
        if occurrence_columns and (
            ("expense_id" in occurrence_columns and occurrence_columns["expense_id"]["notnull"])
            or "status" not in occurrence_columns
        ):
            conn.execute("DROP INDEX IF EXISTS idx_recurring_store_expense_occurrences_period")
            conn.execute(
                "ALTER TABLE recurring_store_expense_occurrences "
                "RENAME TO recurring_store_expense_occurrences_legacy"
            )
            conn.execute(
                """CREATE TABLE recurring_store_expense_occurrences (
                       id INTEGER PRIMARY KEY AUTOINCREMENT,
                       template_id INTEGER NOT NULL,
                       period_start TEXT NOT NULL,
                       expense_id INTEGER UNIQUE,
                       status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','cancelled')),
                       created_at TEXT NOT NULL,
                       UNIQUE(template_id,period_start),
                       FOREIGN KEY(template_id) REFERENCES recurring_store_expense_templates(id) ON DELETE RESTRICT
                   )"""
            )
            legacy_columns = {str(row["name"]) for row in conn.execute(
                "PRAGMA table_info(recurring_store_expense_occurrences_legacy)"
            ).fetchall()}
            required_legacy_columns = {"id", "template_id", "period_start", "expense_id", "created_at"}
            if not required_legacy_columns.issubset(legacy_columns):
                raise RuntimeError("Cannot safely migrate recurring expense occurrences: legacy columns are incomplete")
            conn.execute(
                """INSERT INTO recurring_store_expense_occurrences(
                       id,template_id,period_start,expense_id,status,created_at
                   ) SELECT id,template_id,period_start,expense_id,'active',created_at
                       FROM recurring_store_expense_occurrences_legacy"""
            )
            conn.execute("DROP TABLE recurring_store_expense_occurrences_legacy")
            conn.execute(
                "CREATE INDEX idx_recurring_store_expense_occurrences_period "
                "ON recurring_store_expense_occurrences(period_start, template_id)"
            )

        # Variant identity is optional; zero preserves legacy nmID-level rows.
        for table in ("production_cost_batches", "finished_goods_cost_layers", "inventory_movements", "sales_fifo_events", "execution_tasks", "procurement_items"):
            columns = {str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
            if "chrt_id" not in columns:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN chrt_id INTEGER NOT NULL DEFAULT 0")
        # WIP identity is additive: existing batches are deliberately retained as
        # generic legacy 0/0 rows, because the current catalog cannot tell which
        # historical variant was produced.
        wip_columns = {str(row["name"]) for row in conn.execute("PRAGMA table_info(wip_blank_batches)").fetchall()}
        if "nm_id" not in wip_columns:
            conn.execute("ALTER TABLE wip_blank_batches ADD COLUMN nm_id INTEGER NOT NULL DEFAULT 0")
        if "chrt_id" not in wip_columns:
            conn.execute("ALTER TABLE wip_blank_batches ADD COLUMN chrt_id INTEGER NOT NULL DEFAULT 0")
        expected_wip_fifo_columns = ["material_key", "blank_type", "status", "nm_id", "chrt_id", "batch_date", "id"]
        actual_wip_fifo_columns = [
            str(row["name"])
            for row in conn.execute("PRAGMA index_info(idx_wip_blank_batches_fifo)").fetchall()
        ]
        if actual_wip_fifo_columns != expected_wip_fifo_columns:
            conn.execute("DROP INDEX IF EXISTS idx_wip_blank_batches_fifo")
            conn.execute(
                "CREATE INDEX idx_wip_blank_batches_fifo "
                "ON wip_blank_batches(material_key,blank_type,status,nm_id,chrt_id,batch_date,id)"
            )

        # v4.0: supplier directory and auditable partial payments.
        order_columns = {str(row["name"]) for row in conn.execute("PRAGMA table_info(procurement_orders)").fetchall()}
        if "supplier_id" not in order_columns:
            conn.execute("ALTER TABLE procurement_orders ADD COLUMN supplier_id INTEGER")

        now_v40 = datetime.now().isoformat(timespec="seconds")
        legacy_suppliers = conn.execute(
            "SELECT DISTINCT TRIM(supplier_name) AS name FROM procurement_orders WHERE TRIM(COALESCE(supplier_name,''))<>''"
        ).fetchall()
        for supplier in legacy_suppliers:
            name = str(supplier["name"] or "").strip()
            if not name:
                continue
            conn.execute(
                """
                INSERT INTO suppliers(name,created_at,updated_at)
                VALUES (?,?,?)
                ON CONFLICT(name) DO UPDATE SET updated_at=excluded.updated_at
                """,
                (name, now_v40, now_v40),
            )
        conn.execute(
            """
            UPDATE procurement_orders
               SET supplier_id=(SELECT s.id FROM suppliers s WHERE lower(s.name)=lower(TRIM(procurement_orders.supplier_name)) LIMIT 1)
             WHERE supplier_id IS NULL AND TRIM(COALESCE(supplier_name,''))<>''
            """
        )

        # v4.1: split supplier price, delivery and landed cost; optional automatic material cost.
        order_columns = {str(row["name"]) for row in conn.execute("PRAGMA table_info(procurement_orders)").fetchall()}
        if "exchange_rate" not in order_columns:
            conn.execute("ALTER TABLE procurement_orders ADD COLUMN exchange_rate REAL NOT NULL DEFAULT 1")
        item_columns = {str(row["name"]) for row in conn.execute("PRAGMA table_info(procurement_items)").fetchall()}
        for column, ddl in {
            "supplier_unit_price": "REAL DEFAULT 0",
            "exchange_rate": "REAL DEFAULT 1",
            "delivery_unit_foreign": "REAL DEFAULT 0",
            "extra_unit_rub": "REAL DEFAULT 0",
        }.items():
            if column not in item_columns:
                conn.execute(f"ALTER TABLE procurement_items ADD COLUMN {column} {ddl}")
        cost_columns = {str(row["name"]) for row in conn.execute("PRAGMA table_info(costs)").fetchall()}
        for column, ddl in {
            "auto_material_cost": "INTEGER DEFAULT 0",
            "material_cost_rub": "REAL DEFAULT 0",
            "packaging_cost_rub": "REAL DEFAULT 0",
            "packaging_cost_status": "TEXT NOT NULL DEFAULT 'unknown'",
            "packaging_cost_source": "TEXT NOT NULL DEFAULT 'unknown'",
            "packaging_cost_confirmed_at": "TEXT",
            "labor_cost_rub": "REAL DEFAULT 0",
            "other_cost_rub": "REAL DEFAULT 0",
            "updated_at": "TEXT",
        }.items():
            if column not in cost_columns:
                conn.execute(f"ALTER TABLE costs ADD COLUMN {column} {ddl}")
        # Legacy prices were stored as final RUB cost. Preserve them as a one-to-one RUB component.
        conn.execute("UPDATE procurement_orders SET exchange_rate=1 WHERE exchange_rate IS NULL OR exchange_rate<=0")
        conn.execute(
            """
            UPDATE procurement_items
               SET supplier_unit_price=unit_price,
                   exchange_rate=1,
                   delivery_unit_foreign=0,
                   extra_unit_rub=0
             WHERE COALESCE(supplier_unit_price,0)=0
               AND COALESCE(delivery_unit_foreign,0)=0
               AND COALESCE(extra_unit_rub,0)=0
               AND COALESCE(unit_price,0)>0
            """
        )

        # v4.2: keep the current cost used in profit separate from the forecast
        # of the next production batch. The v4.1 automatic mode could overwrite
        # historical/manual costs with a supplier-only rate. Restore the agreed
        # baseline once and calculate future landed-cost forecasts separately.
        cost_columns = {str(row["name"]) for row in conn.execute("PRAGMA table_info(costs)").fetchall()}
        for column, ddl in {
            "forecast_material_cost_rub": "REAL DEFAULT 0",
            "forecast_total_cost_rub": "REAL DEFAULT 0",
            "forecast_rate_rub_m": "REAL DEFAULT 0",
            "forecast_source": "TEXT DEFAULT ''",
        }.items():
            if column not in cost_columns:
                conn.execute(f"ALTER TABLE costs ADD COLUMN {column} {ddl}")
        conn.execute(
            """
            UPDATE procurement_items
               SET unit_price=(COALESCE(supplier_unit_price,0)+COALESCE(delivery_unit_foreign,0))
                              *CASE WHEN COALESCE(exchange_rate,0)>0 THEN exchange_rate ELSE 1 END
                              +COALESCE(extra_unit_rub,0)
             WHERE COALESCE(supplier_unit_price,0)>0
                OR COALESCE(delivery_unit_foreign,0)>0
                OR COALESCE(extra_unit_rub,0)>0
            """
        )
        # NOTE (genericization pass): this one-time migration and the "v4.4"/
        # "v2.8" ones below it hardcode one specific tenant's own legacy blank
        # types and material costs ("Старые/Новые болванки", 102/192/208 руб.,
        # 0.224/0.447/0.548 м). They're deliberately left as-is rather than
        # rewritten: each is guarded by an app_meta flag (or a WHERE clause
        # that only matches rows with those exact legacy values), so it has
        # already run at most once against real data and is a permanent no-op
        # for any new/other tenant's database going forward -- there is
        # nothing left for it to do, and nothing here is reachable from the
        # live app UI (see ui_helpers.py / pages/settings_page.py /
        # pages/production.py, which are now fully generic).
        migration_key = "v4.2_cost_baseline_restored"
        migration_done = conn.execute("SELECT value FROM app_meta WHERE key=?", (migration_key,)).fetchone()
        if migration_done is None:
            now_v42 = datetime.now().isoformat(timespec="seconds")
            recognized = conn.execute(
                """
                SELECT p.nm_id,p.supplier_article,p.blank_type,p.pack_size,
                       COALESCE(c.packaging_cost_rub,0) AS packaging,
                       COALESCE(c.labor_cost_rub,0) AS labor,
                       COALESCE(c.other_cost_rub,0) AS other
                  FROM production_settings p
                  LEFT JOIN costs c ON c.nm_id=p.nm_id
                 WHERE COALESCE(p.enabled,0)=1
                   AND ((p.blank_type='Старые болванки' AND p.pack_size IN (2,4))
                     OR (p.blank_type='Новые болванки' AND p.pack_size=4))
                """
            ).fetchall()
            for row in recognized:
                if str(row["blank_type"] or "") == "Старые болванки" and int(row["pack_size"] or 0) == 2:
                    baseline = 102.0
                elif str(row["blank_type"] or "") == "Старые болванки":
                    baseline = 192.0
                else:
                    baseline = 208.0
                packaging = float(row["packaging"] or 0)
                if packaging <= 0:
                    packaging = 12.0
                labor = max(0.0, float(row["labor"] or 0))
                other = max(0.0, float(row["other"] or 0))
                material_component = max(0.0, baseline-packaging-labor-other)
                conn.execute(
                    """
                    INSERT INTO costs(
                        nm_id,supplier_article,cost_per_wb_unit,auto_material_cost,material_cost_rub,
                        packaging_cost_rub,labor_cost_rub,other_cost_rub,
                        forecast_material_cost_rub,forecast_total_cost_rub,forecast_rate_rub_m,forecast_source,
                        note,updated_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(nm_id) DO UPDATE SET
                        supplier_article=excluded.supplier_article,
                        cost_per_wb_unit=excluded.cost_per_wb_unit,
                        auto_material_cost=0,
                        material_cost_rub=excluded.material_cost_rub,
                        packaging_cost_rub=excluded.packaging_cost_rub,
                        labor_cost_rub=excluded.labor_cost_rub,
                        other_cost_rub=excluded.other_cost_rub,
                        updated_at=excluded.updated_at
                    """,
                    (int(row["nm_id"]), str(row["supplier_article"] or ""), baseline, 0, material_component,
                     packaging, labor, other, 0.0, 0.0, 0.0, "",
                     "Фиксированная себестоимость старого запаса; прогноз новой партии рассчитывается отдельно", now_v42),
                )
            conn.execute(
                "INSERT INTO app_meta(key,value,updated_at) VALUES (?,?,?)",
                (migration_key, "1", now_v42),
            )

        # v4.4: normalize the agreed historical baseline once more. Some rows were
        # manually edited after v4.2 or existed outside the first migration sample.
        # This affects only enabled own-production profiles and does not touch
        # purchased products. Actual FIFO batch costs remain separate.
        v44_key = "v4.4_production_baseline_normalized"
        v44_done = conn.execute("SELECT value FROM app_meta WHERE key=?", (v44_key,)).fetchone()
        if v44_done is None:
            now_v44 = datetime.now().isoformat(timespec="seconds")
            own_rows = conn.execute(
                """
                SELECT p.nm_id,p.supplier_article,p.blank_type,p.pack_size,
                       COALESCE(c.packaging_cost_rub,0) AS packaging,
                       COALESCE(c.labor_cost_rub,0) AS labor,
                       COALESCE(c.other_cost_rub,0) AS other
                  FROM production_settings p
                  LEFT JOIN costs c ON c.nm_id=p.nm_id
                 WHERE COALESCE(p.enabled,0)=1
                   AND ((p.blank_type='Старые болванки' AND p.pack_size IN (2,4))
                     OR (p.blank_type='Новые болванки' AND p.pack_size=4))
                """
            ).fetchall()
            for row in own_rows:
                blank_type = str(row["blank_type"] or "")
                pack_size = int(row["pack_size"] or 0)
                baseline = 102.0 if blank_type == "Старые болванки" and pack_size == 2 else (192.0 if blank_type == "Старые болванки" else 208.0)
                packaging = max(0.0, float(row["packaging"] or 0)) or 12.0
                labor = max(0.0, float(row["labor"] or 0))
                other = max(0.0, float(row["other"] or 0))
                material_component = max(0.0, baseline-packaging-labor-other)
                conn.execute(
                    """
                    INSERT INTO costs(
                        nm_id,supplier_article,cost_per_wb_unit,auto_material_cost,material_cost_rub,
                        packaging_cost_rub,labor_cost_rub,other_cost_rub,note,updated_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(nm_id) DO UPDATE SET
                        supplier_article=excluded.supplier_article,
                        cost_per_wb_unit=excluded.cost_per_wb_unit,
                        auto_material_cost=0,
                        material_cost_rub=excluded.material_cost_rub,
                        packaging_cost_rub=excluded.packaging_cost_rub,
                        labor_cost_rub=excluded.labor_cost_rub,
                        other_cost_rub=excluded.other_cost_rub,
                        note=excluded.note,updated_at=excluded.updated_at
                    """,
                    (int(row["nm_id"]), str(row["supplier_article"] or ""), baseline, 0,
                     material_component, packaging, labor, other,
                     "Историческая базовая себестоимость; фактические партии учитываются отдельно", now_v44),
                )
            conn.execute(
                "INSERT INTO app_meta(key,value,updated_at) VALUES (?,?,?)",
                (v44_key, "1", now_v44),
            )

        # Preserve the meaning of old fully-paid statuses by creating one legacy
        # payment when no payment history exists yet.
        payment_history_count = int(conn.execute("SELECT COUNT(*) AS n FROM procurement_payments").fetchone()["n"] or 0)
        legacy_paid = []
        if payment_history_count == 0:
            legacy_paid = conn.execute(
                """
                SELECT o.id,o.paid_date,o.order_date,
                       COALESCE((SELECT SUM(i.quantity*i.unit_price) FROM procurement_items i WHERE i.order_id=o.id),0) AS total_amount
                  FROM procurement_orders o
                 WHERE o.status IN ('Оплачено','В пути','Частично получено','Получено')
                """
            ).fetchall()
        for order in legacy_paid:
            amount = float(order["total_amount"] or 0)
            if amount <= 0:
                continue
            conn.execute(
                """
                INSERT INTO procurement_payments(order_id,payment_date,amount,method,note,status,created_at)
                VALUES (?,?,?,?,?,'applied',?)
                """,
                (int(order["id"]), str(order["paid_date"] or order["order_date"] or datetime.now().date().isoformat()),
                 amount, "Перенос из версии 3.9", "Автоматически создано при обновлении", now_v40),
            )

        # Production schema migrations (v2.8). Existing installations keep all
        # saved settings; known material norms are classified automatically.
        prod_columns = {str(row["name"]) for row in conn.execute("PRAGMA table_info(production_settings)").fetchall()}
        prod_migrations = {
            "blank_type": "TEXT DEFAULT ''",
            "pack_size": "INTEGER DEFAULT 4",
            "auto_rules": "INTEGER DEFAULT 0",
            "material_name": "TEXT DEFAULT ''",
        }
        for column, definition in prod_migrations.items():
            if column not in prod_columns:
                conn.execute(f"ALTER TABLE production_settings ADD COLUMN {column} {definition}")

        # Raw-material unit-of-measure generalization: existing rows default to
        # unit='м'/tracking_mode='packaged'/opening_rate_rub=0, which reproduces
        # today's exact behaviour (rolls of meters, single tenant-wide FIFO
        # opening rate) with zero change for any tenant who never touches the
        # new fields. See pages/settings_page.py section "Остатки сырья" and
        # ui_helpers.py's NO_PACKAGE_ROLL_LENGTH for how 'quantity' mode works.
        material_inventory_columns = {
            str(row["name"]) for row in conn.execute("PRAGMA table_info(material_inventory_color)").fetchall()
        }
        for column, definition in {
            "unit": "TEXT DEFAULT 'м'",
            "tracking_mode": "TEXT DEFAULT 'packaged'",
            "opening_rate_rub": "REAL DEFAULT 0",
        }.items():
            if column not in material_inventory_columns:
                conn.execute(f"ALTER TABLE material_inventory_color ADD COLUMN {column} {definition}")

        # Infer the user's three established production profiles from the
        # previously entered material norms. Only rows without a profile are
        # touched, and a manually enlarged batch is never reduced.
        conn.execute(
            """
            UPDATE production_settings
               SET blank_type='Старые болванки', pack_size=2, auto_rules=1,
                   material_per_unit=0.224,
                   min_batch=CASE WHEN COALESCE(min_batch,1) <= 1 THEN 20 ELSE min_batch END
             WHERE COALESCE(blank_type,'')=''
               AND ABS(COALESCE(material_per_unit,0)-0.224) < 0.010
            """
        )
        conn.execute(
            """
            UPDATE production_settings
               SET blank_type='Старые болванки', pack_size=4, auto_rules=1,
                   material_per_unit=0.447,
                   min_batch=CASE WHEN COALESCE(min_batch,1) <= 1 THEN 10 ELSE min_batch END
             WHERE COALESCE(blank_type,'')=''
               AND ABS(COALESCE(material_per_unit,0)-0.447) < 0.010
            """
        )
        conn.execute(
            """
            UPDATE production_settings
               SET blank_type='Новые болванки', pack_size=4, auto_rules=1,
                   material_per_unit=0.548,
                   min_batch=CASE WHEN COALESCE(min_batch,1) <= 1 THEN 10 ELSE min_batch END
             WHERE COALESCE(blank_type,'')=''
               AND ABS(COALESCE(material_per_unit,0)-0.548) < 0.010
            """
        )
        conn.execute(
            """
            UPDATE production_settings
               SET material_per_unit=0.224, min_batch=CASE WHEN COALESCE(min_batch,1) <= 1 THEN 20 ELSE min_batch END
             WHERE blank_type='Старые болванки' AND pack_size=2 AND COALESCE(auto_rules,0)=1
            """
        )
        conn.execute(
            """
            UPDATE production_settings
               SET material_per_unit=0.447, min_batch=CASE WHEN COALESCE(min_batch,1) <= 1 THEN 10 ELSE min_batch END
             WHERE blank_type='Старые болванки' AND pack_size=4 AND COALESCE(auto_rules,0)=1
            """
        )
        conn.execute(
            """
            UPDATE production_settings
               SET pack_size=4, material_per_unit=0.548, min_batch=CASE WHEN COALESCE(min_batch,1) <= 1 THEN 10 ELSE min_batch END
             WHERE blank_type='Новые болванки' AND COALESCE(auto_rules,0)=1
            """
        )

        # v3.0: raw material is a common color stock. The same roll can be used
        # for old or new blanks, so inventory is migrated to a color-only key.
        color_count = conn.execute("SELECT COUNT(*) AS n FROM material_inventory_color").fetchone()["n"]
        if int(color_count or 0) == 0:
            legacy_rows = conn.execute(
                """
                SELECT material_name, balance_known, full_rolls, partial_meters, roll_length, note, updated_at
                  FROM material_inventory
                 WHERE TRIM(COALESCE(material_name,'')) <> ''
                 ORDER BY balance_known DESC, updated_at DESC
                """
            ).fetchall()
            chosen = {}
            for row in legacy_rows:
                name = str(row["material_name"] or "").strip()
                key = name.casefold()
                if key and key not in chosen:
                    chosen[key] = row
            for key, row in chosen.items():
                conn.execute(
                    """
                    INSERT OR IGNORE INTO material_inventory_color(
                        material_key,material_name,balance_known,full_rolls,partial_meters,roll_length,note,updated_at
                    ) VALUES (?,?,?,?,?,?,?,?)
                    """,
                    (key, str(row["material_name"] or "").strip(), int(row["balance_known"] or 0),
                     int(row["full_rolls"] or 0), float(row["partial_meters"] or 0),
                     float(row["roll_length"] or 25.5), str(row["note"] or ""),
                     str(row["updated_at"] or datetime.now().isoformat(timespec="seconds")))
                )

        # v3.7: production receipts also write off raw material by color.
        movement_columns = {str(row["name"]) for row in conn.execute("PRAGMA table_info(inventory_movements)").fetchall()}
        movement_migrations = {
            "material_key": "TEXT DEFAULT ''",
            "material_name": "TEXT DEFAULT ''",
            "material_delta": "REAL DEFAULT 0",
            "procurement_order_id": "INTEGER",
            "procurement_item_id": "INTEGER",
            "procurement_quantity": "REAL DEFAULT 0",
            "material_cost_rub": "REAL DEFAULT 0",
            "unit_cost_rub": "REAL DEFAULT 0",
            "goods_cost_rub": "REAL DEFAULT 0",
            "goods_unit_cost_rub": "REAL DEFAULT 0",
        }
        for column, definition in movement_migrations.items():
            if column not in movement_columns:
                conn.execute(f"ALTER TABLE inventory_movements ADD COLUMN {column} {definition}")

        capacity_columns = {str(row["name"]) for row in conn.execute("PRAGMA table_info(production_capacity)").fetchall()}
        capacity_migrations = {
            "fulfillment_lead_days": "INTEGER DEFAULT 0",
            "emergency_cover_days": "INTEGER DEFAULT 7",
            "expedited_fbo_lead_days": "INTEGER DEFAULT 3",
            "fbs_lead_days": "INTEGER DEFAULT 0",
        }
        for column, definition in capacity_migrations.items():
            if column not in capacity_columns:
                conn.execute(f"ALTER TABLE production_capacity ADD COLUMN {column} {definition}")

        conn.execute(
            """
            INSERT OR IGNORE INTO production_capacity(
                id,capacity_known,pieces_per_day,workdays,horizon_days,
                fulfillment_lead_days,emergency_cover_days,expedited_fbo_lead_days,
                fbs_lead_days,note,updated_at
            ) VALUES (1,0,0,'0,1,2,3,4,5',14,0,7,3,0,'',?)
            """,
            (datetime.now().isoformat(timespec="seconds"),),
        )

        # v4.6: historical sales already present at the moment of upgrade are
        # registered as a baseline. Only later API events consume FIFO layers.
        _initialize_sales_fifo_baseline_if_needed(conn)
        from .manufacturing import init_manufacturing_schema
        init_manufacturing_schema(conn)
        from .workcenters import init_workcenter_schema
        init_workcenter_schema(conn)
        from .expense_coverage import init_expense_coverage_schema
        init_expense_coverage_schema(conn)
        from .ozon_pnl import ensure_schema as ensure_ozon_pnl_schema
        ensure_ozon_pnl_schema(conn)
        # Idempotent audit ledger for the explicit supply-plan confirmation flow.
        # This is additive and preserves all legacy procurement records.
        conn.execute("""CREATE TABLE IF NOT EXISTS supply_plan_confirmations (
            confirmation_key TEXT PRIMARY KEY,
            request_hash TEXT NOT NULL,
            preview_fingerprint TEXT NOT NULL,
            order_ids_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        )""")

def _num(value: Any, default: float = 0.0) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return default

def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return default

def _dump(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)

def _table_projection(conn, name: str, *, omit_raw: bool = False) -> str:
    """Opt-in numerical/dashboard projection; ordinary readers retain raw data."""
    if not omit_raw:
        return "*"
    columns = [row[1] for row in conn.execute(f'PRAGMA table_info("{name}")') if row[1] != "raw_json"]
    return ",".join('"' + column.replace('"', '""') + '"' for column in columns)


def read_table(name: str, *, omit_raw: bool = False) -> pd.DataFrame:
    allowed = {"orders", "sales", "stocks", "ads_daily", "costs", "products_catalog", "financial_report", "production_settings", "material_inventory", "material_inventory_color", "product_pipeline", "production_capacity", "execution_tasks", "inventory_movements", "procurement_orders", "procurement_items", "suppliers", "procurement_payments", "material_cost_layers", "material_fifo_consumptions", "production_cost_batches", "wip_blank_batches", "wip_blank_allocations", "sync_log"}
    if name not in allowed:
        raise ValueError("Недопустимая таблица")
    with connect() as conn:
        return pd.read_sql_query(f"SELECT {_table_projection(conn, name, omit_raw=omit_raw)} FROM {name}", conn)

def table_count(name: str) -> int:
    allowed = {"orders", "sales", "stocks", "ads_daily", "costs", "products_catalog", "production_settings", "material_inventory", "material_inventory_color", "product_pipeline", "production_capacity", "execution_tasks", "inventory_movements", "procurement_orders", "procurement_items", "suppliers", "procurement_payments", "material_cost_layers", "material_fifo_consumptions", "production_cost_batches", "wip_blank_batches", "wip_blank_allocations"}
    if name not in allowed:
        return 0
    with connect() as conn:
        row = conn.execute(f"SELECT COUNT(*) AS n FROM {name}").fetchone()
        return int(row["n"])

def max_value(table: str, column: str) -> str | None:
    allowed = {
        ("orders", "last_change"),
        ("sales", "last_change"),
        ("stocks", "snapshot_at"),
        ("ads_daily", "day"),
        ("financial_report", "operation_date"),
    }
    if (table, column) not in allowed:
        raise ValueError("Недопустимый запрос")
    with connect() as conn:
        row = conn.execute(f"SELECT MAX({column}) AS v FROM {table}").fetchone()
        return row["v"] if row else None

def _now_msk_naive_iso() -> str:
    """Moscow-local naive timestamp, matching this project's convention for
    every other stored timestamp (see wb_fbw_evidence._wb_msk_naive_iso's
    docstring). start_sync_log/finish_sync_log used to store plain
    datetime.now() -- the server's own OS clock, which is UTC on the VPS --
    while every reader (pages/today.py's staleness check, control.py's FIFO
    guard, ui_helpers._parse_local_datetime) treats a naive sync_log
    timestamp as already being Moscow time. That mismatch made "last sync"
    always appear ~180 minutes (the MSK-UTC offset) older than it really
    was, regardless of how recently a sync actually finished."""
    return datetime.now(ZoneInfo("Europe/Moscow")).replace(tzinfo=None).isoformat(timespec="seconds")


def start_sync_log() -> int:
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO sync_log(started_at,status) VALUES (?,?)",
            (_now_msk_naive_iso(), "running"),
        )
        return int(cur.lastrowid)

def update_sync_progress(log_id: int, done: int, total: int, stage: str) -> None:
    """Record live progress ("progress: 3/7 sales") on a run that is still going.

    Only a row that is still 'running' is touched, so a late write can never
    overwrite the final status/details written by finish_sync_log().
    """
    with connect() as conn:
        conn.execute(
            "UPDATE sync_log SET details=? WHERE id=? AND status='running'",
            (f"progress: {done}/{total} {stage}", log_id),
        )


def finish_sync_log(log_id: int, status: str, details: str) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE sync_log SET finished_at=?, status=?, details=? WHERE id=?",
            (_now_msk_naive_iso(), status, details, log_id),
        )

def last_sync() -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM sync_log ORDER BY id DESC LIMIT 1").fetchone()
        return dict(row) if row else None

def clear_demo_data() -> None:
    with connect() as conn:
        conn.executescript(
            """
            DELETE FROM orders WHERE id LIKE 'DEMO-%';
            DELETE FROM sales WHERE id LIKE 'DEMO-%';
            DELETE FROM stocks WHERE raw_json LIKE '%\"demo\":true%';
            DELETE FROM ads_daily WHERE raw_json LIKE '%\"demo\":true%';
            """
        )

def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}

def ensure_financial_schema() -> None:
    with connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS financial_report (
                rrd_id INTEGER PRIMARY KEY,
                report_id INTEGER,
                operation_date TEXT,
                sale_date TEXT,
                nm_id INTEGER DEFAULT 0,
                supplier_article TEXT,
                quantity INTEGER DEFAULT 0,
                retail_amount REAL DEFAULT 0,
                retail_price_withdisc_rub REAL DEFAULT 0,
                ppvz_for_pay REAL DEFAULT 0,
                commission REAL DEFAULT 0,
                delivery_rub REAL DEFAULT 0,
                storage_fee REAL DEFAULT 0,
                deduction REAL DEFAULT 0,
                penalty REAL DEFAULT 0,
                acceptance REAL DEFAULT 0,
                operation_name TEXT,
                raw_json TEXT NOT NULL,
                doc_type_name TEXT DEFAULT '',
                ppvz_sales_commission REAL DEFAULT 0,
                ppvz_reward REAL DEFAULT 0,
                ppvz_vw REAL DEFAULT 0,
                ppvz_vw_nds REAL DEFAULT 0,
                acquiring_fee REAL DEFAULT 0,
                additional_payment REAL DEFAULT 0,
                rebill_logistic_cost REAL DEFAULT 0,
                delivery_amount INTEGER DEFAULT 0,
                return_amount INTEGER DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_fin_operation_date ON financial_report(operation_date);
            CREATE INDEX IF NOT EXISTS idx_fin_nm ON financial_report(nm_id);
            """
        )
        existing = _table_columns(conn, "financial_report")
        migrations = {
            "doc_type_name": "TEXT DEFAULT ''",
            "ppvz_sales_commission": "REAL DEFAULT 0",
            "ppvz_reward": "REAL DEFAULT 0",
            "ppvz_vw": "REAL DEFAULT 0",
            "ppvz_vw_nds": "REAL DEFAULT 0",
            "acquiring_fee": "REAL DEFAULT 0",
            "additional_payment": "REAL DEFAULT 0",
            "rebill_logistic_cost": "REAL DEFAULT 0",
            "delivery_amount": "INTEGER DEFAULT 0",
            "return_amount": "INTEGER DEFAULT 0",
        }
        for column, definition in migrations.items():
            if column not in existing:
                conn.execute(f"ALTER TABLE financial_report ADD COLUMN {column} {definition}")

def _first(row: dict[str, Any], *names: str, default: Any = None) -> Any:
    for name in names:
        if name in row and row.get(name) is not None:
            return row.get(name)
    return default
