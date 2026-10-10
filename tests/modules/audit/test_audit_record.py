"""Test pencatat audit, audit.service.record().

PBI-18 AC1, mengikuti diagram D6b: record() memakai session yang sama
dengan perubahannya dan tidak pernah commit sendiri. Jadi perubahan dan
catatannya tersimpan bersama, atau batal bersama.
"""

import uuid

import pytest
from sqlalchemy.exc import IntegrityError

from app.modules.audit import service
from app.modules.audit.models import AuditEntityType, AuditLog
from app.modules.auth.models import User
from app.shared import request_context
from app.shared.exceptions import ValidationError
from app.shared.security import Role

SUITE_ID = uuid.UUID("00000000-0000-0000-0000-0000000000a1")


def _catat(db, **kolom):
    nilai = {
        "action": "suite.updated",
        "entity_type": AuditEntityType.SUITE,
        "entity_id": SUITE_ID,
    }
    nilai.update(kolom)
    return service.record(db, **nilai)


def _pengguna(db, role: Role = Role.ADMIN) -> User:
    pengguna = User(email=f"{role}@veritask.test", name=f"QA {role}", role=role)
    db.add(pengguna)
    db.commit()
    return pengguna


def test_record_tidak_commit_sendiri(db_session):
    _catat(db_session)

    db_session.rollback()

    assert db_session.query(AuditLog).count() == 0


def test_record_ikut_tersimpan_saat_commit(db_session):
    _catat(db_session, before={"name": "Lama"}, after={"name": "Baru"})
    db_session.commit()

    baris = db_session.query(AuditLog).one()

    assert baris.action == "suite.updated"
    assert baris.entity_type == "suite"
    assert baris.entity_id == SUITE_ID
    assert baris.before == {"name": "Lama"}
    assert baris.after == {"name": "Baru"}


def test_perubahan_gagal_maka_log_ikut_batal(db_session):
    """D6b: tidak ada log tanpa perubahan."""
    _catat(db_session)
    db_session.add(User(email=None, name="Tanpa email", role=Role.AUTHOR))

    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()

    assert db_session.query(AuditLog).count() == 0


def test_tanpa_aktor_tercatat_sebagai_sistem(db_session):
    _catat(db_session)
    db_session.commit()

    baris = db_session.query(AuditLog).one()

    assert baris.actor_user_id is None
    assert baris.actor_role is None


def test_aktor_diambil_dari_session(db_session):
    admin = _pengguna(db_session)
    request_id = uuid.uuid4()
    request_context.bind(db_session, user_id=admin.id, role=Role.ADMIN, request_id=request_id)

    _catat(db_session)
    db_session.commit()

    baris = db_session.query(AuditLog).filter_by(action="suite.updated").one()
    assert baris.actor_user_id == admin.id
    assert baris.actor_role == "admin"
    assert baris.request_id == request_id


def test_case_id_dan_reason_tersimpan(db_session):
    case_id = uuid.uuid4()

    _catat(
        db_session,
        action="case.tag_changed",
        entity_type=AuditEntityType.CASE,
        entity_id=case_id,
        case_id=case_id,
        reason="Dipindah ke set uji",
    )
    db_session.commit()

    baris = db_session.query(AuditLog).one()
    assert baris.case_id == case_id
    assert baris.reason == "Dipindah ke set uji"


def test_kredensial_tidak_pernah_sampai_ke_database(db_session):
    """PBI-10 AC2, ujung ke ujung. Rincian aturan penyaring ada di test_audit_redaction.py."""
    _catat(
        db_session,
        entity_type=AuditEntityType.AI_PRODUCT,
        before={"name": "GPT", "credential_encrypted": "sk-lama"},
        after={"name": "GPT", "credential_encrypted": "sk-baru"},
    )
    db_session.commit()

    baris = db_session.query(AuditLog).one()
    assert (baris.before, baris.after) == ({"name": "GPT"}, {"name": "GPT"})


def test_penanda_kredensial_diganti_tetap_tercatat(db_session):
    """D6a: ai_product.credential_rotated cukup mencatat after = {"credential": "rotated"}."""
    _catat(
        db_session,
        action="ai_product.credential_rotated",
        entity_type=AuditEntityType.AI_PRODUCT,
        after=service.CREDENTIAL_ROTATED,
    )
    db_session.commit()

    assert db_session.query(AuditLog).one().after == {"credential": "rotated"}


def test_ditolak_validasi_maka_tidak_ada_baris(db_session):
    """Validasi lengkap ada di test_audit_service_unit.py. Di sini cukup buktinya di DB."""
    with pytest.raises(ValidationError):
        _catat(db_session, action="review.reviewer_replaced", entity_type=AuditEntityType.REVIEW)
    db_session.commit()

    assert db_session.query(AuditLog).count() == 0


def test_record_mengembalikan_baris_yang_ditambahkan(db_session):
    baris = _catat(db_session)

    assert baris in db_session.new


def test_pengguna_palsu_di_test_ikut_terikat(as_role, db_session):
    """Fixture as_role meniru get_current_user, termasuk pengikatan pelakunya."""
    client = as_role(Role.AUTHOR, user_id=SUITE_ID)

    client.get("/suites")

    assert request_context.actor(db_session) == request_context.Actor(SUITE_ID, "author")
