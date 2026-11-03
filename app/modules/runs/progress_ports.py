"""Port yang dipakai endpoint progres (PBI-12 AC5, SCRUM-215).

Tabel sesi dan upaya (SCRUM-205) belum ada di staging, jadi service
progres hanya berbicara lewat port ini. Implementasi nyata menyusul.
Sisa anggaran memakai SpendPort milik ringkasan, supaya satu produk
punya satu sumber angka pemakaian.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol


@dataclass(frozen=True)
class SessionProgress:
    """Hitungan satu sesi hasil agregasi, bukan baris tabelnya.

    completed_calls termasuk yang gagal. updated_at adalah waktu angka ini
    dihitung, yang ditampilkan sebagai "terakhir diperbarui" (AC5).
    """

    product_id: uuid.UUID
    total_calls: int
    completed_calls: int
    failed_calls: int
    started_at: datetime | None
    updated_at: datetime
    running_cost_idr: Decimal


class SessionProgressPort(Protocol):
    def progress(self, measurement_id: uuid.UUID) -> SessionProgress | None:
        """Hitungan sesi, atau None bila sesinya tidak ada."""
        raise NotImplementedError
