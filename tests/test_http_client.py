"""Prueba el cliente HTTP contra un servidor local que se hace pasar por VTEX."""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from price_stock_sync.vtex import HttpVtexClient, VtexError


class FakeVtexApi(BaseHTTPRequestHandler):
    # Respuestas programadas por ruta: lista de (status, body, headers) que se van consumiendo
    script: dict[str, list] = {}
    received: list[tuple[str, str, dict, bytes]] = []

    def _reply(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        # Los nombres de header no distinguen mayúsculas (urllib los manda como X-vtex-api-appkey)
        headers = {key.lower(): value for key, value in self.headers.items()}
        FakeVtexApi.received.append((self.command, self.path, headers, body))

        responses = FakeVtexApi.script.get(f"{self.command} {self.path}", [(404, {"error": "not found"}, {})])
        status, payload, extra_headers = responses.pop(0) if len(responses) > 1 else responses[0]

        self.send_response(status)
        for key, value in extra_headers.items():
            self.send_header(key, value)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        if payload is not None:
            self.wfile.write(json.dumps(payload).encode())

    do_GET = do_PUT = _reply

    def log_message(self, *args):
        pass


@pytest.fixture
def server():
    FakeVtexApi.script = {}
    FakeVtexApi.received = []
    httpd = HTTPServer(("127.0.0.1", 0), FakeVtexApi)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_port}"
    httpd.shutdown()


def client_for(url):
    return HttpVtexClient("tienda", "key-123", "token-456", base_url=url, pricing_url=url, backoff_seconds=0.01)


def test_reads_sku_price_and_inventory(server):
    FakeVtexApi.script = {
        "GET /api/catalog_system/pvt/sku/stockkeepingunitidbyrefid/TEN-001": [(200, "1203", {})],
        "GET /pricing/prices/1203": [(200, {"basePrice": 1299, "listPrice": 1599}, {})],
        "GET /api/logistics/pvt/inventory/skus/1203": [
            (200, {"balance": [{"warehouseId": "1", "totalQuantity": 20}, {"warehouseId": "2", "totalQuantity": 3}]}, {})
        ],
    }

    state = client_for(server).find_sku_state("TEN-001")

    assert state.sku_id == "1203"
    assert str(state.base_price) == "1299.00"
    assert state.stock_by_warehouse == {"1": 20, "2": 3}

    _, _, headers, _ = FakeVtexApi.received[0]
    assert headers["x-vtex-api-appkey"] == "key-123"
    assert headers["x-vtex-api-apptoken"] == "token-456"


def test_unknown_sku_returns_none(server):
    assert client_for(server).find_sku_state("NO-EXISTE") is None


def test_retries_after_rate_limit(server):
    FakeVtexApi.script = {
        "PUT /pricing/prices/1203": [
            (429, {"error": "Too Many Requests"}, {"Retry-After": "0"}),
            (200, None, {}),
        ]
    }

    client_for(server).update_price("1203", {"basePrice": 1349.0, "listPrice": 1599.0})

    puts = [r for r in FakeVtexApi.received if r[0] == "PUT"]
    assert len(puts) == 2
    assert json.loads(puts[-1][3]) == {"basePrice": 1349.0, "listPrice": 1599.0}


def test_does_not_retry_client_errors(server):
    FakeVtexApi.script = {"PUT /api/logistics/pvt/inventory/skus/1203/warehouses/1": [(400, {"error": "bad"}, {})]}

    with pytest.raises(VtexError) as error:
        client_for(server).update_stock("1203", "1", {"quantity": 1, "unlimitedQuantity": False})

    assert error.value.status == 400
    assert len(FakeVtexApi.received) == 1


def test_gives_up_after_max_attempts_on_server_errors(server):
    FakeVtexApi.script = {"PUT /pricing/prices/1": [(503, {"error": "down"}, {})]}

    with pytest.raises(VtexError) as error:
        client_for(server).update_price("1", {"basePrice": 1.0, "listPrice": 1.0})

    assert error.value.status == 503
    assert len(FakeVtexApi.received) == 4
