from agents.rules import clamp_budget_step, clamp_price_step, floor_price_by_margin


def test_clamp_price_step_limits_upward_move():
    assert clamp_price_step(1000, 2000, max_step_pct=5) == 1050.0


def test_clamp_price_step_limits_downward_move():
    assert clamp_price_step(1000, 500, max_step_pct=5) == 950.0


def test_clamp_price_step_allows_small_move():
    assert clamp_price_step(1000, 1020, max_step_pct=5) == 1020.0


def test_clamp_budget_step_never_goes_negative():
    assert clamp_budget_step(100, -500, max_step_pct=50) == 50.0


def test_floor_price_by_margin_none_cost():
    assert floor_price_by_margin(None, 15) is None


def test_floor_price_by_margin_computes_correctly():
    assert floor_price_by_margin(700, 15) == 805.0
