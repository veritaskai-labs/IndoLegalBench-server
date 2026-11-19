"""Test query baca riwayat laporan di SQLite (PBI-15 SCRUM-320).

Yang diuji di sini terjemahan ReportVisibility (D9) ke SQL, paginasi, urutan, dan join
ke rubric_versions dan case_versions. Aturan bisnisnya sendiri diuji tanpa database di
test_report_access.py dan test_report_history_service.py.

Data dibuat dengan tests/reports_factory.py milik SCRUM-300. SQLite tidak menyalakan
trigger laporan beku, jadi status dan created_at boleh diisi langsung untuk menyiapkan
data.

Label: Positif = baris yang harus muncul, Negatif = baris yang harus tersaring,
Edge = urutan, halaman, dan data milik laporan lain.
"""

import uuid
from datetime import UTC, datetime

import pytest

from app.modules.cases.models import CaseStatus, CaseVersion, SplitTag
from app.modules.reports import repository
from app.modules.reports.access import ReportVisibility
from app.modules.reports.models import ReportStatus, ScoreScope, ScoreSplit
from tests import reports_factory as pabrik


@pytest.fixture
def dasar_data(db_session):
    """Admin, rubrik v1, dan dua sesi: cukup untuk membuat beberapa laporan berbeda."""
    admin = pabrik.buat_admin(db_session)
    rubrik = pabrik.buat_rubrik(db_session, admin)
    sesi_a = pabrik.buat_sesi(db_session, admin, "Gemini")
    sesi_b = pabrik.buat_sesi(db_session, admin, "Justisio")
    return admin, rubrik, sesi_a, sesi_b


def _simpan(db, admin, rubrik, sesi, *, hari=1, **ubah):
    """Satu laporan lengkap berisi satu sesi, dibuat pada tanggal hari-ke-N Desember."""
    report = pabrik.laporan(
        admin, rubrik, created_at=datetime(2026, 12, hari, tzinfo=UTC), case_basis_count=1, **ubah
    )
    split = ScoreSplit.TEST if report.score_scope is ScoreScope.TEST else ScoreSplit.DEV
    tag = SplitTag.TEST if split is ScoreSplit.TEST else SplitTag.DEV
    repository.add_report(
        db,
        report,
        [pabrik.sesi_laporan(report, sesi)],
        [pabrik.hasil(report, sesi, score_split=split)],
        [pabrik.dasar(report, split_tag=tag)],
    )
    return report


@pytest.fixture
def empat_laporan(db_session, dasar_data):
    """Satu laporan untuk tiap sel D9: milik sendiri, milik orang lain, terbit, dan skor dev."""
    admin, rubrik, sesi_a, _ = dasar_data
    lain = pabrik.buat_admin(db_session).id
    milik_sendiri = _simpan(db_session, admin, rubrik, sesi_a, hari=1)
    milik_lain = _simpan(db_session, admin, rubrik, sesi_a, hari=2, created_by=lain)
    terbit = _simpan(
        db_session, admin, rubrik, sesi_a, hari=3, created_by=lain, status=ReportStatus.PUBLISHED
    )
    dev = _simpan(
        db_session,
        admin,
        rubrik,
        sesi_a,
        hari=4,
        score_scope=ScoreScope.DEV,
        has_dev_scores=True,
    )
    return admin.id, {"sendiri": milik_sendiri, "lain": milik_lain, "terbit": terbit, "dev": dev}


def _ids(rows):
    return {row.id for row in rows}


# --- list_reports -------------------------------------------------------------


def test_admin_melihat_semua_laporan(db_session, empat_laporan):
    """Positif: Admin melihat semua laporan tersimpan, termasuk milik orang lain dan dev."""
    # Arrange
    admin_id, laporan = empat_laporan
    visibility = ReportVisibility(viewer_id=admin_id, all_saved=True, include_dev=True)

    # Act
    rows, total = repository.list_reports(db_session, visibility, offset=0, limit=20)

    # Assert
    assert _ids(rows) == {r.id for r in laporan.values()}
    assert total == 4


def test_tanpa_hak_dev_hanya_milik_sendiri_dan_terbit(db_session, empat_laporan):
    """Negatif: laporan tersimpan orang lain dan laporan skor dev tidak ikut terambil."""
    # Arrange
    pemilik, laporan = empat_laporan
    visibility = ReportVisibility(viewer_id=pemilik, all_saved=False, include_dev=False)

    # Act
    rows, total = repository.list_reports(db_session, visibility, offset=0, limit=20)

    # Assert
    assert _ids(rows) == {laporan["sendiri"].id, laporan["terbit"].id}
    assert total == 2


def test_daftar_terbaru_dulu_dan_total_menghitung_semua_halaman(db_session, empat_laporan):
    """Edge: halaman kedua berisi laporan yang lebih lama, total tetap seluruh baris."""
    # Arrange
    admin_id, laporan = empat_laporan
    visibility = ReportVisibility(viewer_id=admin_id, all_saved=True, include_dev=True)

    # Act
    rows, total = repository.list_reports(db_session, visibility, offset=1, limit=2)

    # Assert
    assert [row.id for row in rows] == [laporan["terbit"].id, laporan["lain"].id]
    assert total == 4


# --- get_report dan contents --------------------------------------------------


def test_get_report_mengembalikan_baris_atau_none(db_session, empat_laporan):
    """Positif dan negatif: id yang ada mengembalikan laporannya, id acak mengembalikan None."""
    # Arrange
    _, laporan = empat_laporan

    # Act
    ada = repository.get_report(db_session, laporan["sendiri"].id)
    tidak_ada = repository.get_report(db_session, uuid.uuid4())

    # Assert
    assert ada is laporan["sendiri"]
    assert tidak_ada is None


def test_contents_membaca_versi_rubrik_sesi_hasil_dan_kode_kasus(db_session, dasar_data):
    """Positif: isi beku satu laporan, kode kasus dan versinya diambil lewat case_versions."""
    # Arrange
    admin, rubrik, sesi_a, sesi_b = dasar_data
    report = pabrik.laporan(admin, rubrik, case_basis_count=2)
    versi = [
        CaseVersion(
            case_id=uuid.uuid4(),
            version_no=nomor,
            status=CaseStatus.APPROVED,
            case_code=kode,
            split_tag=SplitTag.TEST,
            content={},
            created_by=admin.id,
        )
        for kode, nomor in (("UAT-PHK-002", 1), ("UAT-PHK-001", 3))
    ]
    db_session.add_all(versi)
    db_session.flush()
    repository.add_report(
        db_session,
        report,
        [
            pabrik.sesi_laporan(report, sesi_b, product_label="Justisio"),
            pabrik.sesi_laporan(report, sesi_a, product_label="Gemini"),
        ],
        [pabrik.hasil(report, sesi_a, rank=1), pabrik.hasil(report, sesi_b, rank=2)],
        [
            pabrik.dasar(report, case_id=v.case_id, case_version_id=v.id, split_tag=SplitTag.TEST)
            for v in versi
        ],
    )
    # Edge: kasus dasar laporan lain tidak boleh ikut terbaca.
    _simpan(db_session, admin, rubrik, sesi_a, hari=9)

    # Act
    isi = repository.contents(db_session, report)

    # Assert
    assert isi.rubric_version_no == 1
    assert [s.product_label for s in isi.sessions] == ["Gemini", "Justisio"]
    assert {(r.session_id, r.rank) for r in isi.results} == {(sesi_a.id, 1), (sesi_b.id, 2)}
    assert isi.case_basis == [
        repository.CaseBasisRow("UAT-PHK-001", 3, SplitTag.TEST),
        repository.CaseBasisRow("UAT-PHK-002", 1, SplitTag.TEST),
    ]


def test_contents_tag_kasus_diambil_dari_versi_kasus_bukan_salinan(db_session, dasar_data):
    """Negatif: salinan split_tag yang salah tidak boleh membuka kode kasus test untuk Developer AI.

    Kasus test yang tersimpan di report_case_basis sebagai dev (salinan keliru, review #146)
    tetap terbaca sebagai test, jadi tetap disamarkan.
    """
    # Arrange
    admin, rubrik, sesi_a, _ = dasar_data
    report = pabrik.laporan(
        admin, rubrik, score_scope=ScoreScope.DEV_TEST, has_dev_scores=True, case_basis_count=1
    )
    versi = CaseVersion(
        case_id=uuid.uuid4(),
        version_no=1,
        status=CaseStatus.APPROVED,
        case_code="RAHASIA-001",
        split_tag=SplitTag.TEST,
        content={},
        created_by=admin.id,
    )
    db_session.add(versi)
    db_session.flush()
    repository.add_report(
        db_session,
        report,
        [pabrik.sesi_laporan(report, sesi_a)],
        [pabrik.hasil(report, sesi_a, score_split=split) for split in ScoreSplit],
        [
            pabrik.dasar(
                report, case_id=versi.case_id, case_version_id=versi.id, split_tag=SplitTag.DEV
            )
        ],
    )

    # Act
    isi = repository.contents(db_session, report)

    # Assert
    assert isi.case_basis == [repository.CaseBasisRow("RAHASIA-001", 1, SplitTag.TEST)]
