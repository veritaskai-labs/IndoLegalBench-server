"""Aturan tabel asal kasus dan impor di SQLite. PBI-4 SCRUM-81 SCRUM-337, diagram D8.

Constraint yang sama ada di migrasi PostgreSQL (f2a7c4e91b38) dan diuji di
tests/integration/test_case_origin_migration_pg.py. Di sini dibuktikan bahwa model ORM, yang
dipakai create_all untuk tes dan bootstrap lokal, menegakkan aturan yang sama.

Label: Positif = boleh disimpan, Negatif = harus ditolak database, Edge = data batas.
"""

import uuid

import pytest
from sqlalchemy.exc import IntegrityError

from app.modules.cases.models import (
    Case,
    CaseOrigin,
    CaseStatus,
    ImportBatch,
    ImportRowResult,
    ImportRowStatus,
)
from app.shared.security import Role
from tests.modules.cases.test_cases import _buat, _suite


@pytest.fixture
def kasus(as_role, db_session) -> Case:
    """Satu kasus draft bertag test yang dibuat Kontributor lewat API."""
    client = as_role(Role.CONTRIBUTOR)
    dibuat = _buat(client, _suite(client)).json()
    return db_session.get(Case, uuid.UUID(dibuat["id"]))


def _batch(kasus: Case, **ubah) -> ImportBatch:
    isi = {
        "file_name": "kasus-lama.csv",
        "imported_by": kasus.created_by,
        "suite_id": kasus.suite_id,
    } | ubah
    return ImportBatch(**isi)


def test_kasus_lama_berasal_dari_input_manual(kasus):
    """Positif: D8, kasus yang dibuat lewat form tercatat manual tanpa batch dan kasus asal."""
    # Assert
    assert (kasus.origin, kasus.import_batch_id, kasus.source_case_id) == (
        CaseOrigin.MANUAL,
        None,
        None,
    )


def test_draft_boleh_tanpa_tag(db_session, kasus):
    """Positif: AC8 dan D8, tag dev/test boleh kosong selama versi masih draft."""
    # Arrange
    kasus.current_version.split_tag = None

    # Act
    db_session.commit()

    # Assert
    db_session.refresh(kasus.current_version)
    assert kasus.current_version.split_tag is None


@pytest.mark.parametrize("status", [CaseStatus.IN_REVIEW, CaseStatus.APPROVED])
def test_versi_yang_lewat_draft_wajib_bertag(db_session, kasus, status):
    """Negatif: D8 CHECK, versi yang sudah diajukan atau disetujui tidak boleh tanpa tag."""
    # Arrange
    kasus.current_version.split_tag = None
    kasus.current_version.status = status

    # Act / Assert
    with pytest.raises(IntegrityError, match="ck_case_versions_split_tag_required_after_draft"):
        db_session.commit()


@pytest.mark.parametrize(
    ("origin", "batch", "asal", "constraint"),
    [
        # Negatif: kasus impor harus menunjuk batch impornya.
        (CaseOrigin.IMPORT, False, False, "ck_cases_import_batch_matches_origin"),
        # Negatif: kasus duplikat harus menunjuk kasus asalnya.
        (CaseOrigin.DUPLICATE, False, False, "ck_cases_source_case_matches_origin"),
        # Negatif: kasus manual tidak boleh menunjuk batch.
        (CaseOrigin.MANUAL, True, False, "ck_cases_import_batch_matches_origin"),
        # Negatif: kasus manual tidak boleh menunjuk kasus asal.
        (CaseOrigin.MANUAL, False, True, "ck_cases_source_case_matches_origin"),
    ],
)
def test_asal_kasus_selalu_berpasangan_dengan_rujukannya(
    db_session, kasus, origin, batch, asal, constraint
):
    """Negatif: D8, asal impor berarti ada batch, asal duplikat berarti ada kasus asal."""
    # Arrange
    if batch:
        baris = _batch(kasus)
        db_session.add(baris)
        db_session.flush()
        kasus.import_batch_id = baris.id
    kasus.source_case_id = kasus.id if asal else None
    kasus.origin = origin

    # Act / Assert
    with pytest.raises(IntegrityError, match=constraint):
        db_session.commit()


def test_kasus_impor_dan_hasil_barisnya_tersimpan(db_session, kasus):
    """Positif: D7, satu batch dengan hasil created, failed, dan skipped."""
    # Arrange
    batch = _batch(kasus, total_rows=3, success_count=1, failed_count=1, skipped_count=1)
    db_session.add(batch)
    db_session.flush()
    kasus.origin, kasus.import_batch_id = CaseOrigin.IMPORT, batch.id

    # Act
    db_session.add_all(
        [
            ImportRowResult(
                batch_id=batch.id, row_no=1, status=ImportRowStatus.CREATED, case_id=kasus.id
            ),
            ImportRowResult(
                batch_id=batch.id,
                row_no=2,
                status=ImportRowStatus.FAILED,
                reason="legal_refs bukan list JSON",
            ),
            ImportRowResult(
                batch_id=batch.id,
                row_no=3,
                status=ImportRowStatus.SKIPPED,
                reason="Kembar dengan kasus yang sudah ada",
                row_fingerprint="a" * 64,
            ),
        ]
    )
    db_session.commit()

    # Assert
    assert db_session.query(ImportRowResult).filter_by(batch_id=batch.id).count() == 3


@pytest.mark.parametrize(
    ("isi", "constraint"),
    [
        # Negatif: baris created wajib menunjuk kasus yang dibuatnya.
        ({"status": ImportRowStatus.CREATED}, "ck_import_row_results_case_only_when_created"),
        # Negatif: baris gagal tidak boleh membuat kasus (AC8).
        (
            {"status": ImportRowStatus.FAILED, "reason": "rusak", "dengan_kasus": True},
            "ck_import_row_results_case_only_when_created",
        ),
        # Negatif: baris gagal wajib punya alasan untuk ringkasan (AC7).
        ({"status": ImportRowStatus.FAILED}, "ck_import_row_results_reason_when_not_created"),
        # Edge: alasan yang hanya spasi dianggap kosong.
        (
            {"status": ImportRowStatus.SKIPPED, "reason": "   "},
            "ck_import_row_results_reason_when_not_created",
        ),
        # Edge: nomor baris dimulai dari 1.
        (
            {"status": ImportRowStatus.FAILED, "reason": "rusak", "row_no": 0},
            "ck_import_row_results_row_no",
        ),
    ],
)
def test_hasil_baris_yang_tidak_konsisten_ditolak(db_session, kasus, isi, constraint):
    """Negatif: D7 dan D8, hasil baris selalu bisa dijelaskan di ringkasan impor."""
    # Arrange
    batch = _batch(kasus)
    db_session.add(batch)
    db_session.flush()
    dengan_kasus = isi.pop("dengan_kasus", False)
    baris = {"batch_id": batch.id, "row_no": 1, "case_id": kasus.id if dengan_kasus else None}
    db_session.add(ImportRowResult(**(baris | isi)))

    # Act / Assert
    with pytest.raises(IntegrityError, match=constraint):
        db_session.commit()


def test_satu_baris_berkas_hanya_dicatat_sekali(db_session, kasus):
    """Negatif: D8 UNIQUE (batch_id, row_no)."""
    # Arrange
    batch = _batch(kasus)
    db_session.add(batch)
    db_session.flush()
    for _ in range(2):
        db_session.add(
            ImportRowResult(
                batch_id=batch.id, row_no=7, status=ImportRowStatus.FAILED, reason="rusak"
            )
        )

    # Act / Assert
    with pytest.raises(IntegrityError, match="UNIQUE"):
        db_session.commit()


@pytest.mark.parametrize(
    ("ubah", "constraint"),
    [
        # Negatif: jumlah tidak boleh negatif.
        ({"failed_count": -1}, "ck_import_batches_counts_not_negative"),
        # Negatif: jumlah hasil tidak boleh melebihi baris berkas.
        ({"total_rows": 1, "success_count": 1, "skipped_count": 1}, "counts_within_total"),
        # Negatif: nama berkas tidak boleh kosong.
        ({"file_name": "  "}, "ck_import_batches_file_name"),
    ],
)
def test_ringkasan_batch_yang_tidak_mungkin_ditolak(db_session, kasus, ubah, constraint):
    """Negatif: D8, ringkasan batch selalu masuk akal."""
    # Arrange
    db_session.add(_batch(kasus, **ubah))

    # Act / Assert
    with pytest.raises(IntegrityError, match=constraint):
        db_session.commit()
