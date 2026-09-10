"""Backing logic for the "Агенты" (Agents) screen.

Same role as today_service.py / overview_service.py / ads_service.py: it wraps
exactly what pages/agents_page.py does and returns JSON-serializable dicts for
the Next.js frontend. Nothing here touches Streamlit; pages/agents_page.py
stays live and unchanged. See
memory/project_marketshelper_frontend_migration.md.

Unlike the other *_service.py modules this one is not read-only. The Streamlit
page has three actions and they are ported verbatim:

  * refresh_recommendations() -- price_agent.evaluate + ad_agent.evaluate for
    every connected client (read-only fetches against WB/Ozon, writes only
    local agent_candidates rows).
  * apply_candidate()  -- the "Применить" button. Calls
    <module>.apply_candidate(..., dry_run=False), which performs a REAL write
    against the live marketplace API (a product's price, a campaign's budget).
    Exactly what the Streamlit button already does today; the frontend adds a
    confirmation dialog in front of it that Streamlit does not have.
  * reject_candidate()  -- the "Отклонить" button. Local store only.

Both action helpers only accept candidates that are still `pending`, mirroring
the fact that the Streamlit page only ever renders buttons for pending rows --
that also makes a double-submit from the browser a 404 instead of a second
marketplace write.
"""

from __future__ import annotations

import json
from typing import Any

from agents import ad_agent, price_agent
from agents.marketplaces.ozon_client import OzonAgentClient
from agents.marketplaces.wb_client import WBAgentClient
from agents.rules import AdRules, PriceRules
from agents.store import AgentStore
from agent_sync import wb_cost_lookup
from config import DB_PATH, get_ozon_credentials, get_token
from today_service import json_safe

# render_empty_state(...) calls in pages/agents_page.py, as data instead of
# markup -- wording kept verbatim.
EMPTY_STATE_NO_CLIENTS = {
    "title": "Ни один кабинет не подключён",
    "note": "Подключите токен WB и/или ключи Ozon Seller API в разделе «Настройки», затем вернитесь сюда.",
}

EMPTY_STATE_NO_PENDING = {
    "title": "Пока нет предложений",
    "note": (
        "Нажмите «Обновить рекомендации» — агенты предложат изменения, "
        "только если найдут повод (например, ДРР выше целевого)."
    ),
}

# render_section_header(...) tooltip above the pending list.
PENDING_SECTION_TOOLTIP = (
    "Рекомендации агентов по ценам и рекламным ставкам. Ничего не применяется само — "
    "каждую строку нужно одобрить или отклонить вручную."
)

# st.caption() inside the "История решений" expander when it is empty.
HISTORY_EMPTY_NOTE = "Пока нет обработанных предложений."

HISTORY_LIMIT = 50


class AgentsError(Exception):
    """Service-level failure the API layer turns into an HTTP status.

    Kept framework-free (no fastapi import here) so this module stays a plain
    service like the other *_service.py files.
    """

    def __init__(self, status_code: int, code: str):
        super().__init__(code)
        self.status_code = status_code
        self.code = code


def build_clients() -> dict[str, Any]:
    """pages/agents_page.py::_build_clients, unchanged."""
    clients: dict[str, Any] = {}
    wb_token = get_token()
    if wb_token:
        clients["wb"] = WBAgentClient(wb_token)
    ozon_creds = get_ozon_credentials()
    if ozon_creds:
        clients["ozon"] = OzonAgentClient(*ozon_creds)
    return clients


def _decode(raw: Any) -> Any:
    """current_value/proposed_value are stored as json.dumps() TEXT."""
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return raw


def _display(value: Any) -> str:
    """The Streamlit caption interpolates the decoded value straight into an
    f-string, so `str()` here keeps "1140.0"/"active"/"None" identical to what
    the current page shows -- JS's String() would render 1140.0 as "1140"."""
    return str(value)


def _caption(clients: dict[str, Any], cost_count: int) -> str:
    """The st.caption() under the page title, byte-for-byte."""
    return (
        "Подключено: " + ", ".join(sorted(k.upper() for k in clients))
        + f". Себестоимость WB: заполнена для {cost_count} товаров (Настройки → 3. Себестоимость)."
        + " Реклама Ozon требует отдельные ключи Performance API — если их нет, эта часть просто ничего не найдёт."
    )


def _pending_payload(candidate: dict, clients: dict[str, Any]) -> dict:
    current = _decode(candidate["current_value"])
    proposed = _decode(candidate["proposed_value"])
    return {
        "id": int(candidate["id"]),
        "created_at": candidate["created_at"],
        "agent": candidate["agent"],
        "marketplace": candidate["marketplace"],
        "target_id": candidate["target_id"],
        "target_name": candidate["target_name"],
        "action": candidate["action"],
        "current_value": current,
        "proposed_value": proposed,
        "current_display": _display(current),
        "proposed_display": _display(proposed),
        "reason": candidate["reason"],
        "status": candidate["status"],
        # Streamlit disables "Применить" when the candidate's marketplace has
        # no client (e.g. an old Ozon row while only WB is connected).
        "can_apply": candidate["marketplace"] in clients,
    }


def _history_payload(candidate: dict) -> dict:
    """One st.text() row from the "История решений" expander, pre-rendered.

    The Streamlit version prints the *raw* stored strings here (not the decoded
    values), so this keeps `current_value`/`proposed_value` undecoded on
    purpose -- the two lines must read identically.
    """
    return {
        "id": int(candidate["id"]),
        "status": candidate["status"],
        "text": (
            f"[{candidate['status']}] {str(candidate['created_at'])[:19]} "
            f"{candidate['marketplace']}/{candidate['agent']}/{candidate['action']} "
            f"{candidate['target_name'] or candidate['target_id']}: "
            f"{candidate['current_value']} -> {candidate['proposed_value']}"
        ),
    }


def compute_agents() -> dict:
    """GET /api/agents -- read-only snapshot of the whole page."""
    clients = build_clients()
    if not clients:
        return {
            "connected": [],
            "caption": "",
            "cost_count": 0,
            "empty_state": EMPTY_STATE_NO_CLIENTS,
            "pending_section": {"title": "Предложения (0)", "tooltip": PENDING_SECTION_TOOLTIP},
            "pending_empty_state": EMPTY_STATE_NO_PENDING,
            "pending": [],
            "history": [],
            "history_empty_note": HISTORY_EMPTY_NOTE,
        }

    store = AgentStore(DB_PATH)
    cost_count = len(wb_cost_lookup())
    pending = [_pending_payload(c, clients) for c in store.list_candidates(status="pending")]
    decided = [c for c in store.list_candidates(status=None) if c["status"] != "pending"][:HISTORY_LIMIT]

    return json_safe({
        "connected": sorted(k.upper() for k in clients),
        "caption": _caption(clients, cost_count),
        "cost_count": cost_count,
        "empty_state": None,
        "pending_section": {
            "title": f"Предложения ({len(pending)})",
            "tooltip": PENDING_SECTION_TOOLTIP,
        },
        "pending_empty_state": EMPTY_STATE_NO_PENDING,
        "pending": pending,
        "history": [_history_payload(c) for c in decided],
        "history_empty_note": HISTORY_EMPTY_NOTE,
    })


def refresh_recommendations() -> dict:
    """POST /api/agents/refresh -- pages/agents_page.py::_refresh_recommendations
    plus the success message the button builds afterwards.

    Hits the live WB/Ozon APIs (reads: prices, campaigns) and writes new
    agent_candidates rows locally. Never applies anything.
    """
    clients = build_clients()
    if not clients:
        raise AgentsError(409, "no_marketplace_connected")

    store = AgentStore(DB_PATH)
    warnings: list[str] = []
    for marketplace, client in clients.items():
        cost_lookup = wb_cost_lookup() if marketplace == "wb" else None
        try:
            price_agent.evaluate(client, store, PriceRules(), cost_lookup=cost_lookup)
        except Exception as exc:
            warnings.append(f"{marketplace}: цены — {exc}")
        try:
            ad_agent.evaluate(client, store, AdRules())
        except Exception as exc:
            warnings.append(f"{marketplace}: реклама — {exc}")

    return {
        "level": "success",
        "message": "Рекомендации обновлены" + (f". Пропущено: {'; '.join(warnings)}" if warnings else ""),
        "warnings": warnings,
    }


def _find_pending(store: AgentStore, candidate_id: int) -> dict:
    for row in store.list_candidates(status="pending"):
        if int(row["id"]) == int(candidate_id):
            return row
    raise AgentsError(404, "candidate_not_found_or_not_pending")


def apply_candidate(candidate_id: int) -> dict:
    """POST /api/agents/candidates/{id}/apply -- pages/agents_page.py::_apply.

    REAL marketplace write (dry_run=False), same as the Streamlit button.
    """
    clients = build_clients()
    if not clients:
        raise AgentsError(409, "no_marketplace_connected")

    store = AgentStore(DB_PATH)
    candidate = _find_pending(store, candidate_id)
    client = clients.get(candidate["marketplace"])
    if client is None:
        # The Streamlit button is rendered disabled in this case.
        raise AgentsError(409, "marketplace_not_connected")

    module = price_agent if candidate["agent"] == "price" else ad_agent
    try:
        result = module.apply_candidate(client, store, candidate, decided_by="human", dry_run=False)
    except Exception as exc:  # same catch the Streamlit button wraps _apply in
        return {"level": "error", "message": str(exc)}

    if result.ok:
        return {"level": "success", "message": f"Применено: {result.detail}"}
    return {"level": "error", "message": f"Не применено: {result.detail}"}


def reject_candidate(candidate_id: int) -> dict:
    """POST /api/agents/candidates/{id}/reject -- pages/agents_page.py::_reject.
    Local store only, no marketplace call."""
    store = AgentStore(DB_PATH)
    candidate = _find_pending(store, candidate_id)
    store.record_decision(candidate["id"], decided_by="human", outcome="rejected")
    return {"level": "success", "message": "Предложение отклонено"}
