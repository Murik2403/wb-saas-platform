import json

from agents import price_agent
from agents.marketplaces.base import PriceRow
from agents.rules import PriceRules
from agents.store import AgentStore
from agents.tests.mock_client import MockClient


def make_store(tmp_path) -> AgentStore:
    return AgentStore(tmp_path / "test_agents.sqlite3")


def test_no_change_when_price_already_optimal(tmp_path):
    client = MockClient(prices=[PriceRow(sku="A1", name="Товар", current_price=1000, cost=700, competitor_price=None)])
    store = make_store(tmp_path)
    ids = price_agent.evaluate(client, store, PriceRules())
    assert ids == []


def test_undercuts_competitor_within_margin(tmp_path):
    client = MockClient(prices=[
        PriceRow(sku="A1", name="Товар", current_price=1000, cost=700, competitor_price=950)
    ])
    store = make_store(tmp_path)
    ids = price_agent.evaluate(client, store, PriceRules(min_margin_pct=15, max_step_pct=50))
    assert len(ids) == 1
    candidate = store.list_candidates(status="pending")[0]
    proposed = json.loads(candidate["proposed_value"])
    # 950 * 0.99 = 940.5, above the 700*1.15=805 margin floor
    assert proposed == 940.5


def test_never_proposes_below_margin_floor(tmp_path):
    client = MockClient(prices=[
        PriceRow(sku="A1", name="Товар", current_price=1000, cost=700, competitor_price=500)
    ])
    store = make_store(tmp_path)
    ids = price_agent.evaluate(client, store, PriceRules(min_margin_pct=15, max_step_pct=100))
    assert len(ids) == 1
    candidate = store.list_candidates(status="pending")[0]
    proposed = json.loads(candidate["proposed_value"])
    assert proposed >= 700 * 1.15


def test_step_is_clamped_per_cycle(tmp_path):
    client = MockClient(prices=[
        PriceRow(sku="A1", name="Товар", current_price=1000, cost=700, competitor_price=500)
    ])
    store = make_store(tmp_path)
    ids = price_agent.evaluate(client, store, PriceRules(min_margin_pct=0, max_step_pct=5))
    assert len(ids) == 1
    candidate = store.list_candidates(status="pending")[0]
    proposed = json.loads(candidate["proposed_value"])
    assert proposed == 950.0  # 1000 - 5%


def test_apply_candidate_dry_run_does_not_hit_network(tmp_path):
    client = MockClient(prices=[
        PriceRow(sku="A1", name="Товар", current_price=1000, cost=700, competitor_price=900)
    ])
    store = make_store(tmp_path)
    price_agent.evaluate(client, store, PriceRules(min_margin_pct=15, max_step_pct=50))
    candidate = store.list_candidates(status="pending")[0]

    result = price_agent.apply_candidate(client, store, candidate, decided_by="human", dry_run=True)
    assert result.ok and result.dry_run
    assert client.calls == [("set_price", "A1", json.loads(candidate["proposed_value"]), True)]

    remaining_pending = store.list_candidates(status="pending")
    assert remaining_pending == []
    applied = store.list_candidates(status="applied")
    assert len(applied) == 1
