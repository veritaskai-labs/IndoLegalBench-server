"""Audit arsip sesi pengukuran. PBI-11 AC10, SCRUM-210, PBI-18.

Reviewer SCRUM-205 meminta tracker measurement_sessions ditambahkan bersama
aksi arsip. Tracker ini mencatat arsip (di sini) serta terima apa adanya
(test_session_status_audit). Perpindahan status ditulis lewat UPDATE Core, jadi
dicatat repository runs sendiri, bukan tracker ini (SCRUM-214).

Setiap tes diberi label di docstring-nya:
- Positif: perilaku yang memang diharapkan terjadi.
- Negatif: kejadian yang tidak boleh menulis catatan audit.
- Edge: batas atau kasus pinggir.
"""

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

from sqlalchemy import select

from app.modules.audit.models import AuditEntityType, AuditLog
from app.modules.audit.trackers import Change, tracker_for
from app.modules.auth.models import User
from app.modules.runs.models import SessionStatus
from app.shared.security import Role
from tests.modules.conftest import USER_ID_QA
from tests.modules.providers.test_product_lists import _otomatis
from tests.modules.runs.test_session_list_endpoint import _sesi

SESI_ID = uuid.UUID("00000000-0000-0000-0000-000000000210")
WAKTU_ARSIP = datetime(2026, 11, 3, 9, 0, tzinfo=UTC)


def _baris(**kolom) -> SimpleNamespace:
    return SimpleNamespace(id=SESI_ID, label="Run November", **kolom)


def test_arsip_dicatat_sebagai_measurement_session_archived():
    """Positif: perubahan archived_at menjadi satu event arsip."""
    # Arrange
    tracker = tracker_for("measurement_sessions")

    # Act
    (event,) = tracker.updated(_baris(), {"archived_at": Change(None, WAKTU_ARSIP)})

    # Assert
    assert tracker.entity_type == AuditEntityType.MEASUREMENT_SESSION
    assert event.action == "measurement_session.archived"
    assert event.entity_id == SESI_ID
    assert event.before == {"archived_at": None}
    assert event.after == {"archived_at": WAKTU_ARSIP.isoformat()}


def test_sesi_baru_tidak_dicatat_tracker_ini():
    """Negatif: mulai sesi belum ada di katalog D6a, ditentukan bersama SCRUM-207."""
    # Act + Assert
    assert tracker_for("measurement_sessions").created(_baris()) == []


def test_status_dari_worker_tidak_dicatat():
    """Negatif: tracker tidak memantau status, supaya tidak tercatat dua kali (SCRUM-214)."""
    # Arrange
    tracker = tracker_for("measurement_sessions")

    # Act + Assert
    assert "status" not in tracker.fields
    assert tracker.updated(_baris(), {}) == []


def _siapkan_sesi(db_session, status=SessionStatus.FINISHED):
    # Pembuatnya pengguna as_role, karena Developer AI hanya mengarsipkan sesi miliknya.
    pembuat = User(
        id=USER_ID_QA, email="pembuat-audit@veritask.test", name="Dev AI", role=Role.DEVELOPER_AI
    )
    produk = _otomatis("Produk Audit")
    db_session.add_all([pembuat, produk])
    db_session.commit()
    return _sesi(db_session, produk, pembuat, label="Run November", status=status)


def _catatan_arsip(db_session) -> list[AuditLog]:
    db_session.expire_all()
    return list(
        db_session.scalars(
            select(AuditLog).where(AuditLog.action == "measurement_session.archived")
        )
    )


def test_arsip_lewat_endpoint_menulis_satu_catatan_dengan_pelaku(as_role, db_session):
    """Positif: pelaku dan perannya tercatat dari sesi login."""
    # Arrange
    sesi = _siapkan_sesi(db_session)

    # Act
    response = as_role(Role.DEVELOPER_AI).patch(f"/measurements/{sesi.id}/archive")

    # Assert
    assert response.status_code == 200
    (catatan,) = _catatan_arsip(db_session)
    assert catatan.entity_type == "measurement_session"
    assert catatan.entity_id == sesi.id
    assert catatan.actor_user_id == USER_ID_QA
    assert catatan.actor_role == "developer_ai"


def test_arsip_yang_ditolak_tidak_menulis_catatan(as_role, db_session):
    """Negatif: sesi berjalan ditolak 409 dan tidak meninggalkan jejak arsip."""
    # Arrange
    sesi = _siapkan_sesi(db_session, status=SessionStatus.RUNNING)

    # Act
    response = as_role(Role.ADMIN).patch(f"/measurements/{sesi.id}/archive")

    # Assert
    assert response.status_code == 409
    assert _catatan_arsip(db_session) == []


def test_halaman_audit_menampilkan_label_sesi(as_role, db_session):
    """Edge: kolom Objek di halaman audit memakai label sesi, bukan id saja (F-06)."""
    # Arrange
    sesi = _siapkan_sesi(db_session)
    client = as_role(Role.ADMIN)
    client.patch(f"/measurements/{sesi.id}/archive")

    # Act
    response = client.get("/audit-logs", params={"entity_type": "measurement_session"})

    # Assert
    assert response.status_code == 200
    (item,) = response.json()["items"]
    assert item["action"] == "measurement_session.archived"
    assert item["entity_label"] == "Run November"
