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
