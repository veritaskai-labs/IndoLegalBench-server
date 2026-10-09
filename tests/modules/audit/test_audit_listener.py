"""Test pencatat audit otomatis lewat listener ORM, ujung ke ujung.

PBI-18 AC1. Setiap test memanggil endpoint sungguhan lalu memeriksa
baris audit_logs yang lahir. Daftar event mengikuti katalog D6a, yang
sekaligus menjadi daftar periksa QA: setiap aksi tulis harus terbukti
menghasilkan baris audit.
"""

import logging
import uuid
from unittest.mock import patch

import pytest
from cryptography.fernet import Fernet

from app.modules.audit import listener
from app.modules.audit.models import AuditLog
from app.modules.suites.models import Suite
from app.shared.config import get_settings
from app.shared.security import Role
from tests.modules.conftest import USER_ID_QA

_RAHASIA = "provider-token-value-7kPq"  # pragma: allowlist secret
_RAHASIA_BARU = "replacement-token-value-9mRx"  # pragma: allowlist secret


@pytest.fixture
def encryption_key(monkeypatch):
    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", Fernet.generate_key().decode())
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _log(db_session) -> list[AuditLog]:
    return db_session.query(AuditLog).order_by(AuditLog.id).all()


def _actions(db_session) -> list[str]:
    return [baris.action for baris in _log(db_session)]


def _suite(client, name: str = "Ketenagakerjaan 2026") -> str:
    response = client.post("/suites", json={"name": name, "description": "Tema uji"})
    assert response.status_code == 201
    return response.json()["id"]


def _kasus(**ubah) -> dict:
    badan = {
        "case_code": "PHK-001",
        "identity": {
            "title": "PHK sepihak",
            "question": "Apakah PHK tanpa surat sah?",
            "category": "ketenagakerjaan",
        },
        "legal_refs": [
            {"regulation_type": "uu", "regulation_number": "13", "year": 2003, "pasal": "151"}
        ],
        "answer_criteria": {
            "must_contain": ["surat"],
            "must_not_contain": [],
            "expected_conclusion": "tidak sah",
        },
        "traps": [{"description": "Mencampur upah", "expected_model_behavior": "menolak"}],
        "split_tag": "dev",
    }
    badan.update(ubah)
    return badan


def _produk(client) -> str:
    response = client.post(
        "/admin/providers",
        json={
            "name": "AiYU",
            "provider_type": "openai_compatible",
            "base_url": "https://api.example.test/v1",
            "model_name": "aiyu-1",
            "credential": _RAHASIA,
            "rate_limit_per_minute": 30,
            "monthly_budget_idr": "1500000.00",
        },
    )
    assert response.status_code == 201
    return response.json()["id"]


# --- Pelaku, request, dan transaksi -------------------------------------------


def test_baris_mencatat_pelaku_dan_request(as_role, db_session):
    client = as_role(Role.AUTHOR)

    response = client.post("/suites", json={"name": "Pidana"})

    baris = _log(db_session)[0]
    assert baris.action == "suite.created"
    assert (baris.actor_user_id, baris.actor_role) == (USER_ID_QA, "author")
    assert str(baris.request_id) == response.headers["X-Request-ID"]
    assert str(baris.entity_id) == response.json()["id"]


def test_audit_gagal_maka_perubahan_ikut_batal(as_role, db_session):
    """D6b: tidak ada perubahan tanpa log."""
    client = as_role(Role.AUTHOR)

    with (
        patch.object(listener.audit_service, "record", side_effect=RuntimeError("audit mati")),
        pytest.raises(RuntimeError),
    ):
        client.post("/suites", json={"name": "Pidana"})
    db_session.rollback()

    assert db_session.query(Suite).count() == 0
    assert db_session.query(AuditLog).count() == 0


def test_audit_gagal_tercatat_jelas_di_log_server(as_role, db_session, caplog):
    """Error tetap dilempar (D6b), tapi log server menyebut event mana yang gagal."""
    client = as_role(Role.AUTHOR)

    with (
        patch.object(listener.audit_service, "record", side_effect=RuntimeError("audit mati")),
        caplog.at_level(logging.ERROR, logger=listener.logger.name),
        pytest.raises(RuntimeError),
    ):
        client.post("/suites", json={"name": "Pidana"})
    db_session.rollback()

    assert "suite.created" in caplog.text
    assert "change rolled back" in caplog.text
    assert "audit mati" in caplog.text


def test_aksi_baca_tidak_dicatat(as_role, db_session):
    """D6a: aksi melihat tidak dicatat di Sprint 2."""
    client = as_role(Role.AUTHOR)
    suite_id = _suite(client)

    client.get("/suites")
    client.get(f"/suites/{suite_id}")

    assert _actions(db_session) == ["suite.created"]


def test_simpan_tanpa_perubahan_nilai_tidak_dicatat(as_role, db_session):
    client = as_role(Role.AUTHOR)
    suite_id = _suite(client)

    client.patch(f"/suites/{suite_id}", json={"name": "Ketenagakerjaan 2026"})

    assert _actions(db_session) == ["suite.created"]


# --- Suite --------------------------------------------------------------------


def test_suite_diubah_mencatat_field_yang_berubah_saja(as_role, db_session):
    client = as_role(Role.AUTHOR)
    suite_id = _suite(client)

    client.patch(f"/suites/{suite_id}", json={"name": "Perdata"})

    baris = _log(db_session)[-1]
    assert baris.action == "suite.updated"
    assert (baris.before, baris.after) == ({"name": "Ketenagakerjaan 2026"}, {"name": "Perdata"})


def test_suite_diarsip_lalu_dibuka(as_role, db_session):
    client = as_role(Role.AUTHOR)
    suite_id = _suite(client)

    client.post(f"/suites/{suite_id}/archive")
    client.post(f"/suites/{suite_id}/unarchive")

    assert _actions(db_session) == ["suite.created", "suite.archived", "suite.unarchived"]


def test_suite_dihapus(as_role, db_session):
    client = as_role(Role.AUTHOR)
    suite_id = _suite(client)

    assert client.delete(f"/suites/{suite_id}").status_code == 204

    baris = _log(db_session)[-1]
    assert baris.action == "suite.deleted"
    assert baris.after["deleted_at"]


# --- Case ---------------------------------------------------------------------


def test_kasus_dibuat_dan_diubah(as_role, db_session):
    client = as_role(Role.AUTHOR)
    suite_id = _suite(client)
    kasus = client.post(f"/suites/{suite_id}/cases", json=_kasus()).json()

    client.put(
        f"/cases/{kasus['id']}", json=_kasus(identity={**_kasus()["identity"], "title": "PHK"})
    )

    dibuat, diubah = _log(db_session)[1:]
    assert dibuat.action == "case.created"
    assert dibuat.after == {"case_code": "PHK-001", "version_no": 1}
    assert diubah.action == "case.updated"
    assert (diubah.before, diubah.after) == ({"title": "PHK sepihak"}, {"title": "PHK"})
    assert str(diubah.case_id) == kasus["id"]


def test_tag_kasus_diubah_dev_ke_test(as_role, db_session):
    client = as_role(Role.AUTHOR)
    suite_id = _suite(client)
    kasus = client.post(f"/suites/{suite_id}/cases", json=_kasus()).json()

    client.put(f"/cases/{kasus['id']}", json=_kasus(split_tag="test"))

    baris = _log(db_session)[-1]
    assert baris.action == "case.tag_changed"
    assert baris.after == {"split_tag": "test", "warning": True}


# --- AI product ---------------------------------------------------------------


def test_produk_ai_didaftarkan_tanpa_kredensial(as_role, db_session, encryption_key):
    client = as_role(Role.ADMIN)

    _produk(client)

    baris = _log(db_session)[0]
    assert baris.action == "ai_product.registered"
    assert baris.after["name"] == "AiYU"
    assert _RAHASIA not in str(baris.after)


def test_kredensial_diganti_hanya_penanda(as_role, db_session, encryption_key):
    """PBI-10 AC2: nilai kredensial lama maupun baru tidak pernah tercatat."""
    client = as_role(Role.ADMIN)
    produk_id = _produk(client)

    client.patch(f"/admin/providers/{produk_id}", json={"credential": _RAHASIA_BARU})

    baris = _log(db_session)[-1]
    assert baris.action == "ai_product.credential_rotated"
    assert baris.after == {"credential": "rotated"}
    seluruh_log = str([(b.before, b.after) for b in _log(db_session)])
    assert _RAHASIA not in seluruh_log
    assert _RAHASIA_BARU not in seluruh_log


def test_produk_ai_dinonaktifkan_lalu_diaktifkan(as_role, db_session, encryption_key):
    client = as_role(Role.ADMIN)
    produk_id = _produk(client)

    client.post(f"/admin/providers/{produk_id}/deactivate")
    client.post(f"/admin/providers/{produk_id}/activate")

    assert _actions(db_session)[1:] == ["ai_product.deactivated", "ai_product.activated"]


# --- User ---------------------------------------------------------------------


def test_anggota_ditambah_diubah_perannya_lalu_dinonaktifkan(as_role, db_session):
    client = as_role(Role.ADMIN)
    anggota = client.post(
        "/admin/users", json={"email": "baru@veritask.ai", "name": "Baru", "role": "author"}
    ).json()

    client.patch(f"/admin/users/{anggota['id']}", json={"role": "reviewer"})
    client.post(f"/admin/users/{anggota['id']}/deactivate")

    ditambah, diubah, dinonaktifkan = _log(db_session)
    assert (ditambah.action, ditambah.after) == (
        "user.added",
        {"email": "baru@veritask.ai", "role": "author"},
    )
    assert (diubah.action, diubah.after) == ("user.role_changed", {"role": "reviewer"})
    assert dinonaktifkan.action == "user.deactivated"
    # Nonaktifkan menghapus sesi dan commit lebih dulu, jadi baris user sudah
    # kedaluwarsa saat is_active diubah. Nilai lama tetap harus tercatat.
    assert (dinonaktifkan.before, dinonaktifkan.after) == (
        {"is_active": True},
        {"is_active": False},
    )
    assert {baris.actor_role for baris in _log(db_session)} == {"admin"}


def test_nilai_lama_tercatat_walau_baris_kedaluwarsa(as_role, db_session):
    """Commit di tengah request membuat atribut kedaluwarsa. before tidak boleh jadi null."""
    client = as_role(Role.AUTHOR)
    suite_id = _suite(client)
    suite = db_session.get(Suite, uuid.UUID(suite_id))
    db_session.expire(suite)

    suite.name = "Nama Baru"
    db_session.commit()

    baris = _log(db_session)[-1]
    assert (baris.before, baris.after) == ({"name": "Ketenagakerjaan 2026"}, {"name": "Nama Baru"})


def test_model_yang_dipetakan_belakangan_ikut_memuat_nilai_lama():
    """Model baru (mis. tabel runs di PBI-11) dipetakan setelah install() jalan."""
    from sqlalchemy import Boolean, String, Uuid, event
    from sqlalchemy.orm import DeclarativeBase, Mapped, configure_mappers, mapped_column

    class _BaseLain(DeclarativeBase):
        pass

    class _UserLain(_BaseLain):
        __tablename__ = "users"
        id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
        role: Mapped[str] = mapped_column(String(32))
        is_active: Mapped[bool] = mapped_column(Boolean)

    configure_mappers()

    assert event.contains(_UserLain.role, "set", listener._no_op)
    assert event.contains(_UserLain.is_active, "set", listener._no_op)
