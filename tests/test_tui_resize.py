from munazzim.tui.app import MunazzimApp


def test_horizontal_ratio_adjustment_keeps_total():
    app = MunazzimApp()
    # prevent calls to Textual's query_one during tests
    app._apply_layout_ratios = lambda: None
    initial_plan = app._plan_column_fr
    initial_side = app._side_column_fr
    total = initial_plan + initial_side

    app._adjust_horizontal_ratio(0.5)
    assert app._plan_column_fr > initial_plan
    assert abs((app._plan_column_fr + app._side_column_fr) - total) < 1e-6

    app._adjust_horizontal_ratio(-0.75)
    assert app._plan_column_fr < initial_plan
    assert abs((app._plan_column_fr + app._side_column_fr) - total) < 1e-6


def test_vertical_ratio_adjustment_clamps_minimum():
    app = MunazzimApp()
    app._apply_layout_ratios = lambda: None
    total = app._plan_table_fr + app._week_table_fr

    # Try to reduce plan table a lot
    app._adjust_vertical_ratio(-10.0)
    assert app._plan_table_fr >= 0.4
    assert abs((app._plan_table_fr + app._week_table_fr) - total) < 1e-6

    # Try to increase plan table a lot
    app._adjust_vertical_ratio(10.0)
    assert app._week_table_fr >= 0.4
    assert abs((app._plan_table_fr + app._week_table_fr) - total) < 1e-6


def test_ctrl_j_k_bindings_swapped():
    # validate ctrl+j/k match desired actions
    # Build a lookup by key for quick checks (avoid importing textual.binding in tests)
    key_to_action = {}
    for b in MunazzimApp.BINDINGS:
        key = getattr(b, "key", None)
        action = getattr(b, "action", None)
        if key:
            key_to_action[key] = action

    # ctrl+j should be mapped to resize_up (decrease height), ctrl+k -> resize_down
    assert key_to_action.get("ctrl+j") == "resize_up"
    assert key_to_action.get("ctrl+k") == "resize_down"


def test_set_side_half_sets_equal_columns():
    app = MunazzimApp()
    app._apply_layout_ratios = lambda: None
    # Make sure set_side_half sets left and right columns to equal share
    initial_total = app._column_total_fr
    app.action_set_side_half()
    assert app._plan_column_fr == app._side_column_fr
    assert abs(app._plan_column_fr + app._side_column_fr - initial_total) < 1e-6


def test_default_side_is_half():
    app = MunazzimApp()
    # defaults were changed to equal columns, so right-hand should be half
    assert app._plan_column_fr == app._side_column_fr
    assert abs(app._plan_column_fr + app._side_column_fr - app._column_total_fr) < 1e-6
