from decimal import Decimal
import pytest
from db.material_units import normalize_material_quantity


@pytest.mark.parametrize('amount,entered,base,expected,factor', [
    ('125','г','кг','0.125','0.001'), ('0.125','кг','г','125','1000'),
    ('250','мл','л','0.25','0.001'), ('25','см','м','0.25','0.01'),
    ('2.5','м','мм','2500','1000'), ('0.25','м²','м²','0.25','1'),
])
def test_exact_same_dimension(amount,entered,base,expected,factor):
    result=normalize_material_quantity(amount,entered,base)
    assert Decimal(result['normalized_quantity_per_unit'])==Decimal(expected)
    assert Decimal(result['conversion_factor'])==Decimal(factor)
    assert result['entered_unit']==entered
    assert Decimal(result['entered_quantity_per_unit'])==Decimal(amount)


@pytest.mark.parametrize('amount,entered,base', [
    ('1','кг','л'), ('1','м','м²'), ('1','рулон','м'), ('1','box','шт'),
    ('0.0001','г','кг'), ('0.001001','г','кг'), ('NaN','г','кг'),
    ('Infinity','г','кг'), (True,'г','кг'), ('-1','г','кг'), ('0','г','кг'),
    ('999999999999.999999','кг','кг'),
])
def test_rejects_incompatible_unknown_and_unrepresentable_base(amount,entered,base):
    with pytest.raises(ValueError): normalize_material_quantity(amount,entered,base)


def test_custom_catalog_identity_is_preserved_without_guessing_conversion():
    assert normalize_material_quantity('2','box','box')['conversion_factor']=='1'


@pytest.mark.parametrize('qty,price,entered,base', [('1.001','0.1','г','кг'),('1','0.000001','кг','г'),('1','NaN','г','кг')])
def test_receipt_conversion_rejects_quantity_and_inverse_rate_precision_loss(qty,price,entered,base):
    from db.material_units import convert_material_amount
    with pytest.raises(ValueError): convert_material_amount(qty,price,entered,base)
