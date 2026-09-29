"""Bounded no-overwrite CSV migration; preview never persists source records."""
from datetime import date, datetime, timezone
from decimal import Decimal
import csv
import hashlib
import io
import json
from .core import connect

FIELDS = {
    "products": ["source_id", "nm_id", "supplier_article", "product_name"],
    "materials": ["source_id", "material_name", "unit"],
    "opening_materials": ["source_id", "material_name", "unit", "quantity", "unit_cost_rub", "source_date"],
}
UNITS = {"м", "кг", "л", "шт"}

def _json(value): return json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(",",":"),allow_nan=False)
def _hash(value): return hashlib.sha256(_json(value).encode()).hexdigest()

def _schema(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS migration_import_batches(
      request_key TEXT PRIMARY KEY, request_hash TEXT NOT NULL, content_hash TEXT NOT NULL UNIQUE,
      kind TEXT NOT NULL, imported_count INTEGER NOT NULL, created_at TEXT NOT NULL, actor TEXT)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS migration_import_sources(
      kind TEXT NOT NULL, source_id TEXT NOT NULL, batch_key TEXT NOT NULL,
      entity_key TEXT NOT NULL, snapshot_json TEXT NOT NULL, PRIMARY KEY(kind,source_id))""")

    for table in ("migration_import_batches", "migration_import_sources"):
        for operation in ("UPDATE", "DELETE"):
            conn.execute(f"CREATE TRIGGER IF NOT EXISTS {table}_{operation.lower()}_immutable BEFORE {operation} ON {table} BEGIN SELECT RAISE(ABORT, 'Import history is immutable'); END")

def parse_csv(kind, text, mapping):
    if kind not in FIELDS: raise ValueError("Неизвестный тип импорта")
    if len(text.encode("utf-8"))>1_000_000: raise ValueError("CSV больше 1 МБ")
    if "\x00" in text: raise ValueError("CSV содержит недопустимый символ")
    if set(mapping) != set(FIELDS[kind]) or len(set(mapping.values())) != len(mapping):
        raise ValueError("Сопоставьте каждое обязательное поле с отдельным столбцом")
    try:
        delimiter=csv.Sniffer().sniff(text[:4096],delimiters=",;\t").delimiter
    except csv.Error: delimiter=";"
    reader=csv.DictReader(io.StringIO(text.lstrip("\ufeff")),delimiter=delimiter)
    if not reader.fieldnames or len(set(reader.fieldnames))!=len(reader.fieldnames): raise ValueError("Заголовки CSV отсутствуют или повторяются")
    if any(column not in reader.fieldnames for column in mapping.values()): raise ValueError("Сопоставленный столбец отсутствует в CSV")
    rows=[]; errors=[]; identities=set(); source_ids=set()
    try:
        for index,raw in enumerate(reader,start=2):
            if index>1001: raise ValueError("В одном импорте допускается не более 1000 строк")
            try:
                if None in raw or any(v is None for v in raw.values()): raise ValueError("Количество ячеек не совпадает с заголовком")
                row={field:str(raw[mapping[field]]).strip() for field in FIELDS[kind]}
                if any(not value or len(value)>300 for value in row.values()): raise ValueError("Все поля обязательны и не длиннее 300 символов")
                if row["source_id"] in source_ids: raise ValueError("Повторяется идентификатор исходного документа")
                source_ids.add(row["source_id"])
                if kind=="products":
                    if not row["nm_id"].isdigit() or not 0<int(row["nm_id"])<=2**53-1: raise ValueError("Артикул WB должен быть положительным целым")
                    row["nm_id"]=int(row["nm_id"]); identity=str(row["nm_id"])
                else:
                    if row["unit"] not in UNITS: raise ValueError("Единица должна быть м, кг, л или шт")
                    identity=row["material_name"].casefold()
                    row["material_key"]=identity
                if identity in identities: raise ValueError("Товар или материал повторяется внутри файла")
                identities.add(identity)
                if kind=="opening_materials":
                    for field in ("quantity","unit_cost_rub"):
                        value=Decimal(row[field].replace(",","."))
                        if not value.is_finite() or value<=0 or value>Decimal("100000000") or value.as_tuple().exponent < -6:
                            raise ValueError("Количество и стоимость должны быть >0, не более 100000000, до 6 знаков после запятой")
                        row[field]=float(value)
                    if row["quantity"]<=0.0005: raise ValueError("Количество слишком мало для FIFO-слоя")
                    row["source_date"]=date.fromisoformat(row["source_date"]).isoformat()
                    if date.fromisoformat(row["source_date"])>date.today(): raise ValueError("Начальный остаток не может быть из будущего")
                rows.append({"line":index,"entity_key":identity,"data":row})
            except (ValueError,ArithmeticError) as error:
                errors.append({"line":index,"message":str(error)})
    except csv.Error as error: raise ValueError("Неверный CSV") from error
    if not rows and not errors: raise ValueError("В CSV нет строк")
    return rows,errors

def _preview(conn, kind, text, mapping):
    rows,errors=parse_csv(kind,text,mapping)
    states=[]
    for item in rows:
        row=item["data"]; key=item["entity_key"]
        if kind=="products":
            found=conn.execute("SELECT * FROM products_catalog WHERE nm_id=?",(row["nm_id"],)).fetchone()
            state={"existing":dict(found) if found else None}
            if found: errors.append({"line":item["line"],"message":"Артикул уже существует: импорт не перезаписывает каталог"})
        else:
            found=conn.execute("SELECT * FROM material_inventory_color WHERE material_key=?",(key,)).fetchone()
            layers=conn.execute("SELECT COUNT(*) FROM material_cost_layers WHERE material_key=?",(key,)).fetchone()[0]
            state={"existing":dict(found) if found else None,"layers":layers}
            if kind=="materials" and found: errors.append({"line":item["line"],"message":"Материал уже существует: импорт не перезаписывает справочник"})
            if kind=="opening_materials":
                if not found: errors.append({"line":item["line"],"message":"Сначала импортируйте материал в справочник"})
                elif found["unit"]!=row["unit"] or found["tracking_mode"]!="quantity": errors.append({"line":item["line"],"message":"Единица или режим склада не совпадает; используйте материал в базовых единицах"})
                elif layers or float(found["partial_meters"] or 0)!=0 or int(found["full_rolls"] or 0)!=0:
                    errors.append({"line":item["line"],"message":"У материала уже есть остаток или история FIFO: замена запрещена"})
        state["source"]=None
        # Query a journal only if present: preview must not create schema or data.
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='migration_import_sources'").fetchone():
            source=conn.execute("SELECT * FROM migration_import_sources WHERE kind=? AND source_id=?",(kind,row["source_id"])).fetchone()
            state["source"]=dict(source) if source else None
            if source: errors.append({"line":item["line"],"message":"Документ уже импортирован"})
        states.append(state)
    fingerprint=_hash({"kind":kind,"rows":rows,"states":states,"errors":errors})
    return {"kind":kind,"rows":rows,"errors":errors,"valid_count":len(rows),"can_confirm":not errors and bool(rows),"preview_fingerprint":fingerprint}

def preview_import(kind,text,mapping):
    with connect() as conn:
        conn.execute("BEGIN")
        return _preview(conn,kind,text,mapping)

def confirm_import(kind,text,mapping,request_key,preview_fingerprint,actor=None):
    rows,errors=parse_csv(kind,text,mapping)
    if errors: raise ValueError("Исправьте ошибки CSV перед подтверждением")
    content_hash=_hash({"kind":kind,"rows":rows})
    request_hash=_hash({"content":content_hash,"preview":preview_fingerprint})
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE"); _schema(conn)
        prior=conn.execute("SELECT * FROM migration_import_batches WHERE request_key=?",(request_key,)).fetchone()
        if prior and prior["request_hash"]!=request_hash: raise ValueError("Ключ подтверждения уже использован для других данных")
        prior=prior or conn.execute("SELECT * FROM migration_import_batches WHERE content_hash=?",(content_hash,)).fetchone()
        if prior:
            if prior["request_key"]==request_key and prior["request_hash"]!=request_hash: raise ValueError("Ключ подтверждения уже использован для других данных")
            return {"imported_count":prior["imported_count"],"request_key":prior["request_key"],"replayed":True}
        current=_preview(conn,kind,text,mapping)
        if current["preview_fingerprint"]!=preview_fingerprint: raise ValueError("Данные изменились: повторите предпросмотр")
        if not current["can_confirm"]: raise ValueError("Импорт заблокирован ошибками; существующие данные не заменяются")
        now=datetime.now(timezone.utc).isoformat()
        for item in rows:
            row=item["data"]; key=item["entity_key"]
            if kind=="products":
                conn.execute("INSERT INTO products_catalog(nm_id,supplier_article,product_name,updated_at,raw_json) VALUES(?,?,?,?,?)",
                  (row["nm_id"],row["supplier_article"],row["product_name"],now,_json({"migration_source":row["source_id"],"sizes":[]})))
            elif kind=="materials":
                conn.execute("INSERT INTO material_inventory_color(material_key,material_name,unit,tracking_mode,balance_known,full_rolls,partial_meters,roll_length,note,updated_at) VALUES(?,?,?,'quantity',0,0,0,0,?,?)",
                  (key,row["material_name"],row["unit"],f"CSV: {row['source_id']}",now))
            else:
                from .fifo_materials import _create_material_layer
                _create_material_layer(conn,key,row["material_name"],"migration_opening",f"csv:{row['source_id']}",row["source_date"],row["quantity"],row["unit_cost_rub"],f"CSV opening: {row['source_id']}")
                conn.execute("UPDATE material_inventory_color SET balance_known=1,partial_meters=?,opening_rate_rub=?,updated_at=? WHERE material_key=?",
                             (row["quantity"],row["unit_cost_rub"],now,key))
            conn.execute("INSERT INTO migration_import_sources(kind,source_id,batch_key,entity_key,snapshot_json) VALUES(?,?,?,?,?)",(kind,row["source_id"],request_key,key,_json(row)))
        conn.execute("INSERT INTO migration_import_batches VALUES(?,?,?,?,?,?,?)",(request_key,request_hash,content_hash,kind,len(rows),now,actor))
        return {"imported_count":len(rows),"request_key":request_key,"replayed":False}

def import_history():
    with connect() as conn:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='migration_import_batches'").fetchone(): return []
        return [dict(r) for r in conn.execute("SELECT request_key,kind,imported_count,created_at,actor FROM migration_import_batches ORDER BY created_at DESC LIMIT 100")]
