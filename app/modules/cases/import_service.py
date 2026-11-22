"""Impor kasus lama lewat CSV. PBI-4 SCRUM-81 SCRUM-338, AC4-AC11, diagram D7 dan D8.

Alur D7:
1. Berkas diperiksa utuh (csv_import.read_file). Gagal = tolak semua, tidak ada yang disimpan.
2. Batch dan satu suite sementara dibuat (keputusan SA: semua kasus satu berkas masuk satu suite).
3. Setiap baris diperiksa (csv_import.parse_row), lalu dicek kembar: sidik jari judul + pertanyaan
   sama dengan kasus yang sudah ada, atau dengan baris sebelumnya yang sudah masuk di berkas ini.
4. Baris valid menjadi kasus: kode dari sistem, draft versi 1, origin import, penulis pengimpor,
   tag boleh kosong. Setiap baris disimpan dalam commit sendiri bersama hasil barisnya, jadi baris
   valid tetap tersimpan walau baris lain gagal (tidak ada rollback satu berkas).
5. Jumlah berhasil, gagal, dan dilewati ditambah di batch dalam commit yang sama dengan hasil
   barisnya, jadi batch tetap benar walau impor terhenti di tengah (review PR #157).
6. Suite sementara yang tidak mendapat satu kasus pun diarsipkan, supaya tidak menumpuk suite
   kosong yang aktif (review PR #157).

Kasus hasil impor berstatus draft dengan created_by pengimpor, jadi alur review yang sudah ada
(dua reviewer, penulis tidak boleh mereview kasusnya sendiri) berlaku tanpa perubahan (AC11).
"""

import uuid
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.cases import csv_import, validation
from app.modules.cases.csv_import import ImportedCase, RowFailure
from app.modules.cases.import_schemas import ImportRowRead, ImportSummaryRead
from app.modules.cases.models import (
    Case,
    CaseOrigin,
    CaseStatus,
    CaseVersion,
    ImportBatch,
    ImportRowResult,
    ImportRowStatus,
)
from app.modules.cases.service import free_case_code
from app.modules.legal_refs import service as legal_refs_service
from app.modules.suites.models import Suite, SuiteStatus
from app.shared.exceptions import NotFoundError

IMPORT_BATCH_NOT_FOUND = "IMPORT_BATCH_NOT_FOUND"

# Sama dengan service kasus: pesan IntegrityError yang berarti case_code sudah dipakai.
_BENTROK_KODE = ("uq_cases_case_code", "cases.case_code")
_ALASAN_BENTROK = (
    "Kode kasus bentrok dengan penyimpanan lain. Impor ulang berkas ini; baris yang sudah "
    "masuk akan dilewati."
)


def upload_limit_bytes() -> int:
    """Batas unggahan untuk router; fungsi supaya tes bisa memakai batas kecil."""
    return csv_import.MAX_FILE_BYTES


def import_cases(
    db: Session, *, file_name: str, data: bytes, actor_id: uuid.UUID
) -> ImportSummaryRead:
    """Jalankan satu impor (D7) dan kembalikan ringkasannya (AC7).

    Raises csv_import.ImportFileError bila berkas ditolak utuh; saat itu belum ada yang ditulis.
    """
    baris = csv_import.read_file(file_name, data)
    katalog = legal_refs_service.load_catalog(db)
    tahun = validation.current_year_wib()
    batch = _buat_batch(db, file_name, actor_id, total=len(baris))
    di_berkas: dict[str, int] = {}
    for mentah in baris:
        hasil = csv_import.parse_row(mentah, regulation_types=katalog.active, current_year=tahun)
        if isinstance(hasil, RowFailure):
            _catat(db, batch.id, hasil.row_no, ImportRowStatus.FAILED, reason=hasil.reason)
            continue
        kembar = _alasan_kembar(db, hasil, di_berkas)
        if kembar is not None:
            _catat(db, batch.id, hasil.row_no, ImportRowStatus.SKIPPED, kembar, hasil.fingerprint)
            continue
        try:
            _simpan_kasus(db, batch, hasil, actor_id)
        except IntegrityError as galat:
            db.rollback()
            if not any(tanda in str(galat.orig) for tanda in _BENTROK_KODE):
                raise
            _catat(
                db,
                batch.id,
                hasil.row_no,
                ImportRowStatus.FAILED,
                _ALASAN_BENTROK,
                hasil.fingerprint,
            )
            continue
        if hasil.fingerprint is not None:
            di_berkas.setdefault(hasil.fingerprint, hasil.row_no)
    _arsipkan_suite_kosong(db, batch.id)
    return read_summary(db, batch.id)


def read_summary(db: Session, batch_id: uuid.UUID) -> ImportSummaryRead:
    """Ringkasan satu batch dari tabel D8, sama dengan yang dikembalikan saat impor."""
    batch = db.get(ImportBatch, batch_id)
    if batch is None:
        raise NotFoundError("Batch impor tidak ditemukan", code=IMPORT_BATCH_NOT_FOUND)
    suite = db.get(Suite, batch.suite_id)
    hasil = db.execute(
        select(ImportRowResult, Case.case_code)
        .outerjoin(Case, Case.id == ImportRowResult.case_id)
        .where(ImportRowResult.batch_id == batch_id)
        .order_by(ImportRowResult.row_no)
    ).all()
    return ImportSummaryRead(
        batch_id=batch.id,
        file_name=batch.file_name,
        imported_by=batch.imported_by,
        imported_at=batch.imported_at,
        suite_id=batch.suite_id,
        suite_name=suite.name if suite is not None else "",
        total_rows=batch.total_rows,
        success_count=batch.success_count,
        failed_count=batch.failed_count,
        skipped_count=batch.skipped_count,
        rows=[
            ImportRowRead(
                row_no=baris.row_no,
                status=baris.status,
                case_id=baris.case_id,
                case_code=kode,
                reason=baris.reason,
            )
            for baris, kode in hasil
        ],
    )


def _buat_batch(db: Session, file_name: str, actor_id: uuid.UUID, *, total: int) -> ImportBatch:
    """Batch dan suite sementaranya (keputusan SA), disimpan sebelum baris pertama."""
    batch_id = uuid.uuid4()
    waktu = datetime.now(validation.WIB).strftime("%Y-%m-%d %H:%M")
    nama_berkas = file_name.strip()[:255]
    suite = Suite(
        id=uuid.uuid4(),
        name=f"Impor CSV {waktu} WIB ({batch_id.hex[:8]})",
        description=f"Suite sementara untuk kasus hasil impor berkas {nama_berkas}.",
        status=SuiteStatus.ACTIVE,
        created_by=actor_id,
    )
    db.add(suite)
    db.flush()
    batch = ImportBatch(
        id=batch_id,
        file_name=nama_berkas,
        imported_by=actor_id,
        suite_id=suite.id,
        total_rows=total,
    )
    db.add(batch)
    db.commit()
    return batch


def _alasan_kembar(db: Session, kasus: ImportedCase, di_berkas: dict[str, int]) -> str | None:
    """Keputusan SA (D7): kembar bila sidik jarinya sama dengan baris lain yang sudah masuk di
    berkas ini, dengan kasus yang sudah ada, atau dengan baris impor sebelumnya yang menjadi kasus.
    Yang terakhir menjaga AC11 walau kasus hasil impor itu sudah disunting, karena sidik jari di
    cases ikut berubah saat judul atau pertanyaannya diubah (review PR #157). Tanpa judul dan
    pertanyaan, tidak pernah kembar."""
    if kasus.fingerprint is None:
        return None
    if kasus.fingerprint in di_berkas:
        return f"Kembar dengan baris {di_berkas[kasus.fingerprint]} di berkas ini."
    kode = db.scalars(
        select(Case.case_code)
        .where(Case.content_fingerprint == kasus.fingerprint)
        .order_by(Case.case_code)
        .limit(1)
    ).first()
    if kode is not None:
        return f"Kembar dengan kasus {kode} yang sudah ada."
    sebelumnya = db.execute(
        select(ImportRowResult.row_no, Case.case_code)
        .join(Case, Case.id == ImportRowResult.case_id)
        .where(
            ImportRowResult.row_fingerprint == kasus.fingerprint,
            ImportRowResult.status == ImportRowStatus.CREATED,
        )
        .order_by(Case.case_code)
        .limit(1)
    ).first()
    if sebelumnya is not None:
        return (
            f"Kembar dengan baris {sebelumnya.row_no} dari impor sebelumnya "
            f"(kasus {sebelumnya.case_code})."
        )
    return None


def _simpan_kasus(
    db: Session, batch: ImportBatch, kasus: ImportedCase, actor_id: uuid.UUID
) -> Case:
    """Satu baris valid menjadi kasus draft, disimpan bersama hasil barisnya dalam satu commit."""
    kode = free_case_code(db, f"IMPOR-{batch.id.hex[:8].upper()}-{kasus.row_no:03d}")
    payload = kasus.as_payload(kode)
    case_id, version_id = uuid.uuid4(), uuid.uuid4()
    case = Case(
        id=case_id,
        suite_id=batch.suite_id,
        case_code=kode,
        current_version_id=version_id,
        latest_approved_version_id=None,
        origin=CaseOrigin.IMPORT,
        import_batch_id=batch.id,
        content_fingerprint=kasus.fingerprint,
        created_by=actor_id,
        updated_by=actor_id,
    )
    version = CaseVersion(
        id=version_id,
        case_id=case_id,
        version_no=1,
        status=CaseStatus.DRAFT,
        case_code=kode,
        split_tag=kasus.split_tag,
        content={
            "title": kasus.title,
            "question": kasus.question,
            "category": kasus.category,
            "legal_refs": kasus.legal_refs,
            "answer_criteria": payload["answer_criteria"],
            "completeness": validation.completeness(payload),
        },
        created_by=actor_id,
        based_on_version_id=None,
    )
    db.add(case)
    db.add(version)
    legal_refs_service.replace_for_version(db, version_id, kasus.legal_refs)
    db.add(
        ImportRowResult(
            batch_id=batch.id,
            row_no=kasus.row_no,
            status=ImportRowStatus.CREATED,
            case_id=case_id,
            row_fingerprint=kasus.fingerprint,
        )
    )
    _hitung(db, batch.id, ImportRowStatus.CREATED)
    db.commit()
    return case


def _catat(
    db: Session,
    batch_id: uuid.UUID,
    row_no: int,
    status: ImportRowStatus,
    reason: str,
    fingerprint: str | None = None,
) -> None:
    db.add(
        ImportRowResult(
            batch_id=batch_id,
            row_no=row_no,
            status=status,
            reason=reason,
            row_fingerprint=fingerprint,
        )
    )
    _hitung(db, batch_id, status)
    db.commit()


_KOLOM_JUMLAH = {
    ImportRowStatus.CREATED: ImportBatch.success_count,
    ImportRowStatus.FAILED: ImportBatch.failed_count,
    ImportRowStatus.SKIPPED: ImportBatch.skipped_count,
}


def _hitung(db: Session, batch_id: uuid.UUID, status: ImportRowStatus) -> None:
    """Tambah jumlah batch untuk satu hasil baris, di transaksi yang sama dengan barisnya (D8)."""
    kolom = _KOLOM_JUMLAH[status]
    db.execute(update(ImportBatch).where(ImportBatch.id == batch_id).values({kolom: kolom + 1}))


def _arsipkan_suite_kosong(db: Session, batch_id: uuid.UUID) -> None:
    """Suite sementara tanpa satu kasus pun diarsipkan; batch dan ringkasannya tetap ada."""
    batch = db.get(ImportBatch, batch_id)
    db.refresh(batch)
    if batch.success_count == 0:
        db.get(Suite, batch.suite_id).status = SuiteStatus.ARCHIVED
        db.commit()
