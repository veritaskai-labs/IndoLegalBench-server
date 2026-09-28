"""Cabang tepi modul auth yang belum tersentuh test lain.

PBI-1 (SCRUM-68), sub task QA (SCRUM-96). Semuanya jalur gagal atau
jalur "tidak ada apa-apa", yaitu bagian yang paling jarang dijalankan
tapi paling sering jadi sumber kejutan saat produksi.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.modules.auth import repository, service
from app.modules.auth.pending import PendingAuthStore
from app.modules.auth.schemas import UserUpdateRoleRequest
from app.modules.auth.seeds import AUTHOR_ID, seed_users
from app.shared.exceptions import InvalidOidcStateError, NotFoundError
from app.shared.security import CurrentUser, Role


@pytest.mark.parametrize(
    ("code", "state"),
    [(None, "state-1"), ("kode-1", None), (None, None)],
)
def test_callback_tanpa_code_atau_state_ditolak(db_session, code, state):
    """Callback yang dipanggil langsung tanpa alur login harus ditolak."""
    with pytest.raises(InvalidOidcStateError):
        service.complete_login(db_session, oidc=None, code=code, state=state)  # type: ignore[arg-type]


def test_state_login_yang_kedaluwarsa_dianggap_tidak_ada():
    """Penjaga replay: state lama tidak boleh bisa dipakai lagi."""
    store = PendingAuthStore(ttl_minutes=0)
    pending = store.create(nonce="nonce-1", code_verifier="verifier-1")

    assert store.pop(pending.state) is None


def test_state_yang_tidak_dikenal_dianggap_tidak_ada():
    assert PendingAuthStore().pop("state-karangan") is None


def test_nama_pengguna_diperbarui_saat_berubah_di_idp(db_session):
    seed_users(db_session)
    user = repository.get_user_by_id(db_session, AUTHOR_ID)

    diperbarui = repository.update_user_profile(db_session, user, name="Nama Baru", email=None)

    assert diperbarui.name == "Nama Baru"
    assert diperbarui.email == user.email


def test_profil_tanpa_nama_dan_email_tidak_mengubah_apa_pun(db_session):
    seed_users(db_session)
    user = repository.get_user_by_id(db_session, AUTHOR_ID)
    sebelum = (user.name, user.email, user.updated_at)

    hasil = repository.update_user_profile(db_session, user, name=None, email=None)

    assert (hasil.name, hasil.email, hasil.updated_at) == sebelum


def test_menghapus_sesi_yang_sudah_tidak_ada_tidak_meledak(db_session):
    repository.delete_session(db_session, uuid.uuid4())


def test_ubah_peran_pengguna_yang_tidak_ada_menjadi_404(db_session):
    admin = CurrentUser(
        user_id=uuid.uuid4(), name="Admin", email="admin@veritask.test", role=Role.ADMIN
    )

    with pytest.raises(NotFoundError):
        service.AuthService(db_session).update_member_role(
            uuid.uuid4(), UserUpdateRoleRequest(role=Role.REVIEWER), admin
        )


def test_waktu_yang_sudah_berzona_tidak_diubah():
    """SQLite mengembalikan waktu tanpa zona, PostgreSQL dengan zona.

    Perbandingan idle timeout harus benar di keduanya.
    """
    berzona = datetime.now(UTC) - timedelta(minutes=5)

    assert service._aware(berzona) is berzona
    assert service._aware(berzona.replace(tzinfo=None)).tzinfo is UTC
