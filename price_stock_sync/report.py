"""Reporte de la corrida: resumen en consola y detalle en CSV para compartir con el equipo."""

from __future__ import annotations

import csv
from collections import Counter
from datetime import datetime
from pathlib import Path

from .models import Status, SyncPlan

STATUS_LABELS = {
    Status.APPLIED: "Aplicados",
    Status.SIMULATED: "Por aplicar (simulación)",
    Status.BLOCKED: "Bloqueados para revisión",
    Status.FAILED: "Con error",
    Status.SKIPPED: "Omitidos",
}


def write_csv(plan: SyncPlan, folder: Path, *, applied: bool, now: datetime | None = None) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    path = folder / f"sync-{stamp}-{'aplicado' if applied else 'simulacion'}.csv"

    # utf-8-sig para que Excel abra bien los acentos
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(["sku_ref", "sku_id", "tipo", "almacen", "antes", "despues", "estado", "detalle"])
        for change in plan.changes:
            writer.writerow(
                [
                    change.sku_ref,
                    change.sku_id or "",
                    change.kind.value,
                    change.warehouse or "",
                    change.before,
                    change.after,
                    change.status.value,
                    change.detail,
                ]
            )
    return path


def summary(plan: SyncPlan, erp_errors: list[str]) -> str:
    counts = Counter(change.status for change in plan.changes)
    lines = ["", "Resumen", "-------"]

    for status, label in STATUS_LABELS.items():
        if counts[status]:
            lines.append(f"  {label:<28} {counts[status]:>4}")
    lines.append(f"  {'Sin cambios':<28} {plan.unchanged:>4}")

    attention = plan.by_status(Status.BLOCKED) + plan.by_status(Status.FAILED) + plan.by_status(Status.SKIPPED)
    if attention:
        lines += ["", "Para revisar:"]
        lines += [f"  - {c.sku_ref} ({c.kind.value}): {c.detail}" for c in attention]

    if plan.warnings:
        lines += ["", "Avisos:"]
        lines += [f"  - {warning}" for warning in plan.warnings]

    if erp_errors:
        lines += ["", "Filas del ERP que no se pudieron leer:"]
        lines += [f"  - {error}" for error in erp_errors]

    return "\n".join(lines)


def changes_table(plan: SyncPlan) -> str:
    rows = [c for c in plan.changes if c.status in (Status.SIMULATED, Status.APPLIED)]
    if not rows:
        return "\nNo hay cambios que aplicar."

    header = f"\n  {'SKU':<10} {'Tipo':<11} {'Almacén':<8} {'Antes':>21}   {'Después':>21}"
    lines = [header, "  " + "-" * (len(header) - 3)]
    for c in rows:
        lines.append(f"  {c.sku_ref:<10} {c.kind.value:<11} {c.warehouse or '':<8} {c.before:>21} → {c.after:>21}")
    lines.append("  (precios: venta / lista)")
    return "\n".join(lines)
