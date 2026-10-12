"""Junta las piezas: consulta VTEX, arma el plan y (si se pide) lo aplica."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

from .models import Change, ChangeKind, ErpRow, SkuState, Status, SyncPlan
from .planner import build_plan
from .vtex import VtexClient, VtexError

log = logging.getLogger(__name__)

# Pocas conexiones a la vez: VTEX tiene rate limit y no queremos competir con el tráfico de la tienda
DEFAULT_WORKERS = 4


def fetch_current_state(
    client: VtexClient, sku_refs: list[str], workers: int = DEFAULT_WORKERS
) -> dict[str, SkuState | None]:
    unique_refs = list(dict.fromkeys(sku_refs))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        states = list(pool.map(client.find_sku_state, unique_refs))
    return dict(zip(unique_refs, states))


def apply_plan(client: VtexClient, plan: SyncPlan, workers: int = DEFAULT_WORKERS) -> None:
    pending = plan.by_status(Status.PENDING)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(lambda change: _apply_change(client, change), pending))


def _apply_change(client: VtexClient, change: Change) -> None:
    assert change.sku_id is not None
    try:
        if change.kind == ChangeKind.PRICE:
            client.update_price(change.sku_id, change.payload)
        else:
            client.update_stock(change.sku_id, change.warehouse or "1", change.payload)
        change.status = Status.APPLIED
    except VtexError as error:
        change.status = Status.FAILED
        change.detail = str(error)
        log.warning("No se pudo actualizar %s de %s: %s", change.kind.value, change.sku_ref, error)


def run_sync(
    client: VtexClient,
    rows: list[ErpRow],
    *,
    apply: bool,
    force: bool = False,
    max_price_drop: Decimal = Decimal("0.5"),
    workers: int = DEFAULT_WORKERS,
) -> SyncPlan:
    log.info("Consultando %d SKUs en VTEX...", len({row.sku_ref for row in rows}))
    current = fetch_current_state(client, [row.sku_ref for row in rows], workers)

    plan = build_plan(rows, current, max_price_drop=max_price_drop, force=force)

    if apply:
        log.info("Aplicando %d cambios...", len(plan.by_status(Status.PENDING)))
        apply_plan(client, plan, workers)
    else:
        for change in plan.by_status(Status.PENDING):
            change.status = Status.SIMULATED

    return plan
