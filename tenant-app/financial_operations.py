"""Interpret WB realization operations without rewriting the source journal."""
from __future__ import annotations

import re
from typing import Any


def financial_event_type(row: dict[str, Any]) -> str:
    # Document type also appears on compensations and service rows. Only an
    # actual sale/return changes product units and historical COGS.
    operation = str(row.get('operation_name') or '').strip().casefold()
    document = str(row.get('doc_type_name') or '').strip().casefold()
    kind = operation or document
    return {'продажа': 'sale', 'возврат': 'return'}.get(kind, '')


def is_return_document(row: dict[str, Any]) -> bool:
    return (str(row.get('doc_type_name') or '').strip().casefold() == 'возврат'
            or financial_event_type(row) == 'return')


def is_promotion_settlement(reason: Any) -> bool:
    # Restrict this to campaign promotion. Media/brand-zone and unknown fees
    # must stay in expenses unless their matching spend source is implemented.
    text = re.sub(r'[^\w]+', ' ', str(reason or '').casefold())
    return bool(re.search(r'\b(?:wb|вб) продвижение\b', text))
