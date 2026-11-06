"""SCRUM-222: rubrik berversi di PostgreSQL. PBI-14 AC3, AC7, D6.

Keputusan PO 6 Nov (brief K1, K10): level 0–5 disimpan sebagai data dengan penanda
lulus, dimensi rubrik disimpan sebagai rubric_aspects dengan bobot boleh kosong
(kalau terisi wajib total 100), dan penyetuju berupa teks nama + tanggal.

Isi versi terkunci sejak pertama kali diaktifkan: perubahan rubrik selalu membuat
versi baru, jadi hasil penilaian lama tetap merujuk ke rubrik yang sama (AC7).
Setiap tes berjalan di dalam transaksi yang di-rollback. Dilewati kalau
TEST_POSTGRES_URL kosong.
"""

import os
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, text

from tests.grading_pg import (
    aktifkan,
    buat_aspek,
    buat_deskriptor,
    buat_level,
    buat_rubrik_lengkap,
    buat_versi,
)
from tests.measurement_pg import (
    CHECK_VIOLATION,
    FOREIGN_KEY_VIOLATION,
    INSUFFICIENT_PRIVILEGE,
    INVALID_TEXT_REPRESENTATION,
    UNIQUE_VIOLATION,
    buat_admin,
    galat,
    sqlstate_penolakan,
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
def admin_id(conn) -> uuid.UUID:
    return buat_admin(conn)


def _versi(conn, versi_id) -> tuple:
    return tuple(
        conn.execute(
            text("SELECT is_active, locked_at IS NOT NULL FROM rubric_versions WHERE id = :id"),
            {"id": versi_id},
        ).one()
    )


def _aktifkan_ditolak(conn, versi_id) -> str:
    return galat(conn, lambda: aktifkan(conn, versi_id)).sqlstate


# --- Positif -----------------------------------------------------------------


def test_versi_baru_belum_aktif_dan_belum_terkunci(conn, admin_id):
    versi_id = buat_rubrik_lengkap(conn, admin_id)

    baris = conn.execute(
        text(
            "SELECT is_active, locked_at, approved_by_name, approved_at, created_at IS NOT NULL "
            "FROM rubric_versions WHERE id = :id"
        ),
        {"id": versi_id},
    ).one()
    assert tuple(baris) == (False, None, None, None, True)


def test_rubrik_holistik_tanpa_bobot_bisa_diaktifkan_dan_terkunci(conn, admin_id):
    """K1: rubrik v1 klien tidak punya bobot. Semua bobot kosong tetap sah."""
    versi_id = buat_rubrik_lengkap(conn, admin_id)

    aktifkan(conn, versi_id)

    assert _versi(conn, versi_id) == (True, True)


def test_bobot_total_tepat_100_bisa_diaktifkan(conn, admin_id):
    bobot = (Decimal("33.33"), Decimal("33.33"), Decimal("33.34"))
    versi_id = buat_rubrik_lengkap(conn, admin_id, bobot=bobot)

    aktifkan(conn, versi_id)

    assert _versi(conn, versi_id) == (True, True)


def test_penyetuju_berupa_teks_nama_dan_tanggal(conn, admin_id):
    """K10: penyetuju dari tim hukum klien bukan akun aplikasi."""
    versi_id = buat_versi(
        conn, admin_id, approved_by_name="Penyetuju Sintetis", approved_at="2026-10-27"
    )

    baris = conn.execute(
        text("SELECT approved_by_name, approved_at::text FROM rubric_versions WHERE id = :id"),
        {"id": versi_id},
    ).one()
    assert tuple(baris) == ("Penyetuju Sintetis", "2026-10-27")


def test_ganti_versi_aktif_lewat_nonaktifkan_dulu(conn, admin_id):
    """Corner: v1 dinonaktifkan, v2 diaktifkan. v1 tetap terkunci untuk riwayat."""
    v1 = buat_rubrik_lengkap(conn, admin_id, version_no=1)
    v2 = buat_rubrik_lengkap(conn, admin_id, version_no=2)
    aktifkan(conn, v1)

    aktifkan(conn, v1, aktif=False)
    aktifkan(conn, v2)

    assert _versi(conn, v1) == (False, True)
    assert _versi(conn, v2) == (True, True)


def test_versi_lama_bisa_diaktifkan_kembali(conn, admin_id):
    versi_id = buat_rubrik_lengkap(conn, admin_id)
    aktifkan(conn, versi_id)
    aktifkan(conn, versi_id, aktif=False)

    aktifkan(conn, versi_id)

    assert _versi(conn, versi_id) == (True, True)


# --- Negatif: aktivasi ---------------------------------------------------------


def test_dua_versi_aktif_sekaligus_ditolak(conn, admin_id):
    v1 = buat_rubrik_lengkap(conn, admin_id, version_no=1)
    v2 = buat_rubrik_lengkap(conn, admin_id, version_no=2)
    aktifkan(conn, v1)

    assert _aktifkan_ditolak(conn, v2) == UNIQUE_VIOLATION


@pytest.mark.parametrize(
    "bobot",
    [
        (Decimal("30"), Decimal("30"), Decimal("30")),
        (Decimal("50"), Decimal("50"), Decimal("0.01")),
        (Decimal("50"), Decimal("50"), None),
    ],
    ids=["total-90", "total-100.01", "campur-kosong"],
)
def test_bobot_tidak_total_100_atau_campur_kosong_ditolak_saat_aktivasi(conn, admin_id, bobot):
    versi_id = buat_rubrik_lengkap(conn, admin_id, bobot=bobot)

    assert _aktifkan_ditolak(conn, versi_id) == CHECK_VIOLATION


def test_versi_tanpa_aspek_ditolak_saat_aktivasi(conn, admin_id):
    versi_id = buat_versi(conn, admin_id)
    buat_level(conn, versi_id, 5)

    assert _aktifkan_ditolak(conn, versi_id) == CHECK_VIOLATION


def test_versi_tanpa_level_ditolak_saat_aktivasi(conn, admin_id):
    versi_id = buat_versi(conn, admin_id)
    buat_aspek(conn, versi_id, "Dimensi A", 1)

    assert _aktifkan_ditolak(conn, versi_id) == CHECK_VIOLATION


# --- Negatif: immutabilitas (AC7) ------------------------------------------------


@pytest.mark.parametrize(
    "perubahan",
    [
        "judge_prompt_quality = 'Prompt lain.'",
        "version_no = 9",
        "approved_by_name = 'Nama lain'",
        "locked_at = NULL",
    ],
    ids=["prompt", "nomor", "penyetuju", "buka-kunci"],
)
def test_isi_versi_tidak_bisa_diubah_bahkan_sebelum_aktif(conn, admin_id, perubahan):
    versi_id = buat_rubrik_lengkap(conn, admin_id)

    sqlstate = sqlstate_penolakan(
        conn, f"UPDATE rubric_versions SET {perubahan} WHERE id = :id", {"id": versi_id}
    )

    assert sqlstate == INSUFFICIENT_PRIVILEGE


@pytest.mark.parametrize(
    "perintah",
    ["DELETE FROM rubric_versions WHERE id = :id", "TRUNCATE rubric_versions CASCADE"],
    ids=["delete", "truncate"],
)
def test_versi_tidak_bisa_dihapus(conn, admin_id, perintah):
    versi_id = buat_rubrik_lengkap(conn, admin_id)

    assert sqlstate_penolakan(conn, perintah, {"id": versi_id}) == INSUFFICIENT_PRIVILEGE


@pytest.mark.parametrize("nonaktif_dulu", [False, True], ids=["masih-aktif", "sudah-nonaktif"])
def test_isi_baru_ke_versi_terkunci_ditolak(conn, admin_id, nonaktif_dulu):
    versi_id = buat_rubrik_lengkap(conn, admin_id)
    aktifkan(conn, versi_id)
    if nonaktif_dulu:
        aktifkan(conn, versi_id, aktif=False)

    sqlstate = galat(conn, lambda: buat_aspek(conn, versi_id, "Dimensi D", 4)).sqlstate
    assert sqlstate == INSUFFICIENT_PRIVILEGE
    sqlstate = galat(conn, lambda: buat_level(conn, versi_id, 3)).sqlstate
    assert sqlstate == INSUFFICIENT_PRIVILEGE


def test_deskriptor_baru_ke_versi_terkunci_ditolak(conn, admin_id):
    versi_id = buat_versi(conn, admin_id)
    aspek_id = buat_aspek(conn, versi_id, "Dimensi A", 1)
    level_id = buat_level(conn, versi_id, 5)
    aktifkan(conn, versi_id)

    sqlstate = galat(conn, lambda: buat_deskriptor(conn, level_id, aspek_id)).sqlstate

    assert sqlstate == INSUFFICIENT_PRIVILEGE


@pytest.mark.parametrize(
    "perintah",
    [
        "UPDATE rubric_aspects SET weight = 10 WHERE rubric_version_id = :id",
        "DELETE FROM rubric_aspects WHERE rubric_version_id = :id",
        "TRUNCATE rubric_aspects CASCADE",
        "UPDATE rubric_levels SET is_passing = true WHERE rubric_version_id = :id",
        "DELETE FROM rubric_levels WHERE rubric_version_id = :id",
        "TRUNCATE rubric_levels CASCADE",
        "UPDATE rubric_level_descriptors SET descriptor = 'Lain.'",
        "DELETE FROM rubric_level_descriptors",
        "TRUNCATE rubric_level_descriptors",
    ],
    ids=[
        "aspek-update",
        "aspek-delete",
        "aspek-truncate",
        "level-update",
        "level-delete",
        "level-truncate",
        "deskriptor-update",
        "deskriptor-delete",
        "deskriptor-truncate",
    ],
)
def test_isi_rubrik_hanya_bisa_ditambah(conn, admin_id, perintah):
    """Belum aktif pun tidak bisa diubah: salah ketik diperbaiki dengan versi baru."""
    versi_id = buat_rubrik_lengkap(conn, admin_id)

    assert sqlstate_penolakan(conn, perintah, {"id": versi_id}) == INSUFFICIENT_PRIVILEGE


# --- Negatif: aturan kolom -----------------------------------------------------


def test_deskriptor_dengan_level_dari_versi_lain_ditolak(conn, admin_id):
    v1 = buat_versi(conn, admin_id, version_no=1)
    v2 = buat_versi(conn, admin_id, version_no=2)
    aspek_v1 = buat_aspek(conn, v1, "Dimensi A", 1)
    level_v2 = buat_level(conn, v2, 5)

    sqlstate = galat(conn, lambda: buat_deskriptor(conn, level_v2, aspek_v1)).sqlstate

    assert sqlstate == FOREIGN_KEY_VIOLATION


@pytest.mark.parametrize("skor", [-1, 6])
def test_skor_level_di_luar_0_sampai_5_ditolak(conn, admin_id, skor):
    versi_id = buat_versi(conn, admin_id)

    assert galat(conn, lambda: buat_level(conn, versi_id, skor)).sqlstate == CHECK_VIOLATION


def test_skor_level_ganda_dalam_satu_versi_ditolak(conn, admin_id):
    versi_id = buat_versi(conn, admin_id)
    buat_level(conn, versi_id, 4)

    assert galat(conn, lambda: buat_level(conn, versi_id, 4)).sqlstate == UNIQUE_VIOLATION


@pytest.mark.parametrize("bobot", [Decimal("0"), Decimal("-1"), Decimal("100.01")])
def test_bobot_di_luar_batas_ditolak(conn, admin_id, bobot):
    versi_id = buat_versi(conn, admin_id)

    sqlstate = galat(conn, lambda: buat_aspek(conn, versi_id, "Dimensi A", 1, bobot)).sqlstate

    assert sqlstate == CHECK_VIOLATION


def test_nama_aspek_ganda_dalam_satu_versi_ditolak(conn, admin_id):
    versi_id = buat_versi(conn, admin_id)
    buat_aspek(conn, versi_id, "Dimensi A", 1)

    sqlstate = galat(conn, lambda: buat_aspek(conn, versi_id, "Dimensi A", 2)).sqlstate

    assert sqlstate == UNIQUE_VIOLATION


def test_nomor_versi_ganda_ditolak(conn, admin_id):
    buat_versi(conn, admin_id, version_no=1)

    sqlstate = galat(conn, lambda: buat_versi(conn, admin_id, version_no=1)).sqlstate

    assert sqlstate == UNIQUE_VIOLATION


@pytest.mark.parametrize(
    "ubah",
    [
        {"version_no": 0},
        {"judge_prompt_quality": "   "},
        {"judge_prompt_format": ""},
        {"approved_by_name": " "},
    ],
    ids=["nomor-0", "prompt-kualitas-spasi", "prompt-format-kosong", "penyetuju-spasi"],
)
def test_kolom_versi_tidak_sah_ditolak(conn, admin_id, ubah):
    assert galat(conn, lambda: buat_versi(conn, admin_id, **ubah)).sqlstate == CHECK_VIOLATION


def test_jalur_penilai_di_luar_enum_ditolak(conn, admin_id):
    versi_id = buat_versi(conn, admin_id)

    sqlstate = galat(
        conn, lambda: buat_aspek(conn, versi_id, "Dimensi A", 1, jalur="manual")
    ).sqlstate

    assert sqlstate == INVALID_TEXT_REPRESENTATION


@pytest.mark.parametrize(
    ("aktif", "terkunci"),
    [(True, True), (False, True), (True, False)],
    ids=["aktif-dan-terkunci", "terkunci-saja", "aktif-saja"],
)
def test_versi_baru_tidak_bisa_langsung_aktif_atau_terkunci(conn, admin_id, aktif, terkunci):
    """Aktivasi hanya lewat UPDATE, supaya cek aspek, level, dan bobot tidak bisa dilewati.

    Tanpa aturan ini, INSERT dengan is_active dan locked_at yang diisi sendiri membuat
    versi aktif yang kosong: isinya pun tidak bisa ditambah lagi karena sudah terkunci.
    """
    sqlstate = sqlstate_penolakan(
        conn,
        "INSERT INTO rubric_versions (id, version_no, effective_date, is_active, locked_at, "
        "judge_prompt_quality, judge_prompt_format, created_by) VALUES (:id, 1, '2026-10-27', "
        ":aktif, CASE WHEN :terkunci THEN now() END, 'Prompt kualitas sintetis.', "
        "'Prompt format sintetis.', :admin)",
        {"id": uuid.uuid4(), "aktif": aktif, "terkunci": terkunci, "admin": admin_id},
    )

    assert sqlstate == INSUFFICIENT_PRIVILEGE
