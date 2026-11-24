"""Adapter SQL unduhan skor: sumber data sesi dan penyimpanan berkas. PBI-16 SCRUM-327.

Dijalankan di SQLite. Trigger PostgreSQL yang menjaga berkas tetap selamanya diuji di
tests/integration/test_score_files_pg.py.
"""

import hashlib
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy.exc import IntegrityError

from app.modules.cases.models import CaseStatus, CaseVersion, SplitTag
from app.modules.reports.models import SessionScoreFile
from app.modules.reports.score_file_repository import SqlScoreFileStore, SqlScoreSource
from app.modules.reports.score_files import ScoreFile
from tests.modules.reports.test_session_reader import (  # noqa: F401
    _kasus,
    _nilai,
    _percobaan,
    _sesi,
    admin,
    rubrik,
)

DIARSIPKAN = datetime(2026, 11, 15, tzinfo=UTC)


def _berkas(sesi_id: uuid.UUID, isi: bytes = b"Kasus\r\nPDT-001\r\n") -> ScoreFile:
    return ScoreFile(
        session_id=sesi_id,
        file_name=f"skor-{sesi_id}.csv",
        content=isi,
        sha256=hashlib.sha256(isi).hexdigest(),
    )


def _sesi_dinilai(db, admin, rubrik):  # noqa: F811
    sesi = _sesi(db, admin)
    item = _kasus(db, sesi, "PDT-327")
    run = _nilai(db, sesi, rubrik, admin, [(_percobaan(db, sesi, item), "0.80", "0.60")])
    return sesi, item, run


class TestStore:
    def test_menyimpan_isi_sidik_ukuran_run_dan_pengunduh(self, db_session, admin, rubrik):  # noqa: F811
        sesi, _, run = _sesi_dinilai(db_session, admin, rubrik)
        berkas = _berkas(sesi.id)

        tersimpan = SqlScoreFileStore(db_session).keep(
            berkas, grading_run_id=run.id, created_by=admin.id
        )

        baris = db_session.query(SessionScoreFile).one()
        assert tersimpan == berkas
        assert baris.session_id == sesi.id
        assert baris.content == berkas.content
        assert baris.sha256 == berkas.sha256
        assert baris.size_bytes == len(berkas.content)
        assert baris.file_name == berkas.file_name
        assert baris.grading_run_id == run.id
        assert baris.created_by == admin.id
        assert baris.created_at is not None

    def test_berkas_tetap_ada_setelah_transaksi_berikutnya_dibatalkan(
        self,
        db_session,
        admin,  # noqa: F811
        rubrik,  # noqa: F811
    ):
        sesi, _, run = _sesi_dinilai(db_session, admin, rubrik)

        SqlScoreFileStore(db_session).keep(
            _berkas(sesi.id), grading_run_id=run.id, created_by=admin.id
        )
        db_session.rollback()

        assert db_session.query(SessionScoreFile).count() == 1

    def test_isi_yang_sama_untuk_sesi_yang_sama_tidak_disimpan_dua_kali(
        self,
        db_session,
        admin,  # noqa: F811
        rubrik,  # noqa: F811
    ):
        sesi, _, run = _sesi_dinilai(db_session, admin, rubrik)
        simpanan = SqlScoreFileStore(db_session)

        pertama = simpanan.keep(_berkas(sesi.id), grading_run_id=run.id, created_by=admin.id)
        kedua = simpanan.keep(_berkas(sesi.id), grading_run_id=run.id, created_by=admin.id)

        assert kedua == pertama
        assert db_session.query(SessionScoreFile).count() == 1

    def test_isi_berbeda_menjadi_berkas_baru_dan_yang_lama_tetap_ada(
        self,
        db_session,
        admin,  # noqa: F811
        rubrik,  # noqa: F811
    ):
        sesi, _, run = _sesi_dinilai(db_session, admin, rubrik)
        simpanan = SqlScoreFileStore(db_session)

        simpanan.keep(_berkas(sesi.id, b"lama\r\n"), grading_run_id=run.id, created_by=admin.id)
        simpanan.keep(_berkas(sesi.id, b"baru\r\n"), grading_run_id=run.id, created_by=admin.id)

        isi = {baris.content for baris in db_session.query(SessionScoreFile)}
        assert isi == {b"lama\r\n", b"baru\r\n"}

    def test_unduhan_bersamaan_yang_kalah_membaca_berkas_pemenang(
        self,
        db_session,
        admin,  # noqa: F811
        rubrik,  # noqa: F811
    ):
        # Dua unduhan dengan isi sama lolos cek pertama bersamaan; yang kalah kena unik
        # session_id + sha256, lalu harus mengembalikan berkas pemenang, bukan galat.
        sesi, _, run = _sesi_dinilai(db_session, admin, rubrik)
        pemenang = _berkas(sesi.id)
        SqlScoreFileStore(db_session).keep(pemenang, grading_run_id=run.id, created_by=admin.id)

        class SimpananYangTerlambat(SqlScoreFileStore):
            def __init__(self, db):
                super().__init__(db)
                self._cek_pertama = True

            def _find(self, session_id, sha256):
                if self._cek_pertama:
                    self._cek_pertama = False
                    return None
                return super()._find(session_id, sha256)

        hasil = SimpananYangTerlambat(db_session).keep(
            _berkas(sesi.id), grading_run_id=run.id, created_by=admin.id
        )

        assert hasil == pemenang
        assert db_session.query(SessionScoreFile).count() == 1

    def test_galat_integritas_lain_tidak_disembunyikan(self, db_session, admin, rubrik):  # noqa: F811
        # Berkas kosong melanggar size_bytes > 0, bukan duplikat: galatnya harus naik.
        sesi, _, run = _sesi_dinilai(db_session, admin, rubrik)

        with pytest.raises(IntegrityError):
            SqlScoreFileStore(db_session).keep(
                _berkas(sesi.id, b""), grading_run_id=run.id, created_by=admin.id
            )


class TestSource:
    def test_sesi_yang_tidak_ada_tidak_menghasilkan_apa_pun(self, db_session):
        assert SqlScoreSource(db_session).read(uuid.uuid4()) is None

    def test_membaca_rekaman_yang_sama_dengan_pembaca_laporan(self, db_session, admin, rubrik):  # noqa: F811
        sesi, _, _ = _sesi_dinilai(db_session, admin, rubrik)

        data = SqlScoreSource(db_session).read(sesi.id)

        assert data.record.facts.session_id == sesi.id
        assert [g.ref.case_code for g in data.record.graded_cases] == ["PDT-327"]
        assert data.archived is False

    def test_sesi_arsip_ditandai_arsip(self, db_session, admin, rubrik):  # noqa: F811
        sesi, _, _ = _sesi_dinilai(db_session, admin, rubrik)
        sesi.archived_at = DIARSIPKAN
        sesi.archived_by = admin.id
        db_session.flush()

        assert SqlScoreSource(db_session).read(sesi.id).archived is True

    def test_run_terakhir_yang_selesai_dan_nomor_versi_rubriknya(
        self,
        db_session,
        admin,  # noqa: F811
        rubrik,  # noqa: F811
    ):
        sesi, _, run = _sesi_dinilai(db_session, admin, rubrik)

        data = SqlScoreSource(db_session).read(sesi.id)

        assert data.grading_run_id == run.id
        assert data.rubric_version_no == rubrik.version_no

    def test_sesi_yang_belum_dinilai_tanpa_run_dan_tanpa_rubrik(
        self,
        db_session,
        admin,  # noqa: F811
    ):
        sesi = _sesi(db_session, admin)

        data = SqlScoreSource(db_session).read(sesi.id)

        assert data.grading_run_id is None
        assert data.rubric_version_no is None

    def test_run_yang_belum_selesai_tidak_dipakai(self, db_session, admin, rubrik):  # noqa: F811
        sesi = _sesi(db_session, admin)
        item = _kasus(db_session, sesi, "PDT-327")
        _nilai(
            db_session,
            sesi,
            rubrik,
            admin,
            [(_percobaan(db_session, sesi, item), "0.8", "0.6")],
            selesai=False,
        )

        assert SqlScoreSource(db_session).read(sesi.id).grading_run_id is None

    def test_nomor_versi_kasus_dibaca_dari_case_versions(self, db_session, admin, rubrik):  # noqa: F811
        sesi, item, _ = _sesi_dinilai(db_session, admin, rubrik)
        db_session.add(
            CaseVersion(
                id=item.case_version_id,
                case_id=item.case_id,
                version_no=4,
                status=CaseStatus.APPROVED,
                case_code="PDT-327",
                split_tag=SplitTag.TEST,
                content={},
                created_by=admin.id,
            )
        )
        db_session.flush()

        data = SqlScoreSource(db_session).read(sesi.id)

        assert data.case_version_nos == {item.case_version_id: 4}
