"""Audit provider penilai (LLM judge). PBI-18 AC1, PBI-14 AC6, SCRUM-258.

Slice 1 mengecualikan judge_providers dari audit sampai ada penulisnya. API
/admin/judge-providers adalah penulisnya, jadi sekarang pendaftaran, perubahan
harga dan anggaran, penggantian kredensial, serta aktif/nonaktif tercatat.
Kredensial dan hint-nya tidak pernah masuk audit_logs (pola AiProductTracker,
PBI-10 AC2, L5): penggantian kredensial hanya dicatat dengan penanda tetap.
Di halaman audit objeknya tampil dengan nama penilai, bukan UUID.

Label: Positif = perilaku yang diharapkan, Negatif = tidak boleh tercatat,
Edge = batas.
"""

import uuid
from decimal import Decimal
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select

from app.modules.audit.models import AuditEntityType, AuditLog
from app.modules.audit.redaction import CREDENTIAL_ROTATED
from app.modules.audit.trackers import Change, tracker_for
from app.modules.auth.models import User
from app.shared.config import get_settings
from app.shared.security import Role
from tests.modules.conftest import USER_ID_QA

PENILAI_ID = uuid.UUID("00000000-0000-0000-0000-000000000258")
_SECRET = "judge-audit-secret-5vLp"  # pragma: allowlist secret
_OTHER_SECRET = "judge-audit-rotated-2cNw"  # pragma: allowlist secret


def _baris(**kolom) -> SimpleNamespace:
    isi = {
        "id": PENILAI_ID,
        "name": "Penilai sintetis",
        "provider_type": "openai_compatible",
        "base_url": "https://judge.example.test/v1",
        "model_name": "judge-model-1",
        "credential_encrypted": b"ciphertext-sintetis",
        "credential_hint": "5vLp",
        "price_input_usd_per_mtok": Decimal("0.2700"),
        "price_output_usd_per_mtok": Decimal("1.1000"),
        "monthly_budget_idr": Decimal("500000.00"),
        "is_active": True,
    } | kolom
    return SimpleNamespace(**isi)


# --- Tracker ----------------------------------------------------------------------


def test_pendaftaran_tercatat_tanpa_kredensial_dan_hint():
    """Positif: judge_provider.registered memuat isian D6 kecuali kredensial dan hint."""
    tracker = tracker_for("judge_providers")

    (event,) = tracker.created(_baris())

    assert tracker.entity_type == AuditEntityType.JUDGE_PROVIDER
    assert (event.action, event.entity_id) == ("judge_provider.registered", PENILAI_ID)
    assert event.after == {
        "name": "Penilai sintetis",
        "provider_type": "openai_compatible",
        "base_url": "https://judge.example.test/v1",
        "model_name": "judge-model-1",
        "price_input_usd_per_mtok": "0.2700",
        "price_output_usd_per_mtok": "1.1000",
        "monthly_budget_idr": "500000.00",
        "is_active": True,
    }


def test_perubahan_harga_dan_anggaran_tercatat_sebelum_dan_sesudah():
    (event,) = tracker_for("judge_providers").updated(
        _baris(monthly_budget_idr=Decimal("750000.00")),
        {"monthly_budget_idr": Change(Decimal("500000.00"), Decimal("750000.00"))},
    )

    assert (event.action, event.before, event.after) == (
        "judge_provider.updated",
        {"monthly_budget_idr": "500000.00"},
        {"monthly_budget_idr": "750000.00"},
    )


def test_ganti_kredensial_hanya_penanda_tanpa_nilai():
    """Negatif: ciphertext lama/baru dan hint tidak ikut; cukup penanda "rotated"."""
    events = tracker_for("judge_providers").updated(
        _baris(),
        {
            "credential_encrypted": Change(b"lama", b"baru"),
            "credential_hint": Change("aaaa", "bbbb"),
        },
    )

    assert [(e.action, e.before, e.after) for e in events] == [
        ("judge_provider.credential_rotated", None, dict(CREDENTIAL_ROTATED))
    ]


@pytest.mark.parametrize(
    ("lama", "baru", "aksi"),
    [(False, True, "judge_provider.activated"), (True, False, "judge_provider.deactivated")],
    ids=["aktif", "nonaktif"],
)
def test_aktif_dan_nonaktif_tercatat_sebagai_event_sendiri(lama, baru, aksi):
    (event,) = tracker_for("judge_providers").updated(
        _baris(is_active=baru), {"is_active": Change(lama, baru)}
    )

    assert (event.action, event.before, event.after) == (
        aksi,
        {"is_active": lama},
        {"is_active": baru},
    )


def test_hint_saja_berubah_tidak_tercatat():
    """Edge: hint selalu berubah bersama ciphertext; sendiri tidak membuat event."""
    assert tracker_for("judge_providers").updated(_baris(), {}) == []
    assert "credential_hint" not in tracker_for("judge_providers").fields


# --- Lewat endpoint ---------------------------------------------------------------


@pytest.fixture
def admin(as_role, db_session, monkeypatch):
    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", Fernet.generate_key().decode())
    get_settings.cache_clear()
    db_session.add(User(id=USER_ID_QA, email="qa@veritask.test", name="Pengguna QA", role="admin"))
    db_session.commit()
    yield as_role(Role.ADMIN)
    get_settings.cache_clear()


def _daftar(admin) -> str:
    response = admin.post(
        "/admin/judge-providers",
        json={
            "name": "Penilai audit",
            "provider_type": "openai_compatible",
            "base_url": "https://judge.example.test/v1",
            "model_name": "judge-model-1",
            "credential": _SECRET,
            "price_input_usd_per_mtok": "0.27",
            "price_output_usd_per_mtok": "1.1",
            "monthly_budget_idr": "500000",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _catatan(db_session) -> list[AuditLog]:
    return list(
        db_session.scalars(
            select(AuditLog)
            .where(AuditLog.entity_type == AuditEntityType.JUDGE_PROVIDER.value)
            .order_by(AuditLog.id)
        )
    )


def test_alur_admin_tercatat_dengan_pelaku_dan_tanpa_rahasia(admin, db_session):
    """Positif: daftar, ganti kredensial + anggaran, nonaktifkan; semua oleh Admin,
    dan tidak ada payload yang memuat kredensial atau hint-nya."""
    penilai = _daftar(admin)
    admin.patch(
        f"/admin/judge-providers/{penilai}",
        json={"credential": _OTHER_SECRET, "monthly_budget_idr": "750000"},
    )
    admin.post(f"/admin/judge-providers/{penilai}/deactivate")

    catatan = _catatan(db_session)

    assert [row.action for row in catatan] == [
        "judge_provider.registered",
        "judge_provider.credential_rotated",
        "judge_provider.updated",
        "judge_provider.deactivated",
    ]
    assert {str(row.entity_id) for row in catatan} == {penilai}
    assert {row.actor_user_id for row in catatan} == {USER_ID_QA}
    isi = str([(row.before, row.after) for row in catatan])
    for rahasia in (_SECRET, _OTHER_SECRET, _SECRET[-4:], _OTHER_SECRET[-4:]):
        assert rahasia not in isi


def test_halaman_audit_menampilkan_nama_penilai(admin):
    """Positif: kolom Objek (F-06) berisi nama penilai, bukan UUID."""
    _daftar(admin)

    body = admin.get("/audit-logs", params={"entity_type": "judge_provider"}).json()

    assert [item["entity_label"] for item in body["items"]] == ["Penilai audit"]
