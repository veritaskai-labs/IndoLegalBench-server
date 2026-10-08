"""Test tabel audit_logs.

PBI-18. Bentuk tabel mengikuti diagram D5b (ERD Audit Log): siapa,
kapan, aksi apa, terhadap objek apa, nilai sebelum dan sesudah, serta
alasan bila ada.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.exc import IntegrityError

from app.modules.audit.models import AuditEntityType, AuditLog
from app.modules.auth.models import User
from app.shared.database import Base
from app.shared.security import Role

KASUS_ID = uuid.UUID("00000000-0000-0000-0000-0000000000c1")


def _admin(db_session) -> User:
    admin = User(email="admin@veritask.test", name="Admin QA", role=Role.ADMIN)
    db_session.add(admin)
    db_session.commit()
    return admin


def _baris(**kolom) -> AuditLog:
    nilai = {
        "action": "case.updated",
        "entity_type": AuditEntityType.CASE,
        "entity_id": KASUS_ID,
    }
    nilai.update(kolom)
    return AuditLog(**nilai)


def test_tabel_terdaftar_di_metadata():
    assert "audit_logs" in Base.metadata.tables


def test_kolom_sesuai_erd_d5b():
    kolom = set(Base.metadata.tables["audit_logs"].columns.keys())

    assert kolom == {
        "id",
        "occurred_at",
        "actor_user_id",
        "actor_role",
        "action",
        "entity_type",
        "entity_id",
        "case_id",
        "before",
        "after",
        "reason",
        "request_id",
    }


def test_index_sesuai_erd_d5b():
    nama = {index.name for index in Base.metadata.tables["audit_logs"].indexes}

    assert nama == {
        "ix_audit_logs_occurred_at",
        "ix_audit_logs_entity",
        "ix_audit_logs_actor_occurred_at",
        "ix_audit_logs_case_occurred_at",
    }


def test_index_waktu_urut_terbaru_dulu():
    """Pencarian default menampilkan kejadian terbaru lebih dulu."""
    index = next(
        item
        for item in Base.metadata.tables["audit_logs"].indexes
        if item.name == "ix_audit_logs_occurred_at"
    )

    assert "DESC" in str(next(iter(index.expressions))).upper()


def test_simpan_baris_lengkap(db_session):
    admin = _admin(db_session)
    request_id = uuid.uuid4()
    db_session.add(
        _baris(
            actor_user_id=admin.id,
            actor_role=Role.ADMIN,
            case_id=KASUS_ID,
            before={"title": "Lama"},
            after={"title": "Baru"},
            reason="Perbaikan judul",
            request_id=request_id,
        )
    )
    db_session.commit()

    tersimpan = db_session.query(AuditLog).one()

    assert isinstance(tersimpan.id, int)
    assert tersimpan.actor_user_id == admin.id
    assert tersimpan.actor_role == "admin"
    assert tersimpan.entity_type == "case"
    assert tersimpan.before == {"title": "Lama"}
    assert tersimpan.after == {"title": "Baru"}
    assert tersimpan.reason == "Perbaikan judul"
    assert tersimpan.request_id == request_id


def test_waktu_kejadian_diisi_server(db_session):
    """occurred_at memakai waktu server saat INSERT, bukan nilai kiriman klien."""
    sebelum = datetime.now(UTC) - timedelta(seconds=5)
    db_session.add(_baris())
    db_session.commit()

    waktu = db_session.query(AuditLog).one().occurred_at
    if waktu.tzinfo is None:
        # SQLite tidak menyimpan zona waktu, nilainya UTC. timestamptz menyimpannya.
        waktu = waktu.replace(tzinfo=UTC)

    assert sebelum <= waktu <= datetime.now(UTC) + timedelta(seconds=5)


def test_aktor_kosong_berarti_sistem(db_session):
    """D5b: actor_user_id null menandai kejadian oleh sistem, mis. penugasan otomatis."""
    db_session.add(_baris(action="review.assigned", entity_type=AuditEntityType.REVIEW))
    db_session.commit()

    tersimpan = db_session.query(AuditLog).one()

    assert tersimpan.actor_user_id is None
    assert tersimpan.actor_role is None


def test_nomor_urut_naik(db_session):
    db_session.add_all([_baris(), _baris(action="case.submitted")])
    db_session.commit()

    pertama, kedua = db_session.query(AuditLog).order_by(AuditLog.id).all()

    assert kedua.id > pertama.id


@pytest.mark.parametrize("kolom", ["action", "entity_type", "entity_id"])
def test_kolom_wajib_tidak_boleh_kosong(db_session, kolom):
    db_session.add(_baris(**{kolom: None}))

    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_aktor_harus_pengguna_terdaftar():
    """actor_user_id merujuk users.id. Users tidak pernah dihapus (PBI-1 AC5)."""
    fk = next(iter(Base.metadata.tables["audit_logs"].c.actor_user_id.foreign_keys))

    assert fk.target_fullname == "users.id"


def test_jenis_objek_sesuai_katalog():
    assert {item.value for item in AuditEntityType} == {
        "case",
        "case_version",
        "review",
        "suite",
        "ai_product",
        "user",
    }
