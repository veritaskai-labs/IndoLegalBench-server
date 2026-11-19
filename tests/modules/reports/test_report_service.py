"""Aturan service laporan SCRUM-300: jenis skor per jenis nilai, penjaga terbit, simpan beku.

PBI-15 AC7, AC10, AC14; PBI-17 AC9. Jira SCRUM-300: "Laporan yang memuat skor dev tidak
boleh berstatus diterbitkan, dijaga di service. Tidak ada endpoint hapus laporan."

Isolasi:
- allowed_splits dan ensure_publishable murni, cukup objek Report di memori (tanpa DB).
- save_report memakai db_session SQLite baru per tes (tests/conftest.py).
- monkeypatch mengganti repository.add_report dengan mata-mata untuk membuktikan laporan
  yang melanggar aturan ditolak SEBELUM menyentuh database (tidak ada setengah laporan).

Label: Positif = diterima, Negatif = ditolak, Edge = batas aturan.
"""

import inspect
from decimal import Decimal

import pytest
from fastapi.routing import APIRoute
from sqlalchemy import func, select

from app.main import app
from app.modules.cases.models import SplitTag
from app.modules.reports import repository, service
from app.modules.reports.models import (
    MeasurementKind,
    Report,
    ReportCaseBasis,
    ReportMarkerCode,
    ReportResult,
    ReportSession,
    ReportStatus,
    ScoreScope,
    ScoreSplit,
)
from app.shared.exceptions import ConflictError, ValidationError
from tests.reports_factory import (
    buat_admin,
    buat_rubrik,
    buat_sesi,
    dasar,
    hasil,
    laporan,
    sesi_laporan,
)

# --- allowed_splits ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("scope", "split"),
    [
        (ScoreScope.TEST, {ScoreSplit.TEST}),
        (ScoreScope.DEV, {ScoreSplit.DEV}),
        (ScoreScope.DEV_TEST, {ScoreSplit.TEST, ScoreSplit.DEV, ScoreSplit.COMBINED}),
    ],
)
def test_jenis_skor_yang_boleh_disimpan(scope, split):
    """Positif: Test hanya baris test; Dev hanya dev; Dev + Test tiga baris (D1)."""
    assert service.allowed_splits(scope) == frozenset(split)


def test_laporan_test_tidak_pernah_memuat_skor_dev_atau_gabungan():
    """Negatif: AC7, skor dev dan gabungan tidak ada di laporan Test (Viewer, terbit)."""
    assert not service.allowed_splits(ScoreScope.TEST) & {ScoreSplit.DEV, ScoreSplit.COMBINED}


# --- ensure_publishable --------------------------------------------------------------


def _laporan(scope: ScoreScope, has_dev: bool) -> Report:
    return Report(score_scope=scope, has_dev_scores=has_dev)


def test_laporan_test_boleh_diterbitkan():
    """Positif: laporan berisi skor test saja lolos penjaga (PBI-17 AC9)."""
    assert service.ensure_publishable(_laporan(ScoreScope.TEST, False)) is None


@pytest.mark.parametrize("scope", [ScoreScope.DEV, ScoreScope.DEV_TEST])
def test_laporan_dengan_skor_dev_tidak_boleh_diterbitkan(scope):
    """Negatif: Dev atau Dev + Test ditolak 409 dengan kode yang bisa dibaca klien (AC7, AC14)."""
    with pytest.raises(ConflictError) as info:
        service.ensure_publishable(_laporan(scope, True))

    assert info.value.code == service.REPORT_HAS_DEV_SCORES
    assert info.value.status_code == 409


def test_penanda_dev_saja_cukup_untuk_menolak():
    """Edge: objek yang tidak konsisten (scope test, penanda dev) tetap ditolak: tertutup bawaan."""
    with pytest.raises(ConflictError):
        service.ensure_publishable(_laporan(ScoreScope.TEST, True))


# --- save_report ---------------------------------------------------------------------


@pytest.fixture
def siap(db_session):
    admin = buat_admin(db_session)
    rubrik = buat_rubrik(db_session, admin)
    sesi_a = buat_sesi(db_session, admin, "Produk A 300s")
    sesi_b = buat_sesi(db_session, admin, "Produk B 300s")
    return db_session, admin, rubrik, sesi_a, sesi_b


def _isi(admin, rubrik, sesi_a, sesi_b, **ubah_laporan):
    report = laporan(admin, rubrik, **ubah_laporan)
    sesi = [sesi_laporan(report, sesi_a), sesi_laporan(report, sesi_b, product_label="Produk B")]
    hasil_ = [
        hasil(report, sesi_a, rank=1),
        hasil(report, sesi_b, rank=2, total_score=Decimal("0.4")),
    ]
    kasus = [dasar(report), dasar(report)]
    return report, sesi, hasil_, kasus


def _jumlah(db, model) -> int:
    return db.scalar(select(func.count()).select_from(model))


def test_simpan_laporan_utuh_dalam_satu_langkah(siap):
    """Positif: laporan, sesi, hasil, dan kasus dasar tersimpan bersama, status saved (AC10)."""
    db, admin, rubrik, sesi_a, sesi_b = siap
    report, sesi, hasil_, kasus = _isi(admin, rubrik, sesi_a, sesi_b)

    tersimpan = service.save_report(db, report, sesi, hasil_, kasus)

    assert tersimpan.id == report.id and tersimpan.status == ReportStatus.SAVED
    assert [_jumlah(db, m) for m in (ReportSession, ReportResult, ReportCaseBasis)] == [2, 2, 2]


def test_simpan_laporan_dev_test_dengan_tiga_baris_per_sesi(siap):
    """Positif: Dev + Test menyimpan test, dev, dan combined (AC14, D1)."""
    db, admin, rubrik, sesi_a, _ = siap
    report = laporan(
        admin, rubrik, score_scope=ScoreScope.DEV_TEST, has_dev_scores=True, case_basis_count=1
    )
    hasil_ = [hasil(report, sesi_a, score_split=s) for s in ScoreSplit]

    service.save_report(
        db, report, [sesi_laporan(report, sesi_a)], hasil_, [dasar(report, split_tag=SplitTag.DEV)]
    )

    assert _jumlah(db, ReportResult) == 3


@pytest.fixture
def mata_mata(monkeypatch):
    panggilan = []
    monkeypatch.setattr(repository, "add_report", lambda *args: panggilan.append(args))
    return panggilan


@pytest.mark.parametrize("split", [ScoreSplit.DEV, ScoreSplit.COMBINED])
def test_skor_dev_di_laporan_test_ditolak_sebelum_ke_database(siap, mata_mata, split):
    """Negatif: baris dev atau gabungan di laporan Test ditolak, tidak ada yang disimpan (AC7)."""
    db, admin, rubrik, sesi_a, sesi_b = siap
    report, sesi, hasil_, kasus = _isi(admin, rubrik, sesi_a, sesi_b)
    hasil_.append(hasil(report, sesi_a, score_split=split, rank=None, total_score=None))

    with pytest.raises(ValidationError) as info:
        service.save_report(db, report, sesi, hasil_, kasus)

    assert info.value.code == service.REPORT_SPLIT_NOT_ALLOWED
    assert mata_mata == []


def test_kasus_dev_di_dasar_laporan_test_ditolak(siap, mata_mata):
    """Negatif: kasus dev tidak boleh jadi dasar laporan Test, isinya tidak bocor ke Viewer (AC7)."""
    db, admin, rubrik, sesi_a, sesi_b = siap
    report, sesi, hasil_, kasus = _isi(admin, rubrik, sesi_a, sesi_b)
    kasus[1] = dasar(report, split_tag=SplitTag.DEV)

    with pytest.raises(ValidationError) as info:
        service.save_report(db, report, sesi, hasil_, kasus)

    assert info.value.code == service.REPORT_SPLIT_NOT_ALLOWED
    assert mata_mata == []


def test_produk_manual_dengan_tingkat_kegagalan_ditolak(siap, mata_mata):
    """Negatif: tingkat kegagalan tidak berlaku untuk produk manual (AC2)."""
    db, admin, rubrik, sesi_a, sesi_b = siap
    report, sesi, hasil_, kasus = _isi(admin, rubrik, sesi_a, sesi_b)
    hasil_[0].failure_rate = Decimal("12.50")

    with pytest.raises(ValidationError) as info:
        service.save_report(db, report, sesi, hasil_, kasus)

    assert info.value.code == service.REPORT_MANUAL_FAILURE_RATE
    assert mata_mata == []


def test_produk_otomatis_boleh_punya_tingkat_kegagalan(siap):
    """Positif: tingkat kegagalan otomatis dari total upaya termasuk percobaan ulang (AC2)."""
    db, admin, rubrik, sesi_a, sesi_b = siap
    report, sesi, hasil_, kasus = _isi(admin, rubrik, sesi_a, sesi_b)
    sesi[0].measurement_kind = MeasurementKind.AUTOMATIC
    hasil_[0].failure_rate = Decimal("12.50")

    service.save_report(db, report, sesi, hasil_, kasus)

    assert db.get(ReportResult, hasil_[0].id).failure_rate == Decimal("12.50")


@pytest.mark.parametrize("jumlah_kasus", [1, 3])
def test_jumlah_kasus_dasar_harus_sama_dengan_baris_dasar(siap, mata_mata, jumlah_kasus):
    """Negatif: case_basis_count yang tidak cocok dengan daftar kasus membuat angka tak bisa dijelaskan (AC1, AC3)."""
    db, admin, rubrik, sesi_a, sesi_b = siap
    report, sesi, hasil_, kasus = _isi(admin, rubrik, sesi_a, sesi_b, case_basis_count=jumlah_kasus)

    with pytest.raises(ValidationError) as info:
        service.save_report(db, report, sesi, hasil_, kasus)

    assert info.value.code == service.REPORT_CASE_BASIS_MISMATCH
    assert mata_mata == []


def test_hasil_untuk_sesi_di_luar_laporan_ditolak(siap, mata_mata):
    """Negatif: setiap baris hasil wajib milik salah satu sesi laporan itu."""
    db, admin, rubrik, sesi_a, sesi_b = siap
    report, sesi, hasil_, kasus = _isi(admin, rubrik, sesi_a, sesi_b)

    with pytest.raises(ValidationError) as info:
        service.save_report(db, report, sesi[:1], hasil_, kasus)

    assert info.value.code == service.REPORT_RESULT_SESSION_UNKNOWN
    assert mata_mata == []


def test_baris_milik_laporan_lain_ditolak(siap, mata_mata):
    """Negatif: semua baris anak wajib menunjuk laporan yang sedang disimpan."""
    db, admin, rubrik, sesi_a, sesi_b = siap
    report, sesi, hasil_, kasus = _isi(admin, rubrik, sesi_a, sesi_b)
    lain = laporan(admin, rubrik)
    kasus[0] = dasar(lain)

    with pytest.raises(ValidationError) as info:
        service.save_report(db, report, sesi, hasil_, kasus)

    assert info.value.code == service.REPORT_ROW_OTHER_REPORT
    assert mata_mata == []


def test_laporan_satu_produk_tersimpan(siap):
    """Edge: satu produk saja tetap tersimpan; batas jumlah produk bukan urusan tabel."""
    db, admin, rubrik, sesi_a, _ = siap
    report = laporan(admin, rubrik, case_basis_count=1)

    service.save_report(
        db, report, [sesi_laporan(report, sesi_a)], [hasil(report, sesi_a)], [dasar(report)]
    )

    assert _jumlah(db, Report) == 1


# --- report_titles (label objek audit) ----------------------------------------------


def test_judul_laporan_untuk_label_audit(siap):
    """Positif: halaman audit menampilkan judul laporan, bukan UUID (PBI-18 F-06)."""
    db, admin, rubrik, sesi_a, sesi_b = siap
    report, sesi, hasil_, kasus = _isi(admin, rubrik, sesi_a, sesi_b)
    service.save_report(db, report, sesi, hasil_, kasus)
    hilang = laporan(admin, rubrik).id

    assert service.report_titles(db, {report.id, hilang}) == {report.id: "Laporan Oktober"}


def test_tanpa_id_tidak_ada_query(monkeypatch, db_session):
    """Edge: halaman audit tanpa catatan laporan tidak memanggil database."""
    monkeypatch.setattr(repository, "titles", lambda *_: pytest.fail("tidak boleh query"))

    assert service.report_titles(db_session, set()) == {}


# --- Tidak ada jalur hapus atau ubah (AC10, riwayat permanen) ------------------------


def test_tidak_ada_endpoint_hapus_laporan():
    """Negatif: tidak ada DELETE di bawah /reports atau /published-reports (Jira SCRUM-300).

    Penjaga regresi: lulus sejak awal, gagal begitu ada yang menambah route hapus.
    """
    hapus = [
        route.path
        for route in app.routes
        if isinstance(route, APIRoute)
        and "DELETE" in route.methods
        and route.path.startswith(("/reports", "/published-reports"))
    ]

    assert hapus == []


@pytest.mark.parametrize("modul", [repository, service])
def test_tidak_ada_fungsi_ubah_atau_hapus(modul):
    """Negatif: laporan beku hanya ditambah dan dibaca; status terbit milik SCRUM-332."""
    nama = [
        n
        for n, _ in inspect.getmembers(modul, inspect.isfunction)
        if _.__module__ == modul.__name__
    ]

    assert not [n for n in nama if n.startswith(("delete", "remove", "update", "edit"))]


# --- Kelengkapan baris hasil (review Kak Aileen #4) -------------------------------------


def test_dev_test_tanpa_baris_combined_ditolak(siap, mata_mata):
    """Negatif: Dev + Test wajib tepat tiga baris per sesi (test, dev, combined), D1."""
    db, admin, rubrik, sesi_a, _ = siap
    report = laporan(
        admin, rubrik, score_scope=ScoreScope.DEV_TEST, has_dev_scores=True, case_basis_count=1
    )
    hasil_ = [hasil(report, sesi_a, score_split=s) for s in (ScoreSplit.TEST, ScoreSplit.DEV)]

    with pytest.raises(ValidationError) as info:
        service.save_report(
            db,
            report,
            [sesi_laporan(report, sesi_a)],
            hasil_,
            [dasar(report, split_tag=SplitTag.DEV)],
        )

    assert info.value.code == service.REPORT_RESULTS_INCOMPLETE
    assert mata_mata == []


def test_sesi_tanpa_hasil_ditolak(siap, mata_mata):
    """Negatif: setiap sesi di laporan harus punya angka; sesi kosong tidak bisa dijelaskan."""
    db, admin, rubrik, sesi_a, sesi_b = siap
    report, sesi, hasil_, kasus = _isi(admin, rubrik, sesi_a, sesi_b)

    with pytest.raises(ValidationError) as info:
        service.save_report(db, report, sesi, hasil_[:1], kasus)

    assert info.value.code == service.REPORT_RESULTS_INCOMPLETE
    assert mata_mata == []


# --- Bentuk markers (keputusan SA, review Kak Aileen #9) -------------------------------


def test_kode_penanda_sesuai_keputusan_sa():
    """Positif: empat kode D2; Uji Coba hanya di is_trial, bukan di markers."""
    assert [m.value for m in ReportMarkerCode] == [
        "model_version_differs",
        "repetitions_differ",
        "manual_date_span_over_7_days",
        "refusals",
    ]


def test_penanda_yang_sah_tersimpan(siap):
    """Positif: list objek {code, detail}; detail boleh null atau teks (refusals: jumlahnya)."""
    db, admin, rubrik, sesi_a, sesi_b = siap
    report, sesi, hasil_, kasus = _isi(admin, rubrik, sesi_a, sesi_b)
    sesi[0].markers = [
        {"code": "refusals", "detail": "3"},
        {"code": "model_version_differs", "detail": None},
    ]

    service.save_report(db, report, sesi, hasil_, kasus)

    assert db.get(ReportSession, (report.id, sesi_a.id)).markers[0] == {
        "code": "refusals",
        "detail": "3",
    }


@pytest.mark.parametrize(
    "markers",
    [
        [{"code": "trial", "detail": None}],
        [{"code": "kode_lain", "detail": None}],
        [{"code": "refusals"}],
        [{"code": "refusals", "detail": 3}],
        [{"code": "refusals", "detail": "3", "lain": 1}],
        ["refusals"],
        {"code": "refusals", "detail": "3"},
    ],
    ids=[
        "trial",
        "unknown-code",
        "no-detail",
        "detail-not-text",
        "extra-key",
        "not-object",
        "not-list",
    ],
)
def test_penanda_dengan_bentuk_lain_ditolak(siap, mata_mata, markers):
    """Negatif: SCRUM-301 dan SCRUM-313 harus mengisi bentuk yang sama, jadi bentuk lain ditolak."""
    db, admin, rubrik, sesi_a, sesi_b = siap
    report, sesi, hasil_, kasus = _isi(admin, rubrik, sesi_a, sesi_b)
    sesi[0].markers = markers

    with pytest.raises(ValidationError) as info:
        service.save_report(db, report, sesi, hasil_, kasus)

    assert info.value.code == service.REPORT_MARKER_INVALID
    assert mata_mata == []


# --- Review round 2 (Kak Aileen, non-blocking 1) --------------------------------------


def test_laporan_tanpa_id_diberi_id_sebelum_dicek(siap, mata_mata):
    """Edge: Report() tanpa id dapat UUID di awal save_report, jadi baris anak yang
    menunjuk None ditolak jelas (REPORT_ROW_OTHER_REPORT), bukan IntegrityError saat flush."""
    db, admin, rubrik, sesi_a, _ = siap
    report = laporan(admin, rubrik, case_basis_count=1)
    report.id = None
    sesi = [sesi_laporan(report, sesi_a)]

    with pytest.raises(ValidationError) as info:
        service.save_report(db, report, sesi, [hasil(report, sesi_a)], [dasar(report)])

    assert info.value.code == service.REPORT_ROW_OTHER_REPORT
    assert report.id is not None
    assert mata_mata == []
