"""Compliance status used by the public compliance page.

Data is loaded from ``compliance_status.json`` so legal/compliance owners can
update values without touching code.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


_DATA_PATH = Path(__file__).with_name("compliance_status.json")

_FALLBACK: dict[str, Any] = {
    "rkn": {
        "operator_inn": "071513433550",
        "status": "Требуется ручная проверка",
        "record_url": "https://pd.rkn.gov.ru",
        "status_hint": (
            "Актуальные сведения доступны в официальном реестре операторов персональных данных."
        ),
    },
    "localization": [
        {
            "direction": "База данных клиентов и учётные записи",
            "current_state": "Выполняется регулярная проверка провайдера.",
            "update_action": "Зафиксировать фактическую страну размещения основных БД и резервных копий.",
        },
        {
            "direction": "Логи приложений и веб-серверов",
            "current_state": "Хранение и доступ зависят от инфраструктуры провайдера.",
            "update_action": "Подтвердить, какие сервисы используются и где физически расположены логи.",
        },
        {
            "direction": "Резервные копии и точки восстановления",
            "current_state": "Требуется подтверждение по каждому бэкап-каналу.",
            "update_action": "Проверить локации бэкапов и срок хранения на каждом этапе цепочки.",
        },
        {
            "direction": "Почтовая доставка отчётов",
            "current_state": "Сервис доставки работает по инфраструктуре оператора при подписке.",
            "update_action": "Зафиксировать, передаются ли персональные данные за пределы РФ и на каких условиях.",
        },
        {
            "direction": "Потоки Telegram/внешние интеграции",
            "current_state": "Telegram временно отключён в сервисе.",
            "update_action": "Проверять условия обработки до подключения внешнего канала.",
        },
    ],
    "incident_steps": [
        "Немедленно ограничить распространение данных и приостановить рискованные процессы.",
        "Зафиксировать время инцидента, возможный объём затронутых данных и затронутые источники.",
        "Оценить признаки нарушения и при необходимости уведомить ответственных лиц по внутреннему регламенту.",
        "При выявлении неправомерной или случайной передачи либо доступа к персональным данным, повлёкшего нарушение прав субъектов, уведомить РКН в течение 24 часов, о результатах расследования — в течение 72 часов с момента выявления.",
    ],
    "incident_contact_email": "mvvorokov@yandex.ru",
    "next_steps": [
        "Обновить секцию трансграничной передачи после получения актуальной карты поставщиков.",
        "Проверять актуальность сведений оператора в РКН при изменении обработки данных.",
        "Проверить, что в политике не появились необоснованные или невалидные утверждения без подтверждения поставщиков.",
        "Подготовить журнал ответственных пересмотров и дату следующего контроля.",
    ],
}


def get_compliance_status() -> dict[str, Any]:
    """Return compliance context for ``/compliance``.

    If JSON parsing fails for any reason, return a safe fallback to keep the
    page renderable and avoid breaking unrelated flows.
    """

    try:
        raw = _DATA_PATH.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError):
        return _FALLBACK

    if not isinstance(data, dict):
        return _FALLBACK

    required_keys = {"rkn", "localization", "incident_steps", "incident_contact_email", "next_steps"}
    if not required_keys.issubset(data.keys()):
        return _FALLBACK
    if not isinstance(data["localization"], list) or len(data["localization"]) < 5:
        return _FALLBACK
    if not isinstance(data["incident_steps"], list) or len(data["incident_steps"]) < 4:
        return _FALLBACK
    if not isinstance(data["next_steps"], list) or not data["next_steps"]:
        return _FALLBACK

    return data
