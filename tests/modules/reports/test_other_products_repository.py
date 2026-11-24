"""Query produk lain pada angka yang sama di SQLite (PBI-16 SCRUM-370).

Yang diuji: dari satu angka laporan, repository mengembalikan angka produk lain di laporan
yang sama dengan jenis skor yang sama. Hasilnya dipakai untuk membandingkan jawaban beberapa
produk pada satu kasus saat penelusuran.

Data dibuat dengan tests/reports_factory.py milik SCRUM-300.

Label: Positif = baris yang harus terbaca, Negatif = baris yang harus diabaikan,
Edge = laporan dengan satu produk.
"""

import pytest

from app.modules.reports import repository
from app.modules.reports.models import MeasurementKind, ScoreSplit
from tests import reports_factory as pabrik


@pytest.fixture
def tiga_produk(db_session):
    """Laporan dengan tiga produk, plus laporan lain yang memuat produk B sebagai pengecoh.

    Produk A punya angka test dan dev, B hanya test, C hanya dev.
    """
    admin = pabrik.buat_admin(db_session)
    rubrik = pabrik.buat_rubrik(db_session, admin)
    sesi_a = pabrik.buat_sesi(db_session, admin, "Produk A")
    sesi_b = pabrik.buat_sesi(db_session, admin, "Produk B")
    sesi_c = pabrik.buat_sesi(db_session, admin, "Produk C")
    report = pabrik.laporan(admin, rubrik, case_basis_count=1)
    hasil_a_test = pabrik.hasil(report, sesi_a, score_split=ScoreSplit.TEST)
    hasil_a_dev = pabrik.hasil(report, sesi_a, score_split=ScoreSplit.DEV)
    hasil_b_test = pabrik.hasil(report, sesi_b, score_split=ScoreSplit.TEST, rank=2)
    hasil_c_dev = pabrik.hasil(report, sesi_c, score_split=ScoreSplit.DEV, rank=2)
    repository.add_report(
        db_session,
        report,
        [
            pabrik.sesi_laporan(report, sesi_a, product_label="Produk A"),
            pabrik.sesi_laporan(
                report,
                sesi_b,
                product_label="Produk B",
                measurement_kind=MeasurementKind.AUTOMATIC,
            ),
            pabrik.sesi_laporan(report, sesi_c, product_label="Produk C", is_trial=True),
        ],
        [hasil_a_test, hasil_a_dev, hasil_b_test, hasil_c_dev],
        [pabrik.dasar(report)],
    )
    lain = pabrik.laporan(admin, rubrik, case_basis_count=1)
    hasil_lain = pabrik.hasil(lain, sesi_b, score_split=ScoreSplit.TEST)
    repository.add_report(
        db_session,
        lain,
        [pabrik.sesi_laporan(lain, sesi_b, product_label="Produk B")],
        [hasil_lain],
        [pabrik.dasar(lain)],
    )
    return report, hasil_a_test, hasil_a_dev, hasil_b_test, hasil_c_dev, sesi_b, hasil_lain


def test_produk_lain_dengan_jenis_skor_yang_sama_terbaca_lengkap(db_session, tiga_produk):
    """Positif: produk B muncul dengan label beku, jenis pengukuran, dan penanda uji coba."""
    # Arrange
    report, hasil_a_test, _, hasil_b_test, _, sesi_b, _ = tiga_produk

    # Act
    lain = repository.other_product_results(db_session, report.id, hasil_a_test)

    # Assert
    assert lain == [
        repository.OtherProductRow(
            result_id=hasil_b_test.id,
            session_id=sesi_b.id,
            product_label="Produk B",
            measurement_kind=MeasurementKind.AUTOMATIC,
            is_trial=False,
        )
    ]


def test_angka_yang_sedang_dibuka_tidak_ikut_daftar(db_session, tiga_produk):
    """Negatif: produk yang sedang ditelusuri tidak dibandingkan dengan dirinya sendiri."""
    # Arrange
    report, hasil_a_test, _, _, _, _, _ = tiga_produk

    # Act
    lain = repository.other_product_results(db_session, report.id, hasil_a_test)

    # Assert
    assert hasil_a_test.id not in [baris.result_id for baris in lain]


def test_jenis_skor_lain_tidak_tercampur(db_session, tiga_produk):
    """Negatif: angka dev hanya dibandingkan dengan angka dev, angka test dengan angka test."""
    # Arrange
    report, hasil_a_test, hasil_a_dev, _, hasil_c_dev, _, _ = tiga_produk

    # Act
    untuk_test = repository.other_product_results(db_session, report.id, hasil_a_test)
    untuk_dev = repository.other_product_results(db_session, report.id, hasil_a_dev)

    # Assert
    assert [(b.product_label, b.is_trial) for b in untuk_test] == [("Produk B", False)]
    assert [(b.result_id, b.is_trial) for b in untuk_dev] == [(hasil_c_dev.id, True)]


def test_angka_laporan_lain_tidak_ikut_walau_sesinya_sama(db_session, tiga_produk):
    """Negatif: sesi produk B juga ada di laporan lain, tetapi angka laporan lain tidak terbaca."""
    # Arrange
    report, hasil_a_test, _, _, _, _, hasil_lain = tiga_produk

    # Act
    lain = repository.other_product_results(db_session, report.id, hasil_a_test)

    # Assert
    assert hasil_lain.id not in [baris.result_id for baris in lain]


def test_laporan_satu_produk_tidak_punya_produk_lain(db_session):
    """Edge: laporan dengan satu produk menghasilkan daftar kosong, bukan error."""
    # Arrange
    admin = pabrik.buat_admin(db_session)
    rubrik = pabrik.buat_rubrik(db_session, admin)
    sesi = pabrik.buat_sesi(db_session, admin, "Produk A")
    report = pabrik.laporan(admin, rubrik, case_basis_count=1)
    hasil = pabrik.hasil(report, sesi)
    repository.add_report(
        db_session, report, [pabrik.sesi_laporan(report, sesi)], [hasil], [pabrik.dasar(report)]
    )

    # Act
    lain = repository.other_product_results(db_session, report.id, hasil)

    # Assert
    assert lain == []


def test_produk_lain_diurutkan_menurut_label_lalu_sesi(db_session):
    """Edge: urutan tetap (label, lalu id sesi) supaya tampilan perbandingan tidak berubah-ubah."""
    # Arrange
    admin = pabrik.buat_admin(db_session)
    rubrik = pabrik.buat_rubrik(db_session, admin)
    sesi_a = pabrik.buat_sesi(db_session, admin, "Produk A")
    sesi_z = pabrik.buat_sesi(db_session, admin, "Produk Z")
    sesi_m = pabrik.buat_sesi(db_session, admin, "Produk M")
    report = pabrik.laporan(admin, rubrik, case_basis_count=1)
    hasil_a = pabrik.hasil(report, sesi_a)
    repository.add_report(
        db_session,
        report,
        [
            pabrik.sesi_laporan(report, sesi_a, product_label="Produk A"),
            pabrik.sesi_laporan(report, sesi_z, product_label="Produk Z"),
            pabrik.sesi_laporan(report, sesi_m, product_label="Produk M"),
        ],
        [
            hasil_a,
            pabrik.hasil(report, sesi_z, rank=2),
            pabrik.hasil(report, sesi_m, rank=3),
        ],
        [pabrik.dasar(report)],
    )

    # Act
    lain = repository.other_product_results(db_session, report.id, hasil_a)

    # Assert
    assert [baris.product_label for baris in lain] == ["Produk M", "Produk Z"]
