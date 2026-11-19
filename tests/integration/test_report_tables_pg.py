"""SCRUM-300: laporan beku dan penerbitan di PostgreSQL. PBI-15 AC7, AC10, AC14; PBI-17 AC9.

Laporan yang dibuka ulang harus menampilkan angka yang sama (AC10), juga setelah sesi
dinilai ulang (SCRUM-323). Kode aplikasi tidak punya jalur ubah atau hapus, tapi itu
belum cukup: database sendiri menolak setiap perubahan, untuk semua peran.

- Isi laporan (sesi, hasil, dasar kasus, versi terbit): hanya INSERT.
- reports: tidak bisa dihapus; satu-satunya kolom yang boleh berubah adalah status,
  hanya maju (saved → published → superseded) dan hanya kalau baris published_reports-nya ada.
- Isi laporan hanya bisa ditambah dalam transaksi yang membuat laporannya.
- Laporan Test tidak pernah memuat skor dev; laporan dengan skor dev tidak bisa terbit.

Isolasi: satu koneksi, satu transaksi per tes yang selalu di-rollback; penolakan dibungkus
savepoint (tests/measurement_pg.galat). "Transaksi lain" disimulasikan dengan created_at
di masa lalu, karena now() di PostgreSQL adalah waktu awal transaksi.

Dilewati kalau TEST_POSTGRES_URL kosong. Label: Positif, Negatif, Edge.
"""

import os
import uuid

import pytest
from sqlalchemy import create_engine, text

from tests.measurement_pg import (
    CHECK_VIOLATION,
    FOREIGN_KEY_VIOLATION,
    INSUFFICIENT_PRIVILEGE,
    buat_kasus,
    galat,
    sqlstate_penolakan,
)
from tests.reports_pg import (
    buat_laporan,
    laporan_utuh,
    siapkan,
    tambah_dasar,
    tambah_hasil,
    tambah_sesi,
    terbitkan,
    ubah_status,
)

POSTGRES_URL = os.environ.get("TEST_POSTGRES_URL", "")

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(not POSTGRES_URL, reason="TEST_POSTGRES_URL tidak diisi"),
]


@pytest.fixture
def conn():
    engine = create_engine(POSTGRES_URL)
    with engine.connect() as koneksi:
        transaksi = koneksi.begin()
        try:
            yield koneksi
        finally:
            transaksi.rollback()
    engine.dispose()


@pytest.fixture
def dunia(conn):
    return siapkan(conn)


def _sqlstate(conn, aksi) -> str:
    return galat(conn, aksi).sqlstate


# --- Positif: laporan utuh -------------------------------------------------------------


def test_laporan_utuh_tersimpan(conn, dunia):
    """Positif: laporan, sesi, hasil, dan kasus dasar masuk dalam satu transaksi."""
    laporan = laporan_utuh(conn, dunia)

    jumlah = conn.execute(
        text(
            "SELECT (SELECT count(*) FROM report_sessions WHERE report_id = :id), "
            "(SELECT count(*) FROM report_results WHERE report_id = :id), "
            "(SELECT count(*) FROM report_case_basis WHERE report_id = :id), "
            "(SELECT status::text FROM reports WHERE id = :id)"
        ),
        {"id": laporan},
    ).one()

    assert tuple(jumlah) == (1, 1, 1, "saved")


# --- Negatif: isi laporan beku -----------------------------------------------------------


@pytest.mark.parametrize(
    ("perintah", "kunci"),
    [
        ("UPDATE report_sessions SET product_label = 'Kode X' WHERE report_id = :id", "report"),
        ("DELETE FROM report_sessions WHERE report_id = :id", "report"),
        ("UPDATE report_results SET det_score = 1 WHERE report_id = :id", "report"),
        ("DELETE FROM report_results WHERE report_id = :id", "report"),
        ("UPDATE report_case_basis SET split_tag = 'test' WHERE report_id = :id", "report"),
        ("DELETE FROM report_case_basis WHERE report_id = :id", "report"),
        ("UPDATE published_reports SET version_no = 9 WHERE id = :pub", "pub"),
        ("DELETE FROM published_reports WHERE id = :pub", "pub"),
    ],
)
def test_isi_laporan_tidak_bisa_diubah_atau_dihapus(conn, dunia, perintah, kunci):
    """Negatif: angka, label, dasar kasus, dan versi terbit beku (AC10, PBI-17 AC9)."""
    laporan = laporan_utuh(conn, dunia)
    pub = terbitkan(conn, laporan, dunia)

    sqlstate = sqlstate_penolakan(conn, perintah, {"id": laporan, "pub": pub})

    assert sqlstate == INSUFFICIENT_PRIVILEGE


@pytest.mark.parametrize(
    "tabel",
    ["reports", "report_sessions", "report_results", "report_case_basis", "published_reports"],
)
def test_tabel_laporan_tidak_bisa_dikosongkan(conn, dunia, tabel):
    """Negatif: TRUNCATE juga ditolak; trigger baris tidak menangkapnya sendiri."""
    laporan_utuh(conn, dunia)

    assert sqlstate_penolakan(conn, f"TRUNCATE {tabel} CASCADE", {}) == INSUFFICIENT_PRIVILEGE


def test_laporan_tidak_bisa_dihapus(conn, dunia):
    """Negatif: riwayat permanen, tidak ada hapus laporan (Jira SCRUM-300, AC10)."""
    laporan = laporan_utuh(conn, dunia)

    sqlstate = sqlstate_penolakan(conn, "DELETE FROM reports WHERE id = :id", {"id": laporan})

    assert sqlstate == INSUFFICIENT_PRIVILEGE


@pytest.mark.parametrize(
    "perubahan",
    [
        "title = 'Judul baru'",
        "score_scope = 'dev', has_dev_scores = true",
        "case_basis_count = 2",
        "judge_model_name = 'judge lain'",
        "created_at = now() - interval '1 day'",
    ],
)
def test_kolom_laporan_selain_status_beku(conn, dunia, perubahan):
    """Negatif: judul, jenis nilai, versi penilai, dan jumlah kasus tidak bisa diubah."""
    laporan = laporan_utuh(conn, dunia)

    sqlstate = sqlstate_penolakan(
        conn, f"UPDATE reports SET {perubahan} WHERE id = :id", {"id": laporan}
    )

    assert sqlstate == INSUFFICIENT_PRIVILEGE


@pytest.mark.parametrize("tambah", ["sesi", "hasil", "dasar"])
def test_isi_tidak_bisa_ditambah_ke_laporan_lama(conn, dunia, tambah):
    """Negatif: laporan yang sudah dibuat tertutup; menambah baris kemudian mengubah angkanya."""
    laporan = buat_laporan(conn, dunia, created_at="2026-11-01T00:00:00+07:00")

    def aksi():
        if tambah == "sesi":
            tambah_sesi(conn, laporan, dunia.sesi_a, dunia.produk_a)
        elif tambah == "hasil":
            tambah_hasil(conn, laporan, dunia.sesi_a)
        else:
            tambah_dasar(conn, laporan, *buat_kasus(conn, dunia.admin, split_tag="test"))

    assert _sqlstate(conn, aksi) == INSUFFICIENT_PRIVILEGE


# --- Jenis skor per jenis nilai (AC7, AC14) -----------------------------------------


@pytest.mark.parametrize(
    ("scope", "has_dev", "split"),
    [
        ("test", False, "dev"),
        ("test", False, "combined"),
        ("dev", True, "test"),
        ("dev", True, "combined"),
    ],
)
def test_jenis_skor_di_luar_jenis_nilai_ditolak(conn, dunia, scope, has_dev, split):
    """Negatif: laporan Test tidak pernah memuat skor dev atau gabungan, dan sebaliknya."""
    laporan = buat_laporan(conn, dunia, score_scope=scope, has_dev_scores=has_dev)
    tambah_sesi(conn, laporan, dunia.sesi_a, dunia.produk_a)

    assert (
        _sqlstate(conn, lambda: tambah_hasil(conn, laporan, dunia.sesi_a, split=split))
        == CHECK_VIOLATION
    )


def test_dev_test_menyimpan_tiga_jenis_skor(conn, dunia):
    """Positif: Dev + Test menyimpan baris test, dev, dan combined per sesi (D1)."""
    laporan = buat_laporan(conn, dunia, score_scope="dev_test", has_dev_scores=True)
    tambah_sesi(conn, laporan, dunia.sesi_a, dunia.produk_a)

    for split in ("test", "dev", "combined"):
        tambah_hasil(conn, laporan, dunia.sesi_a, split=split)


def test_kasus_dev_tidak_boleh_jadi_dasar_laporan_test(conn, dunia):
    """Negatif: kode kasus dev tidak ikut ke laporan Test yang dibaca Viewer (AC7)."""
    laporan = buat_laporan(conn, dunia)
    kasus, versi = buat_kasus(conn, dunia.admin, split_tag="dev")

    assert (
        _sqlstate(conn, lambda: tambah_dasar(conn, laporan, kasus, versi, split="dev"))
        == CHECK_VIOLATION
    )


def test_versi_kasus_harus_milik_kasusnya(conn, dunia):
    """Negatif: dasar kasus menunjuk versi kasus lain, penelusuran PBI-16 akan salah."""
    laporan = buat_laporan(conn, dunia)
    kasus, _ = buat_kasus(conn, dunia.admin, split_tag="test")
    _, versi_lain = buat_kasus(conn, dunia.admin, split_tag="test")

    assert (
        _sqlstate(conn, lambda: tambah_dasar(conn, laporan, kasus, versi_lain)) == CHECK_VIOLATION
    )


def test_produk_manual_tanpa_tingkat_kegagalan(conn, dunia):
    """Negatif + Edge: manual dengan tingkat kegagalan ditolak; otomatis boleh (AC2)."""
    laporan = buat_laporan(conn, dunia)
    tambah_sesi(conn, laporan, dunia.sesi_a, dunia.produk_a, kind="manual")
    tambah_sesi(conn, laporan, dunia.sesi_b, dunia.produk_b, kind="automatic")

    ditolak = _sqlstate(conn, lambda: tambah_hasil(conn, laporan, dunia.sesi_a, failure_rate=5))
    tambah_hasil(conn, laporan, dunia.sesi_b, failure_rate=5, rank=2)

    assert ditolak == CHECK_VIOLATION


def test_hasil_untuk_sesi_di_luar_laporan_ditolak(conn, dunia):
    """Negatif: FK gabungan, baris hasil hanya untuk sesi yang ada di laporan itu."""
    laporan = buat_laporan(conn, dunia)
    tambah_sesi(conn, laporan, dunia.sesi_a, dunia.produk_a)

    assert (
        _sqlstate(conn, lambda: tambah_hasil(conn, laporan, dunia.sesi_b)) == FOREIGN_KEY_VIOLATION
    )


# --- Penerbitan (PBI-17 AC9) dan skor dev (AC7, AC14) -------------------------------


def test_terbit_lalu_diganti_versi_baru(conn, dunia):
    """Positif: v1 terbit, v2 menggantikannya, v1 tetap tersimpan sebagai superseded."""
    lama, baru = laporan_utuh(conn, dunia), laporan_utuh(conn, dunia)
    v1 = terbitkan(conn, lama, dunia)
    ubah_status(conn, lama, "published")

    terbitkan(conn, baru, dunia, version_no=2, supersedes=v1)
    ubah_status(conn, baru, "published")
    ubah_status(conn, lama, "superseded")

    baris = conn.execute(
        text("SELECT id, status::text FROM reports WHERE id IN (:a, :b)"),
        {"a": lama, "b": baru},
    ).all()
    status = dict(map(tuple, baris))
    assert status == {lama: "superseded", baru: "published"}


@pytest.mark.parametrize("scope", ["dev", "dev_test"])
def test_laporan_dengan_skor_dev_tidak_bisa_terbit(conn, dunia, scope):
    """Negatif: lapis kedua di belakang service, juga untuk INSERT langsung (AC7, AC14)."""
    laporan = buat_laporan(conn, dunia, score_scope=scope, has_dev_scores=True)

    assert _sqlstate(conn, lambda: terbitkan(conn, laporan, dunia)) == CHECK_VIOLATION


def test_status_terbit_butuh_baris_penerbitan(conn, dunia):
    """Negatif: status published tanpa baris published_reports berarti dua sumber kebenaran tak sepakat."""
    laporan = laporan_utuh(conn, dunia)

    assert _sqlstate(conn, lambda: ubah_status(conn, laporan, "published")) == CHECK_VIOLATION


def test_status_digantikan_butuh_versi_pengganti(conn, dunia):
    """Negatif: published → superseded hanya kalau ada versi baru yang menunjuknya."""
    laporan = laporan_utuh(conn, dunia)
    terbitkan(conn, laporan, dunia)
    ubah_status(conn, laporan, "published")

    assert _sqlstate(conn, lambda: ubah_status(conn, laporan, "superseded")) == CHECK_VIOLATION


@pytest.mark.parametrize(
    ("awal", "akhir"),
    [("saved", "superseded"), ("published", "saved"), ("superseded", "published")],
)
def test_status_hanya_maju(conn, dunia, awal, akhir):
    """Negatif: tidak ada lompatan atau mundur; versi lama tidak bisa diterbitkan ulang."""
    lama, baru = laporan_utuh(conn, dunia), laporan_utuh(conn, dunia)
    v1 = terbitkan(conn, lama, dunia)
    if awal != "saved":
        ubah_status(conn, lama, "published")
    if awal == "superseded":
        terbitkan(conn, baru, dunia, version_no=2, supersedes=v1)
        ubah_status(conn, lama, "superseded")

    assert _sqlstate(conn, lambda: ubah_status(conn, lama, akhir)) == CHECK_VIOLATION


def test_status_sama_tidak_dianggap_perubahan(conn, dunia):
    """Edge: UPDATE yang menulis status yang sama tidak ditolak dan tidak mengubah apa pun."""
    laporan = laporan_utuh(conn, dunia)

    ubah_status(conn, laporan, "saved")


def test_hanya_versi_yang_sedang_terbit_bisa_diganti(conn, dunia):
    """Negatif: versi baru tidak boleh menggantikan versi yang laporannya belum terbit."""
    lama, baru = laporan_utuh(conn, dunia), laporan_utuh(conn, dunia)
    v1 = terbitkan(conn, lama, dunia)

    assert (
        _sqlstate(conn, lambda: terbitkan(conn, baru, dunia, version_no=2, supersedes=v1))
        == CHECK_VIOLATION
    )


def test_terbit_dua_kali_ditolak(conn, dunia):
    """Negatif: laporan yang sama tidak punya dua baris terbit."""
    laporan = laporan_utuh(conn, dunia)
    terbitkan(conn, laporan, dunia)

    assert _sqlstate(conn, lambda: terbitkan(conn, laporan, dunia)) == "23505"


def test_id_laporan_tidak_dikenal_ditolak(conn, dunia):
    """Negatif: FK published_reports → reports."""
    assert _sqlstate(conn, lambda: terbitkan(conn, uuid.uuid4(), dunia)) == FOREIGN_KEY_VIOLATION


def test_nomor_versi_pengganti_harus_berikutnya(conn, dunia):
    """Negatif: v1 diganti v3 (melompat) ditolak; nomor versi tanpa celah."""
    lama, baru = laporan_utuh(conn, dunia), laporan_utuh(conn, dunia)
    v1 = terbitkan(conn, lama, dunia)
    ubah_status(conn, lama, "published")

    assert (
        _sqlstate(conn, lambda: terbitkan(conn, baru, dunia, version_no=3, supersedes=v1))
        == CHECK_VIOLATION
    )
