from decimal import Decimal

import pytest

from price_stock_sync.erp import parse_money, parse_stock, read_erp_csv


def write(tmp_path, content: str, encoding="utf-8"):
    path = tmp_path / "erp.csv"
    path.write_text(content, encoding=encoding)
    return path


@pytest.mark.parametrize(
    "raw, expected",
    [("1299", "1299.00"), ("1,299.50", "1299.50"), (" $849.9 ", "849.90"), ("0", "0.00")],
)
def test_parse_money_accepts_common_erp_formats(raw, expected):
    assert parse_money(raw) == Decimal(expected)


def test_parse_money_rejects_garbage_and_negatives():
    with pytest.raises(ValueError):
        parse_money("abc")
    with pytest.raises(ValueError):
        parse_money("-10")


def test_parse_stock_accepts_decimals_from_the_erp():
    assert parse_stock("12.000") == 12
    assert parse_stock("1,200") == 1200


def test_reads_a_file_saved_from_excel_with_bom(tmp_path):
    path = write(tmp_path, "sku_ref,precio,precio_lista,existencias,almacen\nA-1,100,120,5,1\n", encoding="utf-8-sig")

    erp = read_erp_csv(path)

    assert erp.errors == []
    assert erp.rows[0].sku_ref == "A-1"
    assert erp.rows[0].list_price == Decimal("120.00")


def test_list_price_defaults_to_price_and_warehouse_to_1(tmp_path):
    path = write(tmp_path, "sku_ref,precio,precio_lista,existencias,almacen\nA-1,100,,5,\n")

    row = read_erp_csv(path).rows[0]

    assert row.list_price == Decimal("100.00")
    assert row.warehouse == "1"


def test_bad_rows_are_reported_with_their_line_and_the_rest_continue(tmp_path):
    path = write(
        tmp_path,
        "sku_ref,precio,existencias,almacen\n"
        "A-1,100,5,1\n"
        "A-2,abc,5,1\n"
        ",100,5,1\n"
        "A-1,100,9,1\n"
        ",,,\n",
    )

    erp = read_erp_csv(path)

    assert [row.sku_ref for row in erp.rows] == ["A-1"]
    assert erp.errors == [
        "Línea 3 (A-2): 'abc' no es un precio válido",
        "Línea 4: falta sku_ref",
        "Línea 5 (A-1): repetido, ya venía en la línea 2; se usa la primera",
    ]


def test_fails_fast_when_columns_are_missing(tmp_path):
    path = write(tmp_path, "sku,precio\nA-1,100\n")

    with pytest.raises(ValueError, match="almacen, existencias, sku_ref"):
        read_erp_csv(path)
