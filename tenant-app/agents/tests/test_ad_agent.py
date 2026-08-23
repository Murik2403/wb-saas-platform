import json

from agents import ad_agent
from agents.marketplaces.base import Campaign
from agents.rules import AdRules
from agents.store import AgentStore
from agents.tests.mock_client import MockClient


def make_store(tmp_path) -> AgentStore:
    return AgentStore(tmp_path / "test_agents.sqlite3")


def test_no_change_within_target_drr_band(tmp_path):
    client = MockClient(campaigns=[
        Campaign(campaign_id="c1", name="Кампания", status="active", daily_budget=1000, spend_7d=1000, revenue_7d=10000)  # drr 10%
    ])
    store = make_store(tmp_path)
    ids = ad_agent.evaluate(client, store, AdRules(max_drr_pct=25, min_drr_pct=5))
    assert ids == []


def test_reduces_budget_when_drr_too_high(tmp_path):
    client = MockClient(campaigns=[
        Campaign(campaign_id="c1", name="Кампания", status="active", daily_budget=1000, spend_7d=4000, revenue_7d=10000)  # drr 40%
    ])
    store = make_store(tmp_path)
    ids = ad_agent.evaluate(client, store, AdRules(max_drr_pct=25, min_drr_pct=5, max_budget_step_pct=50))
    assert len(ids) == 1
    candidate = store.list_candidates(status="pending")[0]
    assert candidate["action"] == "set_budget"
    proposed = json.loads(candidate["proposed_value"])
    assert proposed < 1000


def test_pauses_when_drr_too_high_and_no_budget(tmp_path):
    client = MockClient(campaigns=[
        Campaign(campaign_id="c1", name="Кампания", status="active", daily_budget=None, spend_7d=4000, revenue_7d=10000)
    ])
    store = make_store(tmp_path)
    ids = ad_agent.evaluate(client, store, AdRules(max_drr_pct=25))
    assert len(ids) == 1
    candidate = store.list_candidates(status="pending")[0]
    assert candidate["action"] == "pause"


def test_increases_budget_when_drr_very_efficient(tmp_path):
    client = MockClient(campaigns=[
        Campaign(campaign_id="c1", name="Кампания", status="active", daily_budget=1000, spend_7d=300, revenue_7d=10000)  # drr 3%
    ])
    store = make_store(tmp_path)
    ids = ad_agent.evaluate(client, store, AdRules(max_drr_pct=25, min_drr_pct=5, max_budget_step_pct=50))
    assert len(ids) == 1
    candidate = store.list_candidates(status="pending")[0]
    assert candidate["action"] == "set_budget"
    proposed = json.loads(candidate["proposed_value"])
    assert proposed > 1000


def test_ignores_paused_campaigns(tmp_path):
    client = MockClient(campaigns=[
        Campaign(campaign_id="c1", name="Кампания", status="paused", daily_budget=1000, spend_7d=4000, revenue_7d=10000)
    ])
    store = make_store(tmp_path)
    ids = ad_agent.evaluate(client, store, AdRules(max_drr_pct=25))
    assert ids == []


def test_apply_candidate_dry_run_does_not_hit_network(tmp_path):
    client = MockClient(campaigns=[
        Campaign(campaign_id="c1", name="Кампания", status="active", daily_budget=None, spend_7d=4000, revenue_7d=10000)
    ])
    store = make_store(tmp_path)
    ad_agent.evaluate(client, store, AdRules(max_drr_pct=25))
    candidate = store.list_candidates(status="pending")[0]

    result = ad_agent.apply_candidate(client, store, candidate, decided_by="human", dry_run=True)
    assert result.ok and result.dry_run
    assert client.calls == [("pause_campaign", "c1", True)]
