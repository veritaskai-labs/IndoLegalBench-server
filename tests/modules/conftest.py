"""Fixture bersama untuk test di bawah tests/modules/.

Sengaja dipisah dari tests/conftest.py supaya file bersama itu tidak
perlu disentuh. pytest otomatis memakai file ini untuk semua modul
(auth, suites, health), dan fixture db_session tetap diambil dari
tests/conftest.py.
"""

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
        return CurrentUser(user_id=user_id, name=name, email=email, role=role)

    return buat


@pytest.fixture
def as_role(db_session, buat_pengguna) -> Callable[..., TestClient]:
    """Client yang sudah dianggap login sebagai peran tertentu.

    PBI-1, sub task [QA] SCRUM-96. Router suites dijaga require_roles
    (author dan admin, SCRUM-99). Fixture ini memasang pengguna palsu
    supaya test tidak perlu sesi Zitadel.

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
