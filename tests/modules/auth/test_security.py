"""Unit test penjaga akses berbasis peran.

PBI-1, sub task [QA] Authentication, roles, sessions & admin members.

File ini sengaja TIDAK menyentuh database dan sebisa mungkin tidak
lewat HTTP. Yang diuji di sini adalah logika require_roles dan
terjemahan errornya. Pembuktian per endpoint ada di test_auth.py.
"""

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.main import domain_error_handler
from app.shared.exceptions import DomainError, ForbiddenError
from app.shared.security import CurrentUser, Role, get_current_user, require_roles

SEMUA_PERAN = [Role.AUTHOR, Role.REVIEWER, Role.ADMIN, Role.VIEWER]


def pengguna(role: Role) -> CurrentUser:
    return CurrentUser(user_id="u-qa", email="qa@veritask.test", role=role)


@pytest.mark.parametrize("role", SEMUA_PERAN)
def test_peran_yang_diizinkan_lolos(role: Role):
    """AC-2: peran yang berwenang boleh menjalankan aksinya."""
    guard = require_roles(role)

    assert guard(user=pengguna(role)).role is role


@pytest.mark.parametrize("role", SEMUA_PERAN)
def test_peran_di_luar_wewenang_ditolak(role: Role):
    """AC-2: percobaan akses di luar wewenang ditolak sistem.

    Tiap peran diuji terhadap guard yang mengizinkan ketiga peran
    lainnya, jadi keempat baris matriks benar-benar terlewati.
    """
    peran_lain = [r for r in SEMUA_PERAN if r is not role]
    guard = require_roles(*peran_lain)

    with pytest.raises(ForbiddenError):
        guard(user=pengguna(role))


def test_guard_bisa_mengizinkan_beberapa_peran_sekaligus():
    """AC-2: sebagian aksi wajar dibuka untuk lebih dari satu peran."""
    guard = require_roles(Role.ADMIN, Role.REVIEWER)

    assert guard(user=pengguna(Role.REVIEWER)).role is Role.REVIEWER
    with pytest.raises(ForbiddenError):
        guard(user=pengguna(Role.AUTHOR))


def test_penolakan_peran_sampai_ke_pengguna_sebagai_403():
    """AC-2: penolakan harus terbaca jelas oleh client, bukan error 500.

    Dipakai app kecil buatan sendiri, bukan app produksi, supaya tidak
    ada endpoint sungguhan yang ikut terpengaruh. Handler error yang
    dipasang tetap handler asli dari app.main, jadi yang diuji adalah
    rantai nyata ForbiddenError sampai jadi JSON 403.
    """
    app_uji = FastAPI()
    app_uji.add_exception_handler(DomainError, domain_error_handler)

    @app_uji.get("/khusus-admin", dependencies=[Depends(require_roles(Role.ADMIN))])
    def khusus_admin() -> dict[str, bool]:
        return {"ok": True}

    app_uji.dependency_overrides[get_current_user] = lambda: pengguna(Role.ADMIN)
    with TestClient(app_uji) as client:
        assert client.get("/khusus-admin").status_code == 200

    app_uji.dependency_overrides[get_current_user] = lambda: pengguna(Role.VIEWER)
    with TestClient(app_uji) as client:
        ditolak = client.get("/khusus-admin")

    assert ditolak.status_code == 403
    assert ditolak.json()["code"] == "forbidden"
