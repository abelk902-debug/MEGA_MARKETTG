from decimal import Decimal

import pytest

from bot.services.errors import ValidationError
from bot.services import validation as v


@pytest.mark.parametrize("raw,expected", [
    ("10", "10.00"), ("10.5", "10.50"), ("١٢٫٥٠", "12.50"), ("۱۵۰۰", "1500.00"), ("12,5", "12.50"),
    ("1,000.50", "1000.50"), (" 7 ", "7.00"), (5, "5.00"), (Decimal("3.1"), "3.10"),
    ("99999999.99", "99999999.99"), ("0.01", "0.01")])
def test_parse_price_valid(raw, expected):
    assert v.parse_price(raw) == Decimal(expected)


@pytest.mark.parametrize("raw", ["abc", "", "0", "0.00", "-5", "1.234", "1e3", "nan", "inf", "100000000",
                                 "100000000.00", True, None, 1.5, Decimal("1.234"), Decimal("NaN"), "١٢٣abc"])
def test_parse_price_invalid(raw):
    with pytest.raises(ValidationError) as e:
        v.parse_price(raw)
    assert e.value.code == "invalid_price"


@pytest.mark.parametrize("raw,expected", [("0", 0), ("5", 5), ("٥٠", 50), (7, 7), ("1000000", 1_000_000)])
def test_parse_stock_valid(raw, expected):
    assert v.parse_stock(raw) == expected


@pytest.mark.parametrize("raw", ["-1", "1000001", "1.5", "abc", "", True, None, 2.0, "1e3"])
def test_parse_stock_invalid(raw):
    with pytest.raises(ValidationError) as e:
        v.parse_stock(raw)
    assert e.value.code == "invalid_stock"


def test_stock_delta():
    assert v.parse_stock_delta("+5") == 5 and v.parse_stock_delta("-3") == -3 and v.parse_stock_delta(10) == 10
    for bad in ("0", "abc", "", None):
        with pytest.raises(ValidationError):
            v.parse_stock_delta(bad)


def test_percent_bounds():
    assert v.parse_percent("1") == 1 and v.parse_percent("٩٠") == 90
    for bad in ("0", "91", "-5", "abc", "10.5", None, True):
        with pytest.raises(ValidationError) as e:
            v.parse_percent(bad)
        assert e.value.code == "invalid_discount"


def test_clean_text_collapses_whitespace_and_strips_control_chars():
    assert v.clean_text("  Blue \t  Shirt\x00 ", "x", 2, 50) == "Blue Shirt"
    assert v.clean_text("a\u202eb  c", "x", 2, 50) == "ab c"


def test_clean_text_multiline_keeps_paragraphs_but_limits_blank_lines():
    assert v.clean_text("one\r\n\r\n\r\n\r\ntwo", "x", 2, 50, multiline=True) == "one\n\ntwo"


@pytest.mark.parametrize("value", ["", "   ", "a", "x" * 51, None, 5, ["a"]])
def test_clean_text_rejects(value):
    with pytest.raises(ValidationError):
        v.clean_text(value, "x", 2, 50)
