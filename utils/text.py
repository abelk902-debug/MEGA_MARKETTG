from __future__ import annotations

import secrets
import string
from datetime import datetime, timezone

_ALPHABET = string.ascii_uppercase + string.digits


def escape_like(value: str, escape: str = "\\") -> str:
    """Escape LIKE wildcards so user input is matched literally."""
    return (
        value.replace(escape, escape + escape)
        .replace("%", escape + "%")
        .replace("_", escape + "_")
    )


def new_code(prefix: str = "MM-", length: int = 8) -> str:
    """Unique-looking public code, e.g. MM-261107-K3J9X2QA."""
    day = datetime.now(timezone.utc).strftime("%y%m%d")
    return f"{prefix}{day}-" + "".join(secrets.choice(_ALPHABET) for _ in range(length))


def parse_id(data: str | None) -> int | None:
    """'product:12' -> 12 ; anything malformed -> None."""
    try:
        return int((data or "").rsplit(":", 1)[1])
    except (IndexError, ValueError):
        return None
