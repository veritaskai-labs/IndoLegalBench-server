"""Fixture bersama untuk test di bawah tests/modules/.

Sengaja dipisah dari tests/conftest.py supaya file bersama itu tidak
perlu disentuh. pytest otomatis memakai file ini untuk semua modul
(auth, suites, health), dan fixture db_session tetap diambil dari
tests/conftest.py.
"""

from collections.abc import Callable
from contextlib import ExitStack

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.shared.database import get_db
from app.shared.security import CurrentUser, Role, get_current_user


@pytest.fixture
def buat_pengguna() -> Callable[..., CurrentUser]:
    """Pembuat CurrentUser palsu, satu-satunya sumber identitas pengguna uji."""

    def buat(
        role: Role,
        *,
        user_id: str = "u-qa",
        email: str = "qa@veritask.test",
    ) -> CurrentUser:
        return CurrentUser(user_id=user_id, email=email, role=role)

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

    Fixture ini memalsukan HASIL verifikasi sesi, jadi ia tidak
    membuktikan apa pun soal login Zitadel.
    """
    stack = ExitStack()

    def login_sebagai(role: Role, **identitas: str) -> TestClient:
        pengguna = buat_pengguna(role, **identitas)

        def override_get_db():
            yield db_session

        app.dependency_overrides[get_db] = override_get_db
        app.dependency_overrides[get_current_user] = lambda: pengguna
        return stack.enter_context(TestClient(app))

    yield login_sebagai

    stack.close()
    app.dependency_overrides.clear()
