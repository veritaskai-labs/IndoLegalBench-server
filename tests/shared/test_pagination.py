"""Unit test bentuk paginasi di app/shared/pagination.py.

PBI-1, sub task [QA] SCRUM-96. Dipakai semua endpoint daftar, jadi
batas nilainya diuji tepat di tepi (boundary value analysis): nilai
terkecil dan terbesar yang sah, dan satu langkah di luarnya.
"""

import pytest
from pydantic import ValidationError

from app.shared.pagination import Page, PageParams


def test_nilai_bawaan_halaman_pertama_isi_dua_puluh():
    params = PageParams()

    assert (params.page, params.size, params.offset) == (1, 20, 0)


@pytest.mark.parametrize(
    ("page", "size", "offset"),
    [(1, 20, 0), (2, 20, 20), (3, 10, 20)],
)
def test_offset_melompati_halaman_sebelumnya(page: int, size: int, offset: int):
    assert PageParams(page=page, size=size).offset == offset


@pytest.mark.parametrize("size", [1, 100])
def test_size_di_tepi_batas_masih_diterima(size: int):
    assert PageParams(size=size).size == size


@pytest.mark.parametrize("nilai", [{"page": 0}, {"size": 0}, {"size": 101}])
def test_nilai_di_luar_batas_ditolak(nilai: dict[str, int]):
    with pytest.raises(ValidationError):
        PageParams(**nilai)


@pytest.mark.parametrize(
    ("total", "size", "pages"),
    [(0, 20, 0), (1, 20, 1), (20, 20, 1), (21, 20, 2), (45, 20, 3)],
)
def test_jumlah_halaman_dibulatkan_ke_atas(total: int, size: int, pages: int):
    halaman = Page[int](items=[], total=total, page=1, size=size)

    assert halaman.pages == pages


def test_size_nol_tidak_membagi_dengan_nol():
    halaman = Page[int](items=[], total=5, page=1, size=0)

    assert halaman.pages == 0
