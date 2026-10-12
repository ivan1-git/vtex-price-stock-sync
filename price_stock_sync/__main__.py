"""Uso:

    python -m price_stock_sync data/erp_export.csv --demo            # simula contra un VTEX de mentira
    python -m price_stock_sync data/erp_export.csv --demo --apply    # aplica los cambios (en el VTEX de mentira)
    python -m price_stock_sync export.csv --apply                    # contra la tienda real (variables VTEX_*)
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from decimal import Decimal
from pathlib import Path

from . import report
from .erp import read_erp_csv
from .models import Status
from .runner import run_sync
from .vtex import FakeVtexClient, HttpVtexClient, VtexClient

DEMO_CATALOG = Path(__file__).resolve().parent.parent / "data" / "demo_catalog.json"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="price_stock_sync",
        description="Sincroniza precios e inventario de un archivo del ERP hacia VTEX.",
    )
    parser.add_argument("archivo", type=Path, help="CSV exportado del ERP")
    parser.add_argument("--apply", action="store_true", help="aplica los cambios (sin esto solo simula)")
    parser.add_argument("--demo", action="store_true", help="usa un catálogo de VTEX en memoria, sin credenciales")
    parser.add_argument("--force", action="store_true", help="aplica también las bajadas de precio grandes")
    parser.add_argument(
        "--max-bajada", type=Decimal, default=Decimal("0.5"), help="bajada de precio que se considera sospechosa (0.5 = 50%%)"
    )
    parser.add_argument("--reportes", type=Path, default=Path("reports"), help="carpeta para el CSV de resultados")
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser.parse_args(argv)


def build_client(demo: bool) -> VtexClient:
    if demo:
        return FakeVtexClient(json.loads(DEMO_CATALOG.read_text(encoding="utf-8")))

    missing = [name for name in ("VTEX_ACCOUNT", "VTEX_APP_KEY", "VTEX_APP_TOKEN") if not os.environ.get(name)]
    if missing:
        sys.exit(f"Faltan variables de entorno: {', '.join(missing)}. Usa --demo para probar sin cuenta.")

    return HttpVtexClient(
        os.environ["VTEX_ACCOUNT"],
        os.environ["VTEX_APP_KEY"],
        os.environ["VTEX_APP_TOKEN"],
        environment=os.environ.get("VTEX_ENVIRONMENT", "vtexcommercestable"),
    )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    # En Windows, si la salida va a un archivo o a Git Bash, Python usa cp1252 y truena con acentos y flechas
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(message)s")

    try:
        erp = read_erp_csv(args.archivo)
    except (OSError, ValueError) as error:
        print(f"No pude leer {args.archivo}: {error}", file=sys.stderr)
        return 2

    mode = "APLICANDO" if args.apply else "SIMULACIÓN (usa --apply para aplicar)"
    target = "VTEX demo en memoria" if args.demo else os.environ.get("VTEX_ACCOUNT", "")
    print(f"{len(erp.rows)} filas leídas de {args.archivo.name} · {target} · {mode}")

    client = build_client(args.demo)
    plan = run_sync(client, erp.rows, apply=args.apply, force=args.force, max_price_drop=args.max_bajada)

    print(report.changes_table(plan))
    print(report.summary(plan, erp.errors))
    path = report.write_csv(plan, args.reportes, applied=args.apply)
    print(f"\nDetalle completo en {path}")

    return 1 if plan.by_status(Status.FAILED) else 0


if __name__ == "__main__":
    sys.exit(main())
