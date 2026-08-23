"""Standalone entrypoint for personal WB/Ozon cabinets — no MARKETSHELPER required.

Reads config.yaml (see config.example.yaml) and runs both agents for
whichever marketplaces have credentials filled in. Dry-run by default.

    python standalone/run_agents.py --once
    python standalone/run_agents.py --loop --interval 60
    python standalone/run_agents.py --once --auto-apply   # применяет предложения (в рамках rules.py)
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tenant-app"))

from agents import ad_agent, price_agent
from agents.marketplaces.ozon_client import OzonAgentClient
from agents.marketplaces.wb_client import WBAgentClient
from agents.rules import AdRules, PriceRules
from agents.runner import run_once
from agents.store import AgentStore

CONFIG_PATH = Path(__file__).parent / "config.yaml"


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        raise SystemExit(
            f"{CONFIG_PATH} не найден. Скопируйте config.example.yaml -> config.yaml и заполните ключи."
        )
    return yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}


def main() -> int:
    parser = argparse.ArgumentParser(description="Standalone WB/Ozon price & ad agents")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--once", action="store_true")
    mode.add_argument("--loop", action="store_true")
    parser.add_argument("--interval", type=int, default=60)
    parser.add_argument("--auto-apply", action="store_true")
    parser.add_argument("--db", default=str(Path(__file__).parent / "agents_store.sqlite3"))
    args = parser.parse_args()

    config = load_config()
    store = AgentStore(args.db)

    clients = []
    wb_token = (config.get("wb") or {}).get("token")
    if wb_token:
        clients.append(WBAgentClient(wb_token))
    ozon_cfg = config.get("ozon") or {}
    if ozon_cfg.get("client_id") and ozon_cfg.get("api_key"):
        clients.append(OzonAgentClient(ozon_cfg["client_id"], ozon_cfg["api_key"]))

    if not clients:
        raise SystemExit("В config.yaml не заполнены ни WB, ни Ozon ключи — нечего запускать.")

    price_rules = PriceRules(**(config.get("price_agent") or {}))
    ad_rules = AdRules(**(config.get("ad_agent") or {}))

    import time

    while True:
        for client in clients:
            run_once(client, store, auto_apply=args.auto_apply, price_rules=price_rules, ad_rules=ad_rules)
        if args.once:
            return 0
        time.sleep(args.interval * 60)


if __name__ == "__main__":
    sys.exit(main())
