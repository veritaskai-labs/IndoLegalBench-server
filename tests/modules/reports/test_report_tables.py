"""Aturan kolom tabel laporan D1 di SQLite. PBI-15 SCRUM-79 SCRUM-300.

Yang diuji: CHECK dan UNIQUE yang dijalankan SQLite juga (PostgreSQL menjalankan yang
sama dari migrasi b6f3a2d81c47). Trigger beku, FK, dan "kasus milik versinya" hanya ada
di PostgreSQL: tests/integration/test_report_tables_pg.py.

Isolasi: fixture db_session (tests/conftest.py) membuat SQLite in-memory baru per tes,
jadi baris yang ditolak tidak bocor ke tes lain. Datanya sintetis (tests/reports_factory.py).

Setiap tes menyebut nama constraint yang harus menolak, supaya penolakan karena hal lain
(misalnya kolom wajib kosong) tidak terhitung lulus.

Label: Positif = diterima, Negatif = ditolak, Edge = batas aturan.
"""

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.modules.cases.models import SplitTag
from app.modules.reports.models import (
    MeasurementFilter,
    MeasurementKind,
    ProductLabelMode,
    PublishedReport,
    Report,
    ReportCaseBasis,
    ReportLanguage,
    ReportResult,
    ReportSession,
    ReportStatus,
    ScoreScope,
    ScoreSplit,
)
from app.shared.database import Base
from tests.reports_factory import (
    buat_admin,
    buat_rubrik,
    buat_sesi,
    dasar,
    hasil,
    laporan,
    sesi_laporan,
    terbit,
)


@pytest.fixture
def siap(db_session):
    admin = buat_admin(db_session)
    rubrik = buat_rubrik(db_session, admin)
    sesi_a = buat_sesi(db_session, admin, "Produk A 300")
    sesi_b = buat_sesi(db_session, admin, "Produk B 300")
    return db_session, admin, rubrik, sesi_a, sesi_b


def _simpan(db, *baris) -> None:
    db.add_all(baris)
    db.flush()


def _ditolak(db, potongan_pesan: str, *baris) -> None:
    """Flush harus ditolak database karena constraint yang namanya disebut."""
    db.add_all(baris)
    with pytest.raises(IntegrityError) as info:
        db.flush()
    db.rollback()
    assert potongan_pesan in str(info.value.orig)


# --- Enum: nilainya sama dengan D1 dan schemas di server#145 --------------------------


def test_nilai_enum_sesuai_d1():
    """Positif: nilai enum persis D1, karena SCRUM-320 dan klien membacanya apa adanya."""
    assert [m.value for m in ScoreScope] == ["test", "dev", "dev_test"]
    assert [m.value for m in ScoreSplit] == ["test", "dev", "combined"]
    assert [m.value for m in MeasurementFilter] == ["automatic", "manual", "all"]
    assert [m.value for m in MeasurementKind] == ["automatic", "manual"]
    assert [m.value for m in ProductLabelMode] == ["real_name", "code"]
    assert [m.value for m in ReportStatus] == ["saved", "published", "superseded"]
    assert [m.value for m in ReportLanguage] == ["en", "id"]


# --- reports -----------------------------------------------------------------------


def test_laporan_lengkap_tersimpan_dengan_bawaan_saved_dan_en(siap):
    """Positif: laporan test tersimpan; status bawaan saved, bahasa bawaan en."""
    db, admin, rubrik, sesi_a, sesi_b = siap
    report = laporan(admin, rubrik)

    _simpan(db, report)

    tersimpan = db.get(Report, report.id)
    assert (tersimpan.status, tersimpan.language) == (ReportStatus.SAVED, ReportLanguage.EN)
    assert tersimpan.created_at is not None


@pytest.mark.parametrize(
    ("scope", "has_dev"),
    [
        (ScoreScope.TEST, True),
        (ScoreScope.DEV, False),
        (ScoreScope.DEV_TEST, False),
    ],
)
def test_penanda_skor_dev_harus_sesuai_jenis_nilai(siap, scope, has_dev):
    """Negatif: has_dev_scores tidak boleh berbohong tentang score_scope (AC7, AC14)."""
    db, admin, rubrik, *_ = siap

    _ditolak(
        db, "ck_reports_dev_flag", laporan(admin, rubrik, score_scope=scope, has_dev_scores=has_dev)
    )


@pytest.mark.parametrize("scope", [ScoreScope.DEV, ScoreScope.DEV_TEST])
def test_laporan_dev_tersimpan_dengan_penanda_dev(siap, scope):
    """Positif: laporan dev untuk Admin dan Dev AI tetap boleh disimpan (AC12)."""
    db, admin, rubrik, *_ = siap

    _simpan(db, laporan(admin, rubrik, score_scope=scope, has_dev_scores=True))


def test_laporan_tanpa_kasus_bersama_tidak_disimpan(siap):
    """Edge: irisan kosong hanya memberi peringatan, tidak pernah tersimpan (AC1, D2)."""
    db, admin, rubrik, *_ = siap

    _ditolak(db, "ck_reports_case_basis_count", laporan(admin, rubrik, case_basis_count=0))


@pytest.mark.parametrize("kolom", ["title", "judge_model_name"])
def test_teks_wajib_tidak_boleh_kosong_tak_terlihat(siap, kolom):
    """Edge: spasi dan zero-width space saja dianggap kosong."""
    db, admin, rubrik, *_ = siap

    _ditolak(db, f"ck_reports_{kolom}_not_blank", laporan(admin, rubrik, **{kolom: " ​ "}))


# --- report_sessions ---------------------------------------------------------------


def test_dua_produk_dalam_satu_laporan(siap):
    """Positif: dua sesi dari dua produk berbeda, label dan penanda ikut beku (AC9, AC3)."""
    db, admin, rubrik, sesi_a, sesi_b = siap
    report = laporan(admin, rubrik)
    penanda = [{"code": "trial", "detail": None}]

    _simpan(
        db,
        report,
        sesi_laporan(report, sesi_a, is_trial=True, markers=penanda),
        sesi_laporan(
            report, sesi_b, product_label="Produk B", measurement_kind=MeasurementKind.AUTOMATIC
        ),
    )

    baris = db.scalars(select(ReportSession).where(ReportSession.report_id == report.id)).all()
    assert {(b.product_label, b.is_trial) for b in baris} == {
        ("Produk A", True),
        ("Produk B", False),
    }
    assert next(b for b in baris if b.is_trial).markers == penanda


def test_satu_produk_satu_sesi_per_laporan(siap):
    """Negatif: produk yang sama tidak boleh diwakili dua sesi (D1 keputusan SA)."""
    db, admin, rubrik, sesi_a, sesi_b = siap
    report = laporan(admin, rubrik)
    _simpan(db, report, sesi_laporan(report, sesi_a))

    _ditolak(
        db,
        "report_sessions.report_id, report_sessions.product_id",
        sesi_laporan(report, sesi_b, product_id=sesi_a.product_id),
    )


def test_label_produk_tidak_boleh_kosong(siap):
    """Edge: label nama asli atau kode wajib terisi (AC9)."""
    db, admin, rubrik, sesi_a, _ = siap
    report = laporan(admin, rubrik)
    _simpan(db, report)

    _ditolak(
        db,
        "ck_report_sessions_product_label_not_blank",
        sesi_laporan(report, sesi_a, product_label="  "),
    )


def test_satu_produk_saja_boleh_disimpan(siap):
    """Edge: database tidak membatasi jumlah produk; aturan builder milik SCRUM-301/313."""
    db, admin, rubrik, sesi_a, _ = siap
    report = laporan(admin, rubrik)

    _simpan(db, report, sesi_laporan(report, sesi_a), hasil(report, sesi_a))


# --- report_results ----------------------------------------------------------------


@pytest.fixture
def satu_sesi(siap):
    db, admin, rubrik, sesi_a, sesi_b = siap
    report = laporan(admin, rubrik)
    _simpan(
        db,
        report,
        sesi_laporan(report, sesi_a),
        sesi_laporan(report, sesi_b, product_label="Produk B"),
    )
    return db, report, sesi_a, sesi_b


def test_produk_manual_tanpa_tingkat_kegagalan(satu_sesi):
    """Edge: tingkat kegagalan tidak berlaku untuk manual, jadi NULL (AC2)."""
    db, report, sesi_a, _ = satu_sesi

    _simpan(db, hasil(report, sesi_a, failure_rate=None))


@pytest.mark.parametrize(
    ("ubah", "constraint"),
    [
        ({"det_score": Decimal("1.01")}, "ck_report_results_scores"),
        ({"llm_score": Decimal("-0.01")}, "ck_report_results_scores"),
        ({"total_score": Decimal("1.50")}, "ck_report_results_scores"),
        ({"failure_rate": Decimal("100.01")}, "ck_report_results_failure_rate"),
        ({"failure_rate": Decimal("-1")}, "ck_report_results_failure_rate"),
        ({"rank": 0}, "ck_report_results_rank"),
        ({"cases_basis_count": 4, "cases_graded_count": 3}, "ck_report_results_counts"),
        ({"cases_graded_count": -1, "cases_basis_count": -1}, "ck_report_results_counts"),
        ({"repetitions": 0}, "ck_report_results_repetitions"),
        ({"repetitions": 6}, "ck_report_results_repetitions"),
        ({"total_score": None}, "ck_report_results_rank_total"),
        ({"rank": None}, "ck_report_results_rank_total"),
    ],
)
def test_angka_di_luar_batas_ditolak(satu_sesi, ubah, constraint):
    """Negatif: skor 0..1 seperti grading, kegagalan 0..100, dasar ≤ dinilai, peringkat ≥ 1."""
    db, report, sesi_a, _ = satu_sesi

    _ditolak(db, constraint, hasil(report, sesi_a, **ubah))


@pytest.mark.parametrize(
    "ubah",
    [
        {"det_score": Decimal("0"), "llm_score": Decimal("1"), "failure_rate": Decimal("0")},
        {"failure_rate": Decimal("100"), "cases_basis_count": 3, "cases_graded_count": 3},
        {"det_score": None, "total_score": None, "rank": None, "repetitions": 5},
    ],
)
def test_angka_tepat_di_batas_diterima(satu_sesi, ubah):
    """Edge: 0, 1, 100, dasar = dinilai, skor kosong tanpa peringkat, 5 percobaan."""
    db, report, sesi_a, _ = satu_sesi

    _simpan(db, hasil(report, sesi_a, **ubah))


def test_satu_baris_per_sesi_per_jenis_skor(satu_sesi):
    """Negatif: hasil test sesi yang sama dua kali ditolak."""
    db, report, sesi_a, _ = satu_sesi
    _simpan(db, hasil(report, sesi_a))

    _ditolak(
        db,
        "report_results.report_id, report_results.session_id, report_results.score_split",
        hasil(report, sesi_a, rank=2),
    )


def test_peringkat_unik_per_jenis_skor(satu_sesi):
    """Negatif: dua produk tidak boleh berbagi peringkat 1; seri sudah diputus SCRUM-313."""
    db, report, sesi_a, sesi_b = satu_sesi
    _simpan(db, hasil(report, sesi_a, rank=1))

    _ditolak(
        db,
        "report_results.report_id, report_results.score_split, report_results.rank",
        hasil(report, sesi_b, rank=1),
    )


def test_peringkat_sama_di_jenis_skor_lain_boleh(satu_sesi):
    """Edge: Dev + Test menyimpan peringkat per baris test, dev, dan combined sendiri."""
    db, report, sesi_a, _ = satu_sesi

    _simpan(
        db, hasil(report, sesi_a, rank=1), hasil(report, sesi_a, score_split=ScoreSplit.DEV, rank=1)
    )


def test_hasil_menunjuk_sesi_di_laporannya_sendiri():
    """Positif: FK gabungan report_results → report_sessions (sesi di luar laporan ditolak PG)."""
    fk = next(
        fk
        for fk in Base.metadata.tables["report_results"].foreign_key_constraints
        if fk.name == "fk_report_results_report_session"
    )

    assert [c.name for c in fk.columns] == ["report_id", "session_id"]
    assert [e.target_fullname for e in fk.elements] == [
        "report_sessions.report_id",
        "report_sessions.session_id",
    ]


# --- report_case_basis -------------------------------------------------------------


def test_kasus_dasar_satu_kali_per_laporan(siap):
    """Negatif: reference id yang sama dua kali dalam satu laporan ditolak (PK)."""
    db, admin, rubrik, *_ = siap
    report = laporan(admin, rubrik)
    kasus = dasar(report)
    _simpan(db, report, kasus)
    # Lepas dari identity map supaya penolakannya datang dari database, bukan dari ORM.
    db.expunge(kasus)

    _ditolak(
        db,
        "report_case_basis.report_id, report_case_basis.case_id",
        dasar(report, case_id=kasus.case_id),
    )


def test_kasus_sama_boleh_di_laporan_lain(siap):
    """Edge: kasus yang sama menjadi dasar banyak laporan (PBI-16 menelusuri per laporan)."""
    db, admin, rubrik, *_ = siap
    pertama, kedua = laporan(admin, rubrik), laporan(admin, rubrik)
    kasus = dasar(pertama)

    _simpan(db, pertama, kedua, kasus, dasar(kedua, case_id=kasus.case_id, split_tag=SplitTag.TEST))


# --- published_reports -------------------------------------------------------------


@pytest.fixture
def dua_laporan(siap):
    db, admin, rubrik, *_ = siap
    lama, baru = laporan(admin, rubrik), laporan(admin, rubrik, title="Laporan Oktober (perbaikan)")
    _simpan(db, lama, baru)
    return db, admin, rubrik, lama, baru


def test_terbit_versi_pertama_lalu_diganti_versi_kedua(dua_laporan):
    """Positif: v2 menunjuk v1 lewat supersedes_id dan v1 tetap tersimpan (PBI-17 AC9)."""
    db, admin, rubrik, lama, baru = dua_laporan
    v1 = terbit(lama, admin)
    _simpan(db, v1)

    _simpan(db, terbit(baru, admin, version_no=2, supersedes_id=v1.id))

    assert (
        db.scalar(select(PublishedReport).where(PublishedReport.report_id == lama.id)) is not None
    )


def test_laporan_yang_sama_tidak_bisa_terbit_dua_kali(dua_laporan):
    """Negatif: tekan Terbitkan dua kali untuk laporan yang sama ditolak."""
    db, admin, _, lama, _ = dua_laporan
    _simpan(db, terbit(lama, admin))

    _ditolak(db, "published_reports.report_id", terbit(lama, admin))


def test_satu_versi_hanya_diganti_sekali(dua_laporan):
    """Negatif: dua versi baru yang mengganti v1 yang sama (cabang) ditolak."""
    db, admin, rubrik, lama, baru = dua_laporan
    ketiga = laporan(admin, rubrik)
    v1 = terbit(lama, admin)
    _simpan(db, ketiga, v1, terbit(baru, admin, version_no=2, supersedes_id=v1.id))

    _ditolak(
        db,
        "published_reports.supersedes_id",
        terbit(ketiga, admin, version_no=2, supersedes_id=v1.id),
    )


@pytest.mark.parametrize(
    ("version_no", "pakai_supersedes", "constraint"),
    [
        (0, False, "ck_published_reports_version_no"),
        (2, False, "ck_published_reports_chain"),
        (1, True, "ck_published_reports_chain"),
    ],
)
def test_nomor_versi_mengikuti_rantai(dua_laporan, version_no, pakai_supersedes, constraint):
    """Negatif: v1 tanpa pendahulu, v2 dan seterusnya wajib menunjuk versi lama."""
    db, admin, rubrik, lama, baru = dua_laporan
    v1 = terbit(lama, admin)
    _simpan(db, v1)

    _ditolak(
        db,
        constraint,
        terbit(
            baru, admin, version_no=version_no, supersedes_id=v1.id if pakai_supersedes else None
        ),
    )


def test_versi_tidak_boleh_mengganti_dirinya_sendiri(dua_laporan):
    """Edge: supersedes_id = id sendiri ditolak."""
    db, admin, _, lama, _ = dua_laporan
    sendiri = uuid.uuid4()

    _ditolak(
        db,
        "ck_published_reports_not_self",
        terbit(lama, admin, id=sendiri, version_no=2, supersedes_id=sendiri),
    )


def test_kolom_laporan_tidak_punya_kolom_target_skor():
    """Negatif: tidak ada target skor atau nilai acuan (AC6, D1 aturan 4)."""
    kolom = {c.name for t in ("reports", "report_results") for c in Base.metadata.tables[t].c}

    assert not {k for k in kolom if "target" in k or "benchmark" in k}


def test_hasil_tanpa_penanda_menyimpan_daftar_kosong(siap):
    """Edge: sesi tanpa penanda menyimpan [] (bukan NULL), supaya pembaca tidak perlu cek None."""
    db, admin, rubrik, sesi_a, _ = siap
    report = laporan(admin, rubrik)
    baris = ReportSession(
        report_id=report.id,
        session_id=sesi_a.id,
        product_id=sesi_a.product_id,
        product_label="Produk A",
        measurement_kind=MeasurementKind.MANUAL,
        is_trial=False,
    )

    _simpan(db, report, baris)

    assert db.get(ReportSession, (report.id, sesi_a.id)).markers == []


def test_hasil_dan_dasar_tersimpan_bersama(siap):
    """Positif: satu laporan utuh: dua sesi, dua hasil, dua kasus dasar."""
    db, admin, rubrik, sesi_a, sesi_b = siap
    report = laporan(admin, rubrik)

    _simpan(
        db,
        report,
        sesi_laporan(report, sesi_a),
        sesi_laporan(report, sesi_b, product_label="Produk B"),
        hasil(report, sesi_a, rank=1),
        hasil(report, sesi_b, rank=2, total_score=Decimal("0.40")),
        dasar(report),
        dasar(report),
    )

    assert len(db.scalars(select(ReportResult)).all()) == 2
    assert len(db.scalars(select(ReportCaseBasis)).all()) == 2
