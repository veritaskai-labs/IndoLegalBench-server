"""Jumlah biaya yang sudah tersimpan. Tidak menghitung ulang harga."""

from decimal import ROUND_HALF_UP, Decimal

_CENT = Decimal("0.01")


def recorded_sum(call_count: int, priced_count: int, total: object) -> Decimal | None:
    """Tidak ada panggilan: 0. Panggilan ada tetapi semuanya tanpa biaya: None."""
    if call_count == 0:
        return Decimal("0.00")
    if priced_count == 0:
        return None
    amount = total if isinstance(total, Decimal) else Decimal(str(total))
    return amount.quantize(_CENT, rounding=ROUND_HALF_UP)


def rupiah_sum(left: Decimal | None, right: Decimal | None) -> Decimal | None:
    """Kedua sisi harus ada. Satu sisi kosong membuat jumlahnya kosong, bukan nol."""
    if left is None or right is None:
        return None
    return (left + right).quantize(_CENT, rounding=ROUND_HALF_UP)
