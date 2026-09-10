"""Tenant-local executor for the dashboard action plan.

Safe tenant-local operations such as creating a verified backup can run
automatically. Marketplace writes keep their separate confirmation flows, so
a plan item can never silently change a seller's account.
"""
from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any


AI_EXECUTOR = "AI-исполнитель"
TERMINAL_STATUSES = {"done", "cancelled", "blocked"}


class ActionPlanExecutor:
    def __init__(
        self,
        db_path: Path,
        inspect_campaign: Callable[[str], dict[str, Any] | None],
        execute_operation: Callable[[str, str], tuple[str, str] | None] | None = None,
    ) -> None:
        self.db_path = Path(db_path)
        self.inspect_campaign = inspect_campaign
        self.execute_operation = execute_operation

    @staticmethod
    def _history(raw: object) -> list[dict[str, Any]]:
        try:
            history = json.loads(str(raw or "[]"))
            return history if isinstance(history, list) else []
        except (TypeError, ValueError):
            return []

    @staticmethod
    def _campaign_id(title: str, sku: str) -> str:
        match = re.search(r"\d{3,}", f"{sku} {title}")
        return match.group(0) if match else ""

    @staticmethod
    def _campaign_result(campaign_id: str, metrics: dict[str, Any]) -> str:
        parts = [
            f"Кампания {campaign_id} проверена.",
            f"Расход: {metrics.get('spend', '—')}",
            f"ДРР: {metrics.get('drr', '—')}",
            f"Выручка: {metrics.get('revenue', '—')}",
        ]
        if metrics.get("orders") not in (None, ""):
            parts.append(f"Заказы: {metrics['orders']}")
        return " ".join(parts)

    def run(self, item_id: int) -> dict[str, Any]:
        """Claim and execute a single task in its own tenant database."""
        now = datetime.now().isoformat(timespec="seconds")
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM redesign_action_plan WHERE id=?", (int(item_id),)).fetchone()
            if row is None:
                raise LookupError("action_plan_item_not_found")
            if str(row["status"] or "") in TERMINAL_STATUSES:
                return dict(row)

            history = self._history(row["history"])
            history.append({"status": "in_progress", "at": now, "note": "Задача принята AI-исполнителем"})
            conn.execute(
                """
                UPDATE redesign_action_plan
                   SET status='in_progress', assignee=?, executor=?, started_at=COALESCE(started_at, ?),
                       history=?, updated_at=?
                 WHERE id=?
                """,
                (AI_EXECUTOR, AI_EXECUTOR, now, json.dumps(history, ensure_ascii=False), now, int(item_id)),
            )

            title = str(row["title"] or "").strip().lower()
            sku = str(row["sku"] or "")
            operation_result = self.execute_operation(str(row["title"] or ""), sku) if self.execute_operation else None
            if operation_result is not None:
                final_status, result = operation_result
            elif title.startswith("провер"):
                campaign_id = self._campaign_id(str(row["title"] or ""), sku)
                if not campaign_id:
                    final_status = "blocked"
                    result = "AI-исполнитель не нашёл номер кампании в задаче. Укажите кампанию в поле SKU."
                else:
                    try:
                        metrics = self.inspect_campaign(campaign_id)
                    except Exception:
                        metrics = None
                    if metrics is None:
                        final_status = "blocked"
                        result = f"Кампания {campaign_id} не найдена в последнем сохранённом срезе рекламы. Сначала обновите данные WB."
                    else:
                        final_status = "done"
                        result = self._campaign_result(campaign_id, metrics)
            else:
                final_status = "blocked"
                result = (
                    "AI-исполнитель разобрал задачу, но для её выполнения нужны данные или отдельное подтверждение. "
                    "Проверьте детали задачи и добавьте недостающие вводные."
                )

            finished_at = datetime.now().isoformat(timespec="seconds")
            history.append({"status": final_status, "at": finished_at, "note": result})
            conn.execute(
                """
                UPDATE redesign_action_plan
                   SET status=?, result=?, completed_at=?, history=?, updated_at=?
                 WHERE id=?
                """,
                (final_status, result, finished_at, json.dumps(history, ensure_ascii=False), finished_at, int(item_id)),
            )
            completed = conn.execute("SELECT * FROM redesign_action_plan WHERE id=?", (int(item_id),)).fetchone()
        return dict(completed)
