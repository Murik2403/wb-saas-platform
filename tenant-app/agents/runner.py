"""CLI entrypoint for the price/ad agents.

Usage:
    python -m agents.runner --marketplace wb --once --dry-run
    python -m agents.runner --marketplace ozon --loop --interval 60

Dry-run is the default everywhere; --auto-apply is required to let a
--loop run actually call the marketplace's write endpoints, and even
then only within the safety bounds in rules.py.
"""
from __future__ import annotations

import argparse
import sys
import time

from . import ad_agent, price_agent
from .marketplaces.base import MarketplaceClient
from .marketplaces.ozon_client import OzonAgentClient
from .marketplaces.wb_client import WBAgentClient
from .rules import AdRules, PriceRules
from .store import AgentStore


def build_client(marketplace: str, *, wb_token: str | None, ozon_client_id: str | None, ozon_api_key: str | None) -> MarketplaceClient:
    if marketplace == "wb":
        if not wb_token:
            raise SystemExit("--wb-token (или WB_API_TOKEN) обязателен для --marketplace wb")
        return WBAgentClient(wb_token)
    if marketplace == "ozon":
        if not ozon_client_id or not ozon_api_key:
            raise SystemExit("--ozon-client-id/--ozon-api-key (или OZON_CLIENT_ID/OZON_API_KEY) обязательны для --marketplace ozon")
        return OzonAgentClient(ozon_client_id, ozon_api_key)
    raise SystemExit(f"неизвестный маркетплейс: {marketplace}")


def run_once(
    client: MarketplaceClient, store: AgentStore, *, auto_apply: bool,
    price_rules: PriceRules = PriceRules(), ad_rules: AdRules = AdRules(),
) -> None:
    price_ids = price_agent.evaluate(client, store, price_rules)
    ad_ids = ad_agent.evaluate(client, store, ad_rules)
    print(f"[{client.marketplace}] предложено: {len(price_ids)} цен, {len(ad_ids)} рекламных решений")

    if not auto_apply:
        print("dry-run/recommend-only: изменения не применены, см. agent_candidates в БД")
        return

    for candidate in store.list_candidates(status="pending"):
        agent_module = price_agent if candidate["agent"] == "price" else ad_agent
        result = agent_module.apply_candidate(client, store, candidate, decided_by="auto", dry_run=False)
        print(f"  applied candidate #{candidate['id']}: ok={result.ok} {result.detail}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="WB/Ozon price & ad agents")
    parser.add_argument("--marketplace", choices=["wb", "ozon"], required=True)
    parser.add_argument("--db", default="agents_store.sqlite3", help="путь к SQLite-файлу агента")
    parser.add_argument("--wb-token", default=None)
    parser.add_argument("--ozon-client-id", default=None)
    parser.add_argument("--ozon-api-key", default=None)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--once", action="store_true")
    mode.add_argument("--loop", action="store_true")
    parser.add_argument("--interval", type=int, default=60, help="минуты между циклами в --loop")
    parser.add_argument(
        "--auto-apply", action="store_true",
        help="применять предложения без подтверждения человеком (в рамках safety-рамок rules.py)",
    )
    args = parser.parse_args(argv)

    client = build_client(
        args.marketplace, wb_token=args.wb_token,
        ozon_client_id=args.ozon_client_id, ozon_api_key=args.ozon_api_key,
    )
    store = AgentStore(args.db)

    if args.once:
        run_once(client, store, auto_apply=args.auto_apply)
        return 0

    print(f"[{client.marketplace}] loop запущен, интервал {args.interval} мин, auto_apply={args.auto_apply}")
    while True:
        run_once(client, store, auto_apply=args.auto_apply)
        time.sleep(args.interval * 60)


if __name__ == "__main__":
    sys.exit(main())
