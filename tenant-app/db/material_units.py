"""BOM entry conversion only; inventory always retains its catalog base unit."""
from decimal import Decimal, InvalidOperation, localcontext

# No density, width, package count or roll-length inference.
UNITS = {
    'г': ('mass', Decimal('0.001')), 'кг': ('mass', Decimal('1')),
    'мл': ('volume', Decimal('0.001')), 'л': ('volume', Decimal('1')),
    'мм': ('length', Decimal('0.001')), 'см': ('length', Decimal('0.01')),
    'м': ('length', Decimal('1')),
}


def _text(value):
    return format(value, 'f').rstrip('0').rstrip('.') if '.' in format(value, 'f') else format(value, 'f')


def normalize_material_quantity(amount, entered_unit, base_unit):
    """Return exact provenance; forbid conversions outside known dimensions.

    Unchanged custom catalog units remain valid for backward compatibility.
    Canonical bounds match the six-place material/FIFO accounting precision.
    """
    if not isinstance(entered_unit,str) or not isinstance(base_unit,str) or not entered_unit or not base_unit:
        raise ValueError('Укажите единицу нормы и единицу справочника материала')
    if isinstance(amount,bool) or len(str(amount)) > 80:
        raise ValueError('Норма: требуется конечное положительное число')
    try:
        entered = Decimal(str(amount))
    except (InvalidOperation, ValueError):
        raise ValueError('Норма: требуется конечное положительное число') from None
    if not entered.is_finite() or entered <= 0 or entered > Decimal('1e12'):
        raise ValueError('Норма: требуется положительное число не больше 1e12')
    if entered_unit == base_unit:
        factor = Decimal(1)
    else:
        source,target=UNITS.get(entered_unit),UNITS.get(base_unit)
        if source is None or target is None or source[0] != target[0]:
            raise ValueError(f'Нельзя перевести {entered_unit} в {base_unit}: единицы несовместимы или перевод не задан')
        factor=source[1]/target[1]
    with localcontext() as context:
        context.prec=100
        normalized=entered*factor
    if normalized < Decimal('0.001') or normalized > Decimal('1e12'):
        raise ValueError('Норма в единице справочника должна быть от 0.001 до 1e12')
    canonical=_text(normalized)
    if '.' in canonical and len(canonical.split('.')[1]) > 6:
        raise ValueError('Норма в единице справочника должна иметь не больше 6 знаков; округление не выполняется')
    if Decimal(str(float(normalized))) != normalized:
        raise ValueError('Норма слишком велика для точного сохранения её дробной части')
    return {'entered_quantity_per_unit':_text(entered),'entered_unit':entered_unit,
            'conversion_factor':_text(factor),'normalized_quantity_per_unit':canonical}


def convert_material_amount(quantity, unit_price, entered_unit, base_unit):
    """Preserve source value and reject physical/FIFO precision loss."""
    provenance=normalize_material_quantity(quantity,entered_unit,base_unit)
    q=Decimal(provenance['normalized_quantity_per_unit'])
    if '.' in _text(q) and len(_text(q).split('.')[1])>3:
        raise ValueError('Количество в единице справочника должно иметь не больше 3 знаков для физического склада')
    if isinstance(unit_price,bool) or len(str(unit_price))>80:
        raise ValueError('Цена материала должна быть конечной неотрицательной')
    try: price=Decimal(str(unit_price))
    except (InvalidOperation,ValueError): raise ValueError('Неверная цена материала') from None
    if not price.is_finite() or price<0 or price>Decimal('1e12'):
        raise ValueError('Цена материала должна быть от 0 до 1e12')
    with localcontext() as context:
        context.prec=100
        base_price=price/Decimal(provenance['conversion_factor'])
        source_total=Decimal(provenance['entered_quantity_per_unit'])*price
        target_total=q*base_price
    text=_text(base_price)
    if base_price>Decimal('1e12') or ('.' in text and len(text.split('.')[1])>6) or Decimal(str(float(base_price)))!=base_price:
        raise ValueError('Цена в единице справочника не сохраняется точно до 6 знаков; округление не выполняется')
    if source_total!=target_total: raise ValueError('Стоимость после перевода не совпадает с исходной')
    if entered_unit != base_unit and source_total != source_total.quantize(Decimal('0.01')):
        raise ValueError('Сумма после перевода содержит долю копейки; укажите документальную сумму/цену до копеек')
    if entered_unit != base_unit and Decimal(str(round(float(q)*float(base_price),2))) != source_total:
        raise ValueError('Сумма после перевода слишком велика для точного сохранения до копеек')
    return {'entered_quantity':provenance['entered_quantity_per_unit'],'entered_unit':entered_unit,
            'entered_unit_price_rub':_text(price),'conversion_factor':provenance['conversion_factor'],
            'base_quantity':_text(q),'base_unit':base_unit,'base_unit_price_rub':text,'source_total_rub':_text(source_total)}


def record_receipt_conversion(conn, movement_id, source_key, provenance):
    """Append within the caller receipt SAVEPOINT; reversal never erases proof."""
    import json
    conn.execute('CREATE TABLE IF NOT EXISTS material_unit_conversion_receipts (movement_id INTEGER PRIMARY KEY REFERENCES inventory_movements(id),source_key TEXT NOT NULL,payload_json TEXT NOT NULL)')
    for operation in ('UPDATE','DELETE'):
        conn.execute(f"CREATE TRIGGER IF NOT EXISTS material_unit_conversion_receipts_no_{operation.lower()} BEFORE {operation} ON material_unit_conversion_receipts BEGIN SELECT RAISE(ABORT,'Material conversion history immutable'); END")
    conn.execute("CREATE TRIGGER IF NOT EXISTS material_unit_conversion_receipts_no_replace BEFORE INSERT ON material_unit_conversion_receipts WHEN EXISTS(SELECT 1 FROM material_unit_conversion_receipts WHERE movement_id=NEW.movement_id) BEGIN SELECT RAISE(ABORT,'Material conversion history immutable'); END")
    conn.execute('INSERT INTO material_unit_conversion_receipts VALUES(?,?,?)',(movement_id,source_key,json.dumps(provenance,sort_keys=True,ensure_ascii=False,allow_nan=False)))


def apply_quantity_receipt_delta(conn, material_key, material_name, delta):
    """Exact converted receipt; known packaging length is representation only."""
    from datetime import datetime
    row=conn.execute('SELECT * FROM material_inventory_color WHERE material_key=?',(material_key,)).fetchone()
    if row is None or row['tracking_mode'] not in {'quantity','packaged'} or row['balance_known']!=1:
        raise ValueError('Перевод приёмки доступен только подтверждённому складу')
    length=Decimal(str(row['roll_length'] or 0))
    full=Decimal(str(row['full_rolls'] or 0));partial=Decimal(str(row['partial_meters'] or 0))
    if not length.is_finite() or not full.is_finite() or not partial.is_finite() or min(length,full,partial)<0 or (not length and full):
        raise ValueError('Физический остаток не подтверждён')
    if row['tracking_mode']=='packaged' and not length:
        raise ValueError('Длина упаковки в справочнике не подтверждена')
    current=full*length+partial
    total=current+Decimal(str(delta))
    total_text=_text(total)
    if total<0 or total>Decimal('1e12') or ('.' in total_text and len(total_text.split('.')[1])>3) or Decimal(str(float(total)))!=total:
        raise ValueError('Физический остаток после приёмки не сохраняется точно до 3 знаков')
    length=length or Decimal('1')
    full=int(total//length);partial=total-Decimal(full)*length
    if Decimal(str(float(partial)))!=partial:
        raise ValueError('Дробная часть физического остатка не сохраняется точно')
    conn.execute('UPDATE material_inventory_color SET material_name=?,full_rolls=?,partial_meters=?,roll_length=?,updated_at=? WHERE material_key=?',(material_name,full,float(partial),float(length),datetime.now().isoformat(timespec='seconds'),material_key))
