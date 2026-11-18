"""Strict input validation shared by all services. Never trust Telegram text."""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from bot.services.errors import ValidationError

# Arabic-Indic (٠-٩) and Persian (۰-۹) digits -> ASCII
_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\u200e\u200f\u202a-\u202e]")
_PRICE_RE = re.compile(r"^\d{1,8}(\.\d{1,2})?$")
_INT_RE = re.compile(r"^[+-]?\d{1,9}$")

MIN_PRICE = Decimal("0.01")
MAX_PRICE = Decimal("99999999.99")
MAX_STOCK = 1_000_000
MAX_DISCOUNT_PERCENT = 90


def normalize_number_text(value) -> str:
    text = str(value).strip().translate(_DIGITS)
    text = text.replace("٫", ".").replace("٬", "").replace(" ", "")
    if "," in text and "." not in text and text.count(",") == 1:
        return text.replace(",", ".")  # "12,5" -> "12.5"
    return text.replace(",", "")  # "1,000.50" -> "1000.50"


def clean_text(value, label: str, min_len: int, max_len: int, multiline: bool = False) -> str:
    if not isinstance(value, str):
        raise ValidationError("invalid_text", f"{label} غير صالح")
    text = _CONTROL.sub("", value).replace("\r\n", "\n").replace("\r", "\n").strip()
    text = re.sub(r"\n{3,}", "\n\n", text) if multiline else " ".join(text.split())
    if not min_len <= len(text) <= max_len:
        raise ValidationError("invalid_text", f"{label} يجب أن يكون بين {min_len} و{max_len} حرفاً")
    return text


def parse_price(value) -> Decimal:
    msg = "السعر غير صالح. اكتب رقماً موجباً بحد أقصى خانتين عشريتين، مثل 1500 أو 99.90"
    if value is None or isinstance(value, (bool, float)):
        raise ValidationError("invalid_price", msg)
    if isinstance(value, Decimal):
        try:
            quantized = value.quantize(Decimal("0.01"))
        except InvalidOperation:
            raise ValidationError("invalid_price", msg)
        if quantized != value:
            raise ValidationError("invalid_price", msg)
        text = format(quantized, "f")
    else:
        text = normalize_number_text(value)
    if not _PRICE_RE.match(text):
        raise ValidationError("invalid_price", msg)
    price = Decimal(text)
    if not MIN_PRICE <= price <= MAX_PRICE:
        raise ValidationError("invalid_price", msg)
    return price


def parse_int(value, label: str, minimum: int, maximum: int, code: str = "invalid_number") -> int:
    msg = f"{label} يجب أن يكون عدداً صحيحاً بين {minimum} و{maximum}"
    if value is None or isinstance(value, (bool, float)):
        raise ValidationError(code, msg)
    if isinstance(value, int):
        number = value
    else:
        text = normalize_number_text(value)
        if not _INT_RE.match(text):
            raise ValidationError(code, msg)
        number = int(text)
    if not minimum <= number <= maximum:
        raise ValidationError(code, msg)
    return number


def parse_stock(value) -> int:
    return parse_int(value, "الكمية", 0, MAX_STOCK, "invalid_stock")


def parse_stock_delta(value) -> int:
    delta = parse_int(value, "التعديل", -MAX_STOCK, MAX_STOCK, "invalid_stock")
    if delta == 0:
        raise ValidationError("invalid_stock", "التعديل لا يمكن أن يكون صفراً")
    return delta


def parse_percent(value) -> int:
    return parse_int(value, "نسبة الخصم", 1, MAX_DISCOUNT_PERCENT, "invalid_discount")
