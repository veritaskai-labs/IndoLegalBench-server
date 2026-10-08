"""Penyaring nilai rahasia sebelum masuk kolom before/after.

PBI-18 dan PBI-10 AC2: nilai kredensial produk AI tidak pernah tercatat.
Kunci dicocokkan per segmen nama (snake_case, kebab-case, camelCase),
bukan potongan kata, supaya "max_tokens" tetap tercatat sedangkan
"access_token" dibuang.
"""

import re
from typing import Any

# Cukup penanda ini yang dicatat saat kredensial diganti (D6a).
CREDENTIAL_ROTATED: dict[str, str] = {"credential": "rotated"}

_SEGMEN_RAHASIA = frozenset({"credential", "secret", "password", "authorization", "apikey"})
_PEMISAH_SEGMEN = re.compile(r"[_\-\s]+|(?<=[a-z0-9])(?=[A-Z])")


def without_secrets(value: Any) -> Any:
    """Salinan value tanpa kunci rahasia, termasuk di dict bersarang dan daftar."""
    if isinstance(value, dict):
        return {
            key: without_secrets(item)
            for key, item in value.items()
            if not _is_secret(str(key), item)
        }
    if isinstance(value, list):
        return [without_secrets(item) for item in value]
    return value


def _is_secret(key: str, item: Any) -> bool:
    segmen = [bagian.lower() for bagian in _PEMISAH_SEGMEN.split(key) if bagian]
    if segmen == ["credential"] and item == CREDENTIAL_ROTATED["credential"]:
        return False
    if _SEGMEN_RAHASIA.intersection(segmen):
        return True
    if any(
        pertama == "api" and kedua == "key"
        for pertama, kedua in zip(segmen, segmen[1:], strict=False)
    ):
        return True
    # "access_token" dan "token" rahasia, "token_limit" atau "max_tokens" bukan.
    return bool(segmen) and segmen[-1] == "token"
