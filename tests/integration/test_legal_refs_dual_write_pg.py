"""SCRUM-160: penyimpanan ganda rujukan hukum di PostgreSQL.

Yang tidak dibuktikan SQLite: urutan insert terhadap foreign key yang benar-benar
dijalankan (legal_refs → case_versions, legal_ref_links → regulation_types),
check constraint tabel SCRUM-159 (penghubung per posisi, teks bebas hanya untuk
kind free_text), enum PostgreSQL, dan seed 266 jenis dari migrasi.

Service kasus dipanggil langsung lewat Session yang menumpang transaksi luar
(join_transaction_mode="create_savepoint"): commit service menjadi savepoint,
dan seluruh baris dibatalkan di akhir test.

Contoh hukum hanya KTK-001 dan contoh AC10 (belum dikonfirmasi tim legal).
"""

import os
import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.modules.cases import service
from app.modules.cases.models import Case, CaseStatus
from app.modules.cases.schemas import CaseWrite
from app.shared import request_context
from tests.legal_refs import rujukan_ac10, rujukan_ktk_001

POSTGRES_URL = os.environ.get("TEST_POSTGRES_URL", "")

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(not POSTGRES_URL, reason="TEST_POSTGRES_URL tidak diisi"),
]

TEKS_BEBAS = {"kind": "free_text", "links": [], "free_text": "Format teks bebas"}


@pytest.fixture
def db():
    engine = create_engine(POSTGRES_URL)
    with engine.connect() as conn:
        transaksi = conn.begin()
        sesi = Session(bind=conn, join_transaction_mode="create_savepoint")
        user_id, suite_id = uuid.uuid4(), uuid.uuid4()
        conn.execute(
            text(
                "INSERT INTO users (id, email, name, role) "
                "VALUES (:id, 'qa-160@veritask.test', 'QA 160', 'author')"
            ),
            {"id": user_id},
        )
        conn.execute(
            text(
                "INSERT INTO suites (id, name, status, created_by) "
                "VALUES (:id, 'Suite QA 160', 'active', :user_id)"
            ),
            {"id": suite_id, "user_id": user_id},
        )
        request_context.bind(sesi, user_id=user_id, role="author")
        try:
            yield sesi, suite_id, user_id
        finally:
            sesi.close()
            transaksi.rollback()
    engine.dispose()


def _badan(*rujukan: dict) -> CaseWrite:
    return CaseWrite.model_validate(
        {
            "case_code": "QA-160",
            "identity": {"title": "PHK sepihak", "question": "Apakah sah?", "category": None},
            "legal_refs": list(rujukan),
            "answer_criteria": {"must_contain": ["surat"], "must_not_contain": []},
            "traps": [],
            "split_tag": "dev",
        }
    )


def _baris(sesi: Session, version_id: uuid.UUID) -> list[tuple]:
    """(order, kind, free_text, position, connector, form, regulation_type, number, year, pasal, angka)."""
    return [
        tuple(baris)
        for baris in sesi.execute(
            text(
                'SELECT r."order", r.kind::text, r.free_text, l.position, l.connector::text, '
                "l.form::text, l.regulation_type, l.number, l.year, l.pasal, l.angka "
                "FROM legal_refs r LEFT JOIN legal_ref_links l ON l.legal_ref_id = r.id "
                'WHERE r.case_version_id = :id ORDER BY r."order", l.position'
            ),
            {"id": version_id},
        )
    ]


KTK_001 = (1, "structured", None, 1, "none", "pasal", "uu", "13", 2003, "156", None)
AC10 = [
    (1, "structured", None, 1, "none", "pasal", "uu", "13", 2003, "156", None),
    (1, "structured", None, 2, "jo", "pasal_angka", "uu", "6", 2023, "II", "5"),
]


def _buat(sesi, suite_id, user_id, *rujukan):
    dibuat = service.create_case(sesi, suite_id, _badan(*rujukan), actor_id=user_id)
    return dibuat, sesi.get(Case, dibuat.id)


def _setujui(sesi: Session, kasus: Case) -> uuid.UUID:
    versi = kasus.current_version
    versi.status = CaseStatus.APPROVED
    kasus.latest_approved_version_id = versi.id
    sesi.commit()
    return versi.id


def test_kasus_baru_tersimpan_di_content_dan_tabel_dengan_constraint_postgres(db):
    # Arrange
    sesi, suite_id, user_id = db

    # Act
    dibuat, kasus = _buat(sesi, suite_id, user_id, rujukan_ac10(), TEKS_BEBAS)

    # Assert
    assert _baris(sesi, kasus.current_version_id) == [
        *AC10,
        (2, "free_text", "Format teks bebas", None, None, None, None, None, None, None, None),
    ]
    assert dibuat.legal_refs[0].display_text == (
        "Pasal 156 UU 13/2003 jo. Pasal II Angka 5 UU 6/2023"
    )


def test_mengubah_kasus_mengganti_baris(db):
    # Arrange
    sesi, suite_id, user_id = db
    _, kasus = _buat(sesi, suite_id, user_id, rujukan_ac10(), TEKS_BEBAS)

    # Act
    service.update_case(sesi, kasus.id, _badan(rujukan_ktk_001()), actor_id=user_id, is_admin=False)

    # Assert
    assert _baris(sesi, kasus.current_version_id) == [KTK_001]


def test_versi_baru_mendapat_baris_sendiri_dan_baris_approved_tetap(db):
    # Arrange
    sesi, suite_id, user_id = db
    _, kasus = _buat(sesi, suite_id, user_id, rujukan_ac10())
    approved_id = _setujui(sesi, kasus)

    # Act
    service.start_new_version(sesi, kasus.id, actor_id=user_id, is_admin=False)
    sesi.refresh(kasus)
    service.update_case(sesi, kasus.id, _badan(rujukan_ktk_001()), actor_id=user_id, is_admin=False)

    # Assert
    assert kasus.current_version_id != approved_id
    assert _baris(sesi, approved_id) == AC10
    assert _baris(sesi, kasus.current_version_id) == [KTK_001]


def test_versi_baru_dari_isi_sprint_1_memakai_jenis_dari_seed(db):
    """Teks jenis "UU" dari Sprint 1 menjadi code uu milik seed 266 jenis di migrasi."""
    # Arrange
    sesi, suite_id, user_id = db
    _, kasus = _buat(sesi, suite_id, user_id, rujukan_ktk_001())
    versi = kasus.current_version
    lama = {"regulation_type": "UU", "regulation_number": "13", "year": 2003, "pasal": "156"}
    versi.content = {**versi.content, "legal_refs": [lama]}
    sesi.commit()
    _setujui(sesi, kasus)

    # Act
    service.start_new_version(sesi, kasus.id, actor_id=user_id, is_admin=False)
    sesi.refresh(kasus)

    # Assert
    assert _baris(sesi, kasus.current_version_id) == [KTK_001]
