"""Background loop that only proposes agent recommendations, never applies them.

Same shape as sync.py's loop()/main() (subprocess launched by launcher.py
alongside it). Real application of a candidate happens exclusively through
a human clicking "Применить" on the "Агенты" page — this loop calls
evaluate() only, never apply_candidate().
"""
from __future__ import annotations

import argparse
import time
from datetime import datetime

from agents import ad_agent, price_agent
from agents.marketplaces.ozon_client import OzonAgentClient
from agents.marketplaces.wb_client import WBAgentClient
from agents.rules import AdRules, PriceRules
from agents.store import AgentStore
from config import DB_PATH, get_ozon_credentials, get_token, load_settings
from db import read_table


def wb_cost_lookup() -> dict[str, float]:
    """nm_id -> cost_per_wb_unit, from MARKETSHELPER's own себестоимость table
    (Настройки → 3. Себестоимость) -- the WB API itself never provides cost."""
    costs = read_table("costs")
    if costs.empty:
        return {}
    lookup = {}
    for _, row in costs.iterrows():
        cost = row.get("cost_per_wb_unit")
        if cost and float(cost) > 0:
            lookup[str(int(row["nm_id"]))] = float(cost)
    return lookup


def run_once() -> dict:
    store = AgentStore(DB_PATH)
    counts: dict[str, int] = {}

    wb_token = get_token()
    if wb_token:
        client = WBAgentClient(wb_token)
        counts["wb_price"] = len(price_agent.evaluate(client, store, PriceRules(), cost_lookup=wb_cost_lookup()))
        counts["wb_ad"] = len(ad_agent.evaluate(client, store, AdRules()))

    ozon_creds = get_ozon_credentials()
    if ozon_creds:
        client = OzonAgentClient(*ozon_creds)
        counts["ozon_price"] = len(price_agent.evaluate(client, store, PriceRules()))
        try:
            counts["ozon_ad"] = len(ad_agent.evaluate(client, store, AdRules()))
        except Exception:
            # Ozon Performance API needs separate credentials the seller may
            # not have yet -- missing ad access must not break price agent runs.
            counts["ozon_ad"] = 0

    return counts


def loop() -> None:
    while True:
        settings = load_settings()
        interval = max(15, int(settings.get("agent_interval_minutes", 60)))
        try:
            result = run_once()
            print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] Рекомендации агентов: {result}", flush=True)
        except Exception as exc:
            print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] Ошибка агентов: {exc}", flush=True)
        time.sleep(interval * 60)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--loop", action="store_true")
    args = parser.parse_args()
    if args.loop:
        loop()
    else:
        print(run_once())


if __name__ == "__main__":
    main()
