from __future__ import annotations

import json

import streamlit as st

from agents import ad_agent, price_agent
from agents.marketplaces.ozon_client import OzonAgentClient
from agents.marketplaces.wb_client import WBAgentClient
from agents.rules import AdRules, PriceRules
from agents.store import AgentStore
from config import DB_PATH, get_ozon_credentials, get_token
from ui_helpers import render_empty_state


def _build_clients() -> dict[str, object]:
    clients: dict[str, object] = {}
    wb_token = get_token()
    if wb_token:
        clients["wb"] = WBAgentClient(wb_token)
    ozon_creds = get_ozon_credentials()
    if ozon_creds:
        clients["ozon"] = OzonAgentClient(*ozon_creds)
    return clients


def _refresh_recommendations(clients: dict[str, object], store: AgentStore) -> list[str]:
    warnings: list[str] = []
    for marketplace, client in clients.items():
        try:
            price_agent.evaluate(client, store, PriceRules())
        except Exception as exc:
            warnings.append(f"{marketplace}: цены — {exc}")
        try:
            ad_agent.evaluate(client, store, AdRules())
        except Exception as exc:
            warnings.append(f"{marketplace}: реклама — {exc}")
    return warnings


def _apply(agent: str, client, store: AgentStore, candidate: dict) -> None:
    module = price_agent if agent == "price" else ad_agent
    result = module.apply_candidate(client, store, candidate, decided_by="human", dry_run=False)
    if result.ok:
        st.session_state["agents_page_message"] = ("success", f"Применено: {result.detail}")
    else:
        st.session_state["agents_page_message"] = ("error", f"Не применено: {result.detail}")


def _reject(store: AgentStore, candidate: dict) -> None:
    store.record_decision(candidate["id"], decided_by="human", outcome="rejected")
    st.session_state["agents_page_message"] = ("success", "Предложение отклонено")


def render(ctx: dict) -> None:
    st.markdown('<div class="wb-title">Агенты</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="wb-subtitle">Рекомендации по ценам и рекламе для WB и Ozon — ничего не меняется без вашего явного подтверждения</div>',
        unsafe_allow_html=True,
    )

    message = st.session_state.pop("agents_page_message", None)
    if message:
        level, text = message
        (st.success if level == "success" else st.error)(text)

    clients = _build_clients()
    if not clients:
        render_empty_state(
            "Ни один кабинет не подключён",
            "Подключите токен WB и/или ключи Ozon Seller API в разделе «Настройки», затем вернитесь сюда.",
        )
        return

    store = AgentStore(DB_PATH)

    st.caption(
        "Подключено: " + ", ".join(sorted(k.upper() for k in clients))
        + ". Реклама Ozon требует отдельные ключи Performance API — если их нет, эта часть просто ничего не найдёт."
    )

    if st.button("Обновить рекомендации", type="primary"):
        with st.spinner("Запрашиваю цены и рекламные кампании..."):
            warnings = _refresh_recommendations(clients, store)
        st.session_state["agents_page_message"] = (
            "success", "Рекомендации обновлены" + (f". Пропущено: {'; '.join(warnings)}" if warnings else "")
        )
        st.cache_data.clear()
        st.rerun()

    pending = store.list_candidates(status="pending")
    st.markdown(f"### Предложения ({len(pending)})")
    if not pending:
        render_empty_state(
            "Пока нет предложений",
            "Нажмите «Обновить рекомендации» — агенты предложат изменения, только если найдут повод (например, ДРР выше целевого).",
        )
    else:
        for candidate in pending:
            client = clients.get(candidate["marketplace"])
            current = json.loads(candidate["current_value"]) if candidate["current_value"] else None
            proposed = json.loads(candidate["proposed_value"]) if candidate["proposed_value"] else None
            with st.container(border=True):
                cols = st.columns([3, 1, 1])
                with cols[0]:
                    st.markdown(
                        f"**{candidate['marketplace'].upper()} · {candidate['agent']} · {candidate['action']}** "
                        f"— {candidate['target_name'] or candidate['target_id']}"
                    )
                    st.caption(f"{current} → {proposed}  \n{candidate['reason']}")
                with cols[1]:
                    if st.button("Применить", key=f"agent_apply_{candidate['id']}", type="primary", use_container_width=True, disabled=client is None):
                        try:
                            _apply(candidate["agent"], client, store, candidate)
                        except Exception as exc:
                            st.session_state["agents_page_message"] = ("error", str(exc))
                        st.cache_data.clear()
                        st.rerun()
                with cols[2]:
                    if st.button("Отклонить", key=f"agent_reject_{candidate['id']}", use_container_width=True):
                        _reject(store, candidate)
                        st.cache_data.clear()
                        st.rerun()

    with st.expander("История решений"):
        decided = [c for c in store.list_candidates(status=None) if c["status"] != "pending"][:50]
        if not decided:
            st.caption("Пока нет обработанных предложений.")
        else:
            for candidate in decided:
                st.text(
                    f"[{candidate['status']}] {candidate['created_at'][:19]} "
                    f"{candidate['marketplace']}/{candidate['agent']}/{candidate['action']} "
                    f"{candidate['target_name'] or candidate['target_id']}: "
                    f"{candidate['current_value']} -> {candidate['proposed_value']}"
                )
