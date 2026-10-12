from decimal import Decimal

from price_stock_sync.models import ChangeKind, ErpRow, SkuState, Status
from price_stock_sync.planner import build_plan


def row(sku="A-1", price="100", list_price=None, stock=5, warehouse="1", line=2):
    price = Decimal(price)
    return ErpRow(line, sku, price, Decimal(list_price) if list_price else price, stock, warehouse)


def state(price="100", list_price=None, stock=None):
    price = Decimal(price)
    return SkuState("10", price, Decimal(list_price) if list_price else price, stock or {"1": 5})


def test_nothing_to_do_when_erp_and_vtex_match():
    plan = build_plan([row()], {"A-1": state()})

    assert plan.changes == []
    assert plan.unchanged == 2  # precio e inventario


def test_detects_price_and_stock_changes():
    plan = build_plan([row(price="120", stock=8)], {"A-1": state()})

    price, stock = plan.changes
    assert price.kind == ChangeKind.PRICE
    assert price.payload == {"basePrice": 120.0, "listPrice": 120.0}
    assert (price.before, price.after) == ("100.00 / 100.00", "120.00 / 120.00")
    assert stock.kind == ChangeKind.STOCK
    assert stock.payload == {"unlimitedQuantity": False, "quantity": 8}


def test_a_change_only_in_list_price_still_counts():
    plan = build_plan([row(price="100", list_price="150")], {"A-1": state(list_price="120")})

    assert len(plan.changes) == 1
    assert plan.changes[0].payload["listPrice"] == 150.0


def test_blocks_suspicious_price_drops_unless_forced():
    rows = [row(price="99")]
    current = {"A-1": state(price="999")}

    blocked = build_plan(rows, current).changes[0]
    forced = build_plan(rows, current, force=True).changes[0]

    assert blocked.status == Status.BLOCKED
    assert "Baja 90%" in blocked.detail
    assert forced.status == Status.PENDING


def test_drop_threshold_is_configurable():
    plan = build_plan([row(price="80")], {"A-1": state(price="100")}, max_price_drop=Decimal("0.1"))

    assert plan.changes[0].status == Status.BLOCKED


def test_list_price_lower_than_price_is_fixed_with_a_warning():
    plan = build_plan([row(price="100", list_price="90")], {"A-1": state(price="80")})

    assert plan.changes[0].payload == {"basePrice": 100.0, "listPrice": 100.0}
    assert "precio de lista" in plan.warnings[0]


def test_negative_stock_is_sent_as_zero():
    plan = build_plan([row(stock=-4)], {"A-1": state()})

    assert plan.changes[0].payload["quantity"] == 0
    assert "negativas" in plan.warnings[0]


def test_unknown_skus_are_skipped():
    plan = build_plan([row(sku="NO-EXISTE")], {"NO-EXISTE": None})

    assert plan.changes[0].status == Status.SKIPPED
    assert plan.changes[0].kind == ChangeKind.SKU


def test_price_is_evaluated_once_per_sku_even_with_several_warehouses():
    rows = [row(price="120", stock=1, warehouse="1"), row(price="120", stock=2, warehouse="2", line=3)]

    plan = build_plan(rows, {"A-1": state(stock={"1": 1, "2": 0})})

    kinds = [(c.kind, c.warehouse) for c in plan.changes]
    assert kinds == [(ChangeKind.PRICE, None), (ChangeKind.STOCK, "2")]


def test_new_warehouse_for_a_sku_shows_as_empty_before():
    plan = build_plan([row(stock=3, warehouse="cedis-norte")], {"A-1": state()})

    assert plan.changes[0].before == "-"
    assert plan.changes[0].warehouse == "cedis-norte"
