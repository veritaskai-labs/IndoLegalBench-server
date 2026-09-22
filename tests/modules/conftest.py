"""Fixture bersama untuk test di bawah tests/modules/.

Sengaja dipisah dari tests/conftest.py supaya file bersama itu tidak
perlu disentuh. pytest otomatis memakai file ini untuk semua modul
(auth, suites, health), dan fixture db_session tetap diambil dari
tests/conftest.py.
"""

import inspect
import uuid
from collections.abc import Callable
from contextlib import ExitStack

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.shared.database import get_db
from app.shared.security import CurrentUser, Role, get_current_user

# UUID tetap, bukan uuid4(), supaya hasil test selalu sama (Repeatable).
USER_ID_QA = uuid.UUID("00000000-0000-0000-0000-00000000a000")

# SCRUM-91 (PR #8) menambah parameter wajib `name` ke CurrentUser.
# Pengecekan ini membuat fixture jalan di staging maupun setelah PR #8
# merge. Hapus pengecekannya begitu PR #8 sudah ada di staging.
_CURRENT_USER_BUTUH_NAME = "name" in inspect.signature(CurrentUser).parameters


@pytest.fixture
def buat_pengguna() -> Callable[..., CurrentUser]:
    """Pembuat CurrentUser palsu, satu-satunya sumber identitas pengguna uji."""

    def buat(
        role: Role,
        *,
        user_id: uuid.UUID = USER_ID_QA,
        email: str = "qa@veritask.test",
        name: str = "Pengguna QA",
    ) -> CurrentUser:
        identitas = {"user_id": user_id, "email": email, "role": role}
        if _CURRENT_USER_BUTUH_NAME:
            identitas["name"] = name
        return CurrentUser(**identitas)

    return buat


@pytest.fixture
def as_role(db_session, buat_pengguna) -> Callable[..., TestClient]:
    """Client yang sudah dianggap login sebagai peran tertentu.

    PBI-1, sub task [QA] SCRUM-96. Router suites sekarang belum dijaga
    (lihat TODO require_roles di app/modules/suites/router.py). Begitu
    guard itu diaktifkan, test yang tadinya polos akan kena 401, dan
    fixture ini tambalannya.

    Pemakaian:

        def test_author_boleh_membuat_suite(as_role):
            client = as_role(Role.AUTHOR)
            assert client.post("/suites", json={...}).status_code == 201

    Panggil SEKALI per test. Override dipasang pada objek app yang sama,
    jadi kalau dipanggil dua kali, client pertama ikut berganti peran.

    Override yang sudah ada sebelum fixture ini jalan disimpan lalu
    dikembalikan di akhir, jadi fixture lain yang memasang override
    sendiri tidak ikut terhapus.

    Fixture ini memalsukan HASIL verifikasi sesi, jadi ia tidak
    membuktikan apa pun soal login Zitadel.
    """
    override_semula = dict(app.dependency_overrides)
    stack = ExitStack()

    def login_sebagai(role: Role, **identitas) -> TestClient:
        pengguna = buat_pengguna(role, **identitas)

        def override_get_db():
            yield db_session

        app.dependency_overrides[get_db] = override_get_db
        app.dependency_overrides[get_current_user] = lambda: pengguna
        return stack.enter_context(TestClient(app))

    yield login_sebagai

    stack.close()
    app.dependency_overrides.clear()
    app.dependency_overrides.update(override_semula)
