"""Compara lo que dice el ERP contra lo que tiene VTEX y decide qué cambiar.

No hace llamadas a ninguna API: recibe datos y regresa un plan. Así se puede probar
cada regla con datos inventados y revisar el plan (--dry-run) antes de tocar la tienda.
"""

from __future__ import annotations

from decimal import Decimal

from .models import Change, ChangeKind, ErpRow, SkuState, Status, SyncPlan


def build_plan(
    rows: list[ErpRow],
    current: dict[str, SkuState | None],
    *,
    max_price_drop: Decimal = Decimal("0.5"),
    force: bool = False,
) -> SyncPlan:
    plan = SyncPlan()
    prices_seen: set[str] = set()

    for row in rows:
        state = current.get(row.sku_ref)
        if state is None:
            plan.changes.append(
                Change(row.sku_ref, None, ChangeKind.SKU, "-", "-", Status.SKIPPED, "El SKU no existe en VTEX")
            )
            continue

        # El precio es por SKU, no por almacén: si el SKU viene en varios almacenes, se toma la primera fila
        if row.sku_ref not in prices_seen:
            prices_seen.add(row.sku_ref)
            price_change = _plan_price(row, state, max_price_drop, force, plan)
            if price_change:
                plan.changes.append(price_change)
            else:
                plan.unchanged += 1

        stock_change = _plan_stock(row, state, plan)
        if stock_change:
            plan.changes.append(stock_change)
        else:
            plan.unchanged += 1

    return plan


def _plan_price(row: ErpRow, state: SkuState, max_drop: Decimal, force: bool, plan: SyncPlan) -> Change | None:
    list_price = row.list_price
    if list_price < row.price:
        plan.warnings.append(
            f"{row.sku_ref}: el precio de lista ({list_price}) es menor al de venta ({row.price}); se usa el de venta"
        )
        list_price = row.price

    if state.base_price == row.price and state.list_price == list_price:
        return None

    change = Change(
        sku_ref=row.sku_ref,
        sku_id=state.sku_id,
        kind=ChangeKind.PRICE,
        before=_price_label(state.base_price, state.list_price),
        after=_price_label(row.price, list_price),
        payload={"basePrice": float(row.price), "listPrice": float(list_price)},
    )

    # Un precio que baja a la mitad casi siempre es un error de captura (99 en lugar de 999)
    if state.base_price and row.price < state.base_price * (1 - max_drop):
        drop = (1 - row.price / state.base_price) * 100
        if force:
            change.detail = f"Baja {drop:.0f}%, aplicado con --force"
        else:
            change.status = Status.BLOCKED
            change.detail = f"Baja {drop:.0f}%. Revísalo y corre con --force si es correcto"

    return change


def _plan_stock(row: ErpRow, state: SkuState, plan: SyncPlan) -> Change | None:
    quantity = row.stock
    if quantity < 0:
        plan.warnings.append(f"{row.sku_ref}: existencias negativas en el ERP ({quantity}); se mandan como 0")
        quantity = 0

    current = state.stock_by_warehouse.get(row.warehouse)
    if current == quantity:
        return None

    return Change(
        sku_ref=row.sku_ref,
        sku_id=state.sku_id,
        kind=ChangeKind.STOCK,
        before="-" if current is None else str(current),
        after=str(quantity),
        payload={"unlimitedQuantity": False, "quantity": quantity},
        warehouse=row.warehouse,
    )


def _price_label(price: Decimal | None, list_price: Decimal | None) -> str:
    """'1,299.00 / 1,599.00' = precio de venta / precio de lista."""
    if price is None:
        return "-"
    return f"{price:,.2f} / {(list_price or price):,.2f}"
