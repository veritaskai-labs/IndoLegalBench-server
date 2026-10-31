"""Body 422 untuk kegagalan validasi request yang selalu bisa dijadikan JSON.

Pydantic mengulang nilai yang ditolak di field `input`. Untuk 1e309, NaN,
atau -Infinity nilainya bukan angka JSON yang sah, dan JSONResponse gagal
menulisnya sehingga jawabannya menjadi 500. Di sini nilai itu diganti
tulisannya ("inf", "-inf", "nan") sebelum dikirim.
"""

import math
from typing import Any

from fastapi.encoders import jsonable_encoder


def _aman(nilai: Any) -> Any:
    if isinstance(nilai, float) and not math.isfinite(nilai):
        return str(nilai)
    if isinstance(nilai, dict):
        return {kunci: _aman(isi) for kunci, isi in nilai.items()}
    if isinstance(nilai, (list, tuple)):
        return [_aman(isi) for isi in nilai]
    return nilai


def detail_aman(errors: list[dict[str, Any]]) -> list[Any]:
    """Daftar error Pydantic yang siap ditulis sebagai JSON."""
    return jsonable_encoder(_aman(errors))
