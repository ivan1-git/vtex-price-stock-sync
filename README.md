# vtex-price-stock-sync

![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-3776ab)
![Sin dependencias](https://img.shields.io/badge/dependencias-0-brightgreen)

Un script en Python que toma el archivo de precios e inventario que exporta el ERP y lo
sincroniza con VTEX. Primero te enseña qué va a cambiar y solo lo aplica cuando se lo pides.

> Proyecto personal para mi portafolio. No tiene código de clientes ni de empresas donde he trabajado.

## El problema

En muchas tiendas, alguien del equipo exporta un Excel del ERP cada mañana y actualiza
precios y existencias en VTEX a mano, o con un import masivo que pisa todo sin revisar.
Funciona hasta el día en que alguien escribe `99` en lugar de `999` y la tienda vende
tenis a 99 pesos toda la mañana.

Este script automatiza esa tarea, pero con cuidado:

- **Solo manda lo que cambió.** Consulta lo que tiene VTEX y compara. Si de 5,000 SKUs
  cambiaron 40, hace 40 llamadas, no 5,000.
- **Simula por defecto.** Sin `--apply` no toca nada; solo te dice qué haría.
- **Frena los errores de dedo.** Si un precio baja más de 50%, lo bloquea y lo marca para
  revisión. Si de verdad es una oferta, se aplica con `--force`.
- **Aguanta archivos sucios.** Precios con `$` y comas, existencias como `12.000`, filas
  vacías, el BOM que deja Excel. Lo que no se puede leer se reporta con su número de línea y el resto sigue.
- **Respeta el rate limit de VTEX.** Pocas conexiones en paralelo y, si VTEX responde 429,
  espera lo que dice `Retry-After` antes de reintentar.
- **Deja evidencia.** Cada corrida genera un CSV con lo que se aplicó, lo que se bloqueó y por qué.

## Cómo correrlo

Solo necesitas **Python 3.10 o más nuevo**. No hay nada que instalar: usa solo la librería estándar.

```bash
python -m price_stock_sync data/erp_export.csv --demo
```

`--demo` usa un catálogo de VTEX en memoria, así que no necesitas cuenta. El archivo de
ejemplo trae a propósito los casos que suelen salir mal en la vida real. Esta es la salida:

```
9 filas leídas de erp_export.csv · VTEX demo en memoria · SIMULACIÓN (usa --apply para aplicar)

  SKU        Tipo        Almacén                  Antes                 Después
  -----------------------------------------------------------------------------
  TEN-001    inventario  1                           20 →                    14
  TEN-002    precio                 1,199.00 / 1,599.00 →   1,349.00 / 1,599.00
  CHA-010    precio                 1,849.00 / 1,999.00 →   1,849.00 / 2,199.00
  CHA-010    inventario  1                            2 →                     7
  CAL-030    inventario  1                            6 →                     0
  (precios: venta / lista)

Resumen
-------
  Por aplicar (simulación)        5
  Bloqueados para revisión        1
  Omitidos                        1
  Sin cambios                     9

Para revisar:
  - PLA-020 (precio): Baja 72%. Revísalo y corre con --force si es correcto
  - GOR-040 (sku): El SKU no existe en VTEX

Avisos:
  - PLA-021: el precio de lista (299.00) es menor al de venta (349.00); se usa el de venta
  - CAL-030: existencias negativas en el ERP (-3); se mandan como 0

Filas del ERP que no se pudieron leer:
  - Línea 11 (MOC-050): 'abc' no es un precio válido
  - Línea 12: falta sku_ref

Detalle completo en reports/sync-20261012-101924-simulacion.csv
```

Para aplicar los cambios (en el demo no pasa nada real):

```bash
python -m price_stock_sync data/erp_export.csv --demo --apply
```

### Contra una tienda real

Crea un AppKey/AppToken en VTEX con permisos de catálogo, precios y logística, y pásalos
por variables de entorno (hay un ejemplo en [`.env.example`](.env.example)):

```bash
export VTEX_ACCOUNT=mitienda
export VTEX_APP_KEY=vtexappkey-mitienda-XXXXXX
export VTEX_APP_TOKEN=...

python -m price_stock_sync export_del_erp.csv            # primero revisa
python -m price_stock_sync export_del_erp.csv --apply    # luego aplica
```

| Opción | Para qué |
| --- | --- |
| `--apply` | Aplica los cambios. Sin esto, solo simula |
| `--demo` | Usa el VTEX de mentira en lugar de la tienda |
| `--force` | Aplica también las bajadas de precio grandes |
| `--max-bajada 0.3` | Cambia qué bajada se considera sospechosa (por defecto 0.5 = 50%) |
| `--reportes carpeta/` | Dónde guardar el CSV de resultados |

El código de salida es `1` si algún cambio falló, así se puede usar en un cron o en un
pipeline y que avise cuando algo sale mal.

## El archivo del ERP

Un CSV con estas columnas (el orden no importa y puede traer columnas extra):

| Columna | Ejemplo | Notas |
| --- | --- | --- |
| `sku_ref` | `TEN-001` | El código de referencia del SKU en VTEX (RefId) |
| `precio` | `1,299.00` | Precio de venta |
| `precio_lista` | `1,599.00` | Opcional. Es el precio tachado; si viene vacío se usa el de venta |
| `existencias` | `14` | Si viene negativo se manda como 0 |
| `almacen` | `1` | ID del almacén en VTEX. Si viene vacío se usa `1` |

## Cómo está organizado

```
price_stock_sync/
├── __main__.py   la línea de comandos
├── erp.py        lee y limpia el CSV del ERP
├── planner.py    compara ERP vs VTEX y decide qué cambiar (sin llamadas a APIs)
├── runner.py     consulta VTEX en paralelo y aplica el plan
├── vtex.py       cliente HTTP de VTEX (catálogo, pricing, logística) + versión en memoria
├── report.py     resumen en consola y CSV de resultados
└── models.py     dataclasses compartidas
```

La decisión más importante: `planner.py` no sabe nada de HTTP. Recibe lo que dice el ERP y
lo que tiene VTEX, y regresa un plan. Eso hace que la simulación sea exactamente lo mismo
que se va a aplicar, y que cada regla se pruebe con datos inventados en milisegundos.

## Pruebas

```bash
pip install -r requirements-dev.txt
python -m pytest
```

30 pruebas con pytest. Las del cliente HTTP levantan un servidor local que se hace pasar por
VTEX, para probar de verdad los reintentos con 429 y 503, los 404 de SKUs que no existen y
que los headers de autenticación se manden. El workflow de GitHub Actions
([`ci.yml`](.github/workflows/ci.yml)) las corre con Python 3.10, 3.12 y 3.13.

## Lo que le agregaría

- Leer directo del ERP (SQL Server o la API que tenga) en lugar de un CSV.
- Mandar el resumen por correo o Slack al terminar.
- Programarlo con cron o como job en la nube, con el CSV de reportes guardado en S3.
- Precios por política comercial (trade policy) y no solo el precio base.

## Stack

Python 3.10+ (solo librería estándar: `urllib`, `csv`, `decimal`, `concurrent.futures`) · pytest · GitHub Actions

---

Hecho por **Iván Uriel Jiménez Granados** ·
[ivan-solutions-l2az.vercel.app](https://ivan-solutions-l2az.vercel.app) ·
uriel130granados@gmail.com
