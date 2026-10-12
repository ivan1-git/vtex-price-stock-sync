import json
from decimal import Decimal
from pathlib import Path

from price_stock_sync.__main__ import main
from price_stock_sync.models import ErpRow, Status
from price_stock_sync.runner import run_sync
from price_stock_sync.vtex import FakeVtexClient

DATA = Path(__file__).resolve().parent.parent / "data"


def catalog():
    return {
        "A-1": {"skuId": "10", "basePrice": 100, "listPrice": 100, "stock": {"1": 5}},
        "B-2": {"skuId": "20", "basePrice": 50, "listPrice": 60, "stock": {"1": 1}},
    }


def rows():
    return [
        ErpRow(2, "A-1", Decimal("110.00"), Decimal("110.00"), 5, "1"),
        ErpRow(3, "B-2", Decimal("50.00"), Decimal("60.00"), 9, "1"),
    ]


def test_dry_run_does_not_touch_vtex():
    client = FakeVtexClient(catalog())

    plan = run_sync(client, rows(), apply=False)

    assert [c.status for c in plan.changes] == [Status.SIMULATED, Status.SIMULATED]
    assert not any(call.startswith(("price", "stock")) for call in client.calls)


def test_apply_updates_prices_and_stock():
    client = FakeVtexClient(catalog())

    plan = run_sync(client, rows(), apply=True)

    assert [c.status for c in plan.changes] == [Status.APPLIED, Status.APPLIED]
    assert client.catalog["A-1"]["basePrice"] == 110.0
    assert client.catalog["B-2"]["stock"]["1"] == 9


def test_one_failure_does_not_stop_the_rest():
    client = FakeVtexClient(catalog())
    client.failing_skus.add("10")

    plan = run_sync(client, rows(), apply=True)

    failed, applied = plan.changes
    assert failed.status == Status.FAILED
    assert "HTTP 500" in failed.detail
    assert applied.status == Status.APPLIED


def test_cli_demo_end_to_end(tmp_path, capsys):
    exit_code = main([str(DATA / "erp_export.csv"), "--demo", "--reportes", str(tmp_path)])

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "SIMULACIÓN" in output
    assert "PLA-020 (precio): Baja 72%" in output

    [report] = list(tmp_path.glob("sync-*-simulacion.csv"))
    lines = report.read_text(encoding="utf-8-sig").splitlines()
    assert lines[0] == "sku_ref,sku_id,tipo,almacen,antes,despues,estado,detalle"
    assert any(line.startswith("GOR-040,,sku") for line in lines)


def test_demo_catalog_matches_the_sample_file():
    # Si alguien edita los datos de ejemplo, que el demo siga mostrando todos los casos
    demo = json.loads((DATA / "demo_catalog.json").read_text(encoding="utf-8"))
    assert "GOR-040" not in demo  # el SKU que "no existe en VTEX"
    assert demo["PLA-020"]["basePrice"] == 349.0  # la bajada sospechosa
