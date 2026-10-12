"""Clientes de VTEX: el real (HTTP) y uno en memoria para el modo demo y las pruebas.

Solo usa la librería estándar, para que el script corra en cualquier servidor con Python
sin instalar nada.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from decimal import Decimal
from typing import Protocol

from .models import SkuState


class VtexError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(f"HTTP {status}: {message}")
        self.status = status


class VtexClient(Protocol):
    def find_sku_state(self, sku_ref: str) -> SkuState | None: ...

    def update_price(self, sku_id: str, payload: dict) -> None: ...

    def update_stock(self, sku_id: str, warehouse: str, payload: dict) -> None: ...


class HttpVtexClient:
    """Habla con las APIs de Catálogo, Pricing y Logística de VTEX."""

    def __init__(
        self,
        account: str,
        app_key: str,
        app_token: str,
        *,
        environment: str = "vtexcommercestable",
        base_url: str | None = None,
        pricing_url: str | None = None,
        max_attempts: int = 4,
        backoff_seconds: float = 1.0,
        timeout: float = 15.0,
    ):
        self.base_url = base_url or f"https://{account}.{environment}.com.br"
        self.pricing_url = pricing_url or f"https://api.vtex.com/{account}"
        self.headers = {
            "X-VTEX-API-AppKey": app_key,
            "X-VTEX-API-AppToken": app_token,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        self.max_attempts = max_attempts
        self.backoff_seconds = backoff_seconds
        self.timeout = timeout

    def find_sku_state(self, sku_ref: str) -> SkuState | None:
        ref = urllib.parse.quote(sku_ref, safe="")
        sku_id = self._get(f"{self.base_url}/api/catalog_system/pvt/sku/stockkeepingunitidbyrefid/{ref}")
        if sku_id is None:
            return None
        sku_id = str(sku_id)

        price = self._get(f"{self.pricing_url}/pricing/prices/{sku_id}") or {}
        inventory = self._get(f"{self.base_url}/api/logistics/pvt/inventory/skus/{sku_id}") or {}

        return SkuState(
            sku_id=sku_id,
            base_price=_decimal(price.get("basePrice")),
            list_price=_decimal(price.get("listPrice")),
            stock_by_warehouse={
                str(balance["warehouseId"]): int(balance.get("totalQuantity", 0))
                for balance in inventory.get("balance", [])
            },
        )

    def update_price(self, sku_id: str, payload: dict) -> None:
        self._request("PUT", f"{self.pricing_url}/pricing/prices/{sku_id}", payload)

    def update_stock(self, sku_id: str, warehouse: str, payload: dict) -> None:
        url = f"{self.base_url}/api/logistics/pvt/inventory/skus/{sku_id}/warehouses/{warehouse}"
        self._request("PUT", url, payload)

    def _get(self, url: str):
        """GET que regresa None en 404 (SKU o precio que no existe), en lugar de lanzar error."""
        try:
            return self._request("GET", url)
        except VtexError as error:
            if error.status == 404:
                return None
            raise

    def _request(self, method: str, url: str, body: dict | None = None):
        data = json.dumps(body).encode() if body is not None else None

        for attempt in range(1, self.max_attempts + 1):
            request = urllib.request.Request(url, data=data, method=method, headers=self.headers)
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    raw = response.read()
                    return json.loads(raw) if raw else None
            except urllib.error.HTTPError as error:
                retriable = error.code == 429 or error.code >= 500
                if not retriable or attempt == self.max_attempts:
                    raise VtexError(error.code, error.read().decode(errors="replace")[:300]) from None
                # VTEX suele mandar Retry-After cuando pega el rate limit; si no, espera exponencial
                wait = _retry_after(error) or self.backoff_seconds * 2 ** (attempt - 1)
            except urllib.error.URLError as error:
                if attempt == self.max_attempts:
                    raise VtexError(0, f"sin conexión: {error.reason}") from None
                wait = self.backoff_seconds * 2 ** (attempt - 1)

            time.sleep(wait)


class FakeVtexClient:
    """VTEX en memoria. Carga un catálogo desde un dict y registra lo que se le pidió."""

    def __init__(self, catalog: dict[str, dict]):
        # catalog = {"REF-1": {"skuId": "10", "basePrice": 100, "listPrice": 120, "stock": {"1": 5}}}
        self.catalog = catalog
        self.calls: list[str] = []
        self.failing_skus: set[str] = set()

    def find_sku_state(self, sku_ref: str) -> SkuState | None:
        self.calls.append(f"find {sku_ref}")
        item = self.catalog.get(sku_ref)
        if item is None:
            return None
        return SkuState(
            sku_id=str(item["skuId"]),
            base_price=_decimal(item.get("basePrice")),
            list_price=_decimal(item.get("listPrice")),
            stock_by_warehouse={str(k): int(v) for k, v in item.get("stock", {}).items()},
        )

    def update_price(self, sku_id: str, payload: dict) -> None:
        self._maybe_fail(sku_id)
        self.calls.append(f"price {sku_id} {payload['basePrice']}")
        item = self._by_id(sku_id)
        item["basePrice"] = payload["basePrice"]
        item["listPrice"] = payload["listPrice"]

    def update_stock(self, sku_id: str, warehouse: str, payload: dict) -> None:
        self._maybe_fail(sku_id)
        self.calls.append(f"stock {sku_id} {warehouse} {payload['quantity']}")
        self._by_id(sku_id).setdefault("stock", {})[warehouse] = payload["quantity"]

    def _maybe_fail(self, sku_id: str) -> None:
        if sku_id in self.failing_skus:
            raise VtexError(500, "Internal Server Error")

    def _by_id(self, sku_id: str) -> dict:
        return next(item for item in self.catalog.values() if str(item["skuId"]) == sku_id)


def _decimal(value) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value)).quantize(Decimal("0.01"))


def _retry_after(error: urllib.error.HTTPError) -> float | None:
    value = error.headers.get("Retry-After") if error.headers else None
    try:
        return float(value) if value else None
    except ValueError:
        return None
