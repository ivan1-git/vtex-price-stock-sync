from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum


@dataclass(frozen=True)
class ErpRow:
    """Una fila del archivo que exporta el ERP."""

    line: int
    sku_ref: str
    price: Decimal
    list_price: Decimal
    stock: int
    warehouse: str


@dataclass(frozen=True)
class SkuState:
    """Lo que VTEX tiene hoy para un SKU."""

    sku_id: str
    base_price: Decimal | None
    list_price: Decimal | None
    stock_by_warehouse: dict[str, int] = field(default_factory=dict)


class ChangeKind(str, Enum):
    PRICE = "precio"
    STOCK = "inventario"
    SKU = "sku"


class Status(str, Enum):
    PENDING = "pendiente"
    APPLIED = "aplicado"
    SIMULATED = "simulado"
    BLOCKED = "bloqueado"
    FAILED = "error"
    SKIPPED = "omitido"


@dataclass
class Change:
    sku_ref: str
    sku_id: str | None
    kind: ChangeKind
    before: str
    after: str
    status: Status = Status.PENDING
    detail: str = ""
    # Lo que se manda a VTEX si el cambio se aplica
    payload: dict = field(default_factory=dict)
    warehouse: str | None = None


@dataclass
class SyncPlan:
    changes: list[Change] = field(default_factory=list)
    unchanged: int = 0
    warnings: list[str] = field(default_factory=list)

    def by_status(self, status: Status) -> list[Change]:
        return [change for change in self.changes if change.status == status]
