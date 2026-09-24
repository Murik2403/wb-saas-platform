from __future__ import annotations

import sys

import pytest


@pytest.fixture(autouse=True)
def clear_dashboard_calculation_caches():
    """Keep API caches isolated across tests that load separate app modules."""
    def clear_loaded_api_caches() -> None:
        for module in tuple(sys.modules.values()):
            clear = getattr(module, "_clear_dashboard_caches", None)
            if callable(clear):
                clear()

    clear_loaded_api_caches()
    yield
    clear_loaded_api_caches()
