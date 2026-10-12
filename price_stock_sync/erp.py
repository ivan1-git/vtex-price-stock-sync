"""Lectura del archivo del ERP.

Los exports de ERP casi nunca vienen limpios: precios con comas de miles, espacios de más,
existencias con decimales, filas a medio llenar. Aquí se normaliza todo eso y lo que no se
puede leer se reporta con su número de línea en lugar de tronar todo el proceso.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path

from .models import ErpRow

REQUIRED_COLUMNS = {"sku_ref", "precio", "existencias", "almacen"}


@dataclass
class ErpFile:
    rows: list[ErpRow] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def parse_money(raw: str) -> Decimal:
    cleaned = raw.strip().replace("$", "").replace(",", "")
    try:
        value = Decimal(cleaned)
    except InvalidOperation:
        raise ValueError(f"'{raw}' no es un precio válido") from None
    if value < 0:
        raise ValueError(f"el precio no puede ser negativo ({raw})")
    return value.quantize(Decimal("0.01"))


def parse_stock(raw: str) -> int:
    cleaned = raw.strip().replace(",", "")
    try:
        # Algunos ERP mandan "12.000" para decir 12 piezas
        return int(Decimal(cleaned))
    except InvalidOperation:
        raise ValueError(f"'{raw}' no es una cantidad válida") from None


def read_erp_csv(path: Path) -> ErpFile:
    result = ErpFile()
    seen: dict[tuple[str, str], int] = {}

    # utf-8-sig quita el BOM que deja Excel al guardar como CSV
    with path.open(encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        columns = {name.strip().lower() for name in (reader.fieldnames or [])}
        missing = REQUIRED_COLUMNS - columns
        if missing:
            raise ValueError(f"Al archivo le faltan columnas: {', '.join(sorted(missing))}")

        for line, raw in enumerate(reader, start=2):
            row = {key.strip().lower(): (value or "").strip() for key, value in raw.items() if key}

            if not any(row.values()):
                continue  # renglón vacío al final del archivo

            sku_ref = row.get("sku_ref", "")
            if not sku_ref:
                result.errors.append(f"Línea {line}: falta sku_ref")
                continue

            try:
                price = parse_money(row["precio"])
                list_price = parse_money(row["precio_lista"]) if row.get("precio_lista") else price
                stock = parse_stock(row["existencias"])
            except ValueError as error:
                result.errors.append(f"Línea {line} ({sku_ref}): {error}")
                continue

            warehouse = row.get("almacen") or "1"
            key = (sku_ref, warehouse)
            if key in seen:
                result.errors.append(
                    f"Línea {line} ({sku_ref}): repetido, ya venía en la línea {seen[key]}; se usa la primera"
                )
                continue
            seen[key] = line

            result.rows.append(ErpRow(line, sku_ref, price, list_price, stock, warehouse))

    return result
