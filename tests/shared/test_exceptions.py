"""Unit test exception domain di app/shared/exceptions.py.

PBI-1, sub task [QA] SCRUM-96. Semua modul melempar exception dari sini
lalu handler di main.py menerjemahkannya jadi HTTP, jadi status dan
kodenya adalah bagian dari kontrak API.

Nilai kode dibandingkan dengan atribut kelasnya, tidak ditulis ulang,
karena SCRUM-91 (PR #8) mengganti sebagian kode ke huruf besar.
"""

import pytest

from app.shared.exceptions import (
    ConflictError,
    DomainError,
    ForbiddenError,
    NotFoundError,
    UnauthorizedError,
    ValidationError,
)


@pytest.mark.parametrize(
    ("kelas", "status"),
    [
        (DomainError, 400),
        (NotFoundError, 404),
        (ConflictError, 409),
        (ValidationError, 422),
        (ForbiddenError, 403),
        (UnauthorizedError, 401),
    ],
)
def test_status_http_dan_pesan_tiap_exception(kelas: type[DomainError], status: int):
    error = kelas("pesan untuk pengguna")

    assert isinstance(error, DomainError)
    assert error.status_code == status
    assert error.message == "pesan untuk pengguna"
    assert str(error) == "pesan untuk pengguna"


def test_kode_bawaan_dipakai_kalau_tidak_diberi():
    assert ValidationError("x").code == ValidationError.code


def test_kode_khusus_menggantikan_kode_bawaan():
    error = ValidationError("x", code="CANNOT_DEACTIVATE_SELF")

    assert error.code == "CANNOT_DEACTIVATE_SELF"


def test_kode_khusus_tidak_bocor_ke_kelas_maupun_instance_lain():
    ValidationError("x", code="KODE_KHUSUS")

    assert ValidationError.code != "KODE_KHUSUS"
    assert ValidationError("y").code == ValidationError.code


def test_kode_kosong_diabaikan():
    assert ValidationError("x", code="").code == ValidationError.code
