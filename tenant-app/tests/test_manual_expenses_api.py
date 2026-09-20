from __future__ import annotations

from unittest.mock import patch

from fastapi.testclient import TestClient

from api_server import app


def test_manual_expenses_api_contract() -> None:
    client = TestClient(app)
    with patch("api_server.save_manual_expense", return_value=7) as save:
        response = client.post(
            "/api/manual-expenses",
            json={
                "nm_id": 123, "article": "taxes", "amount_rub": 600,
                "allocation_basis": "total", "source": "manual",
                "period_start": "2026-09-01", "period_end": "2026-09-30",
            },
        )
    assert response.status_code == 200
    assert response.json() == {"id": 7, "message": "Расход сохранён"}
    assert save.call_args.kwargs["nm_id"] == 123

    with patch("api_server.read_manual_expenses", return_value=[{"id": 7, "nm_id": 123}]) as read:
        response = client.get("/api/manual-expenses?nm_id=123")
    assert response.status_code == 200
    assert response.json() == {"items": [{"id": 7, "nm_id": 123}]}
    read.assert_called_once_with(nm_id=123, start=None, end=None)
