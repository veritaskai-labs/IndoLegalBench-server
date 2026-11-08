"""SCRUM-222: hasil penilai LLM per pengulangan per jenis di PostgreSQL. PBI-14 AC3, AC5, AC6.

Bentuknya mengikuti #108 (JudgeRow): satu baris per (pengulangan, jenis quality/format)
dengan prompt, kriteria, skor, penjelasan, error, dan biaya sendiri (keputusan PO K3).
criteria_score 0–1 adalah "skor LLM" di tabel hasil; rubric_level 0–5 disimpan terpisah
dan hanya di baris quality (keputusan PO K1/K2). Tidak ada kolom jebakan (AC4).
Kurs disimpan sebagai usd_idr_rate seperti call_logs, bukan FK fx_rates.

Setiap tes berjalan di dalam transaksi yang di-rollback. Dilewati kalau
TEST_POSTGRES_URL kosong.
"""

import os
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, text

from tests.grading_pg import (
    HASIL_GANGGUAN,
    JURI_GAGAL,
    buat_baris_juri,
    buat_hasil,
    buat_kriteria,
    buat_percobaan,
    buat_run,
    buat_versi_aktif,
    jalankan_run,
    ubah_run,
)
from tests.measurement_pg import (
    CHECK_VIOLATION,
    FOREIGN_KEY_VIOLATION,
    INSUFFICIENT_PRIVILEGE,
    INVALID_TEXT_REPRESENTATION,
    UNIQUE_VIOLATION,
    buat_admin,
    buat_produk,
    buat_sesi,
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


@pytest.fixture
def sesi_id(conn, admin_id) -> uuid.UUID:
    return buat_sesi(conn, buat_produk(conn, admin_id), admin_id)


@pytest.fixture
def run_id(conn, sesi_id, admin_id) -> uuid.UUID:
    run = buat_run(conn, sesi_id, buat_versi_aktif(conn, admin_id), admin_id)
    jalankan_run(conn, run)
    return run


@pytest.fixture
def hasil_id(conn, run_id, sesi_id, admin_id) -> uuid.UUID:
    return buat_hasil(conn, run_id, buat_percobaan(conn, sesi_id, admin_id))


@pytest.fixture
def baris_id(conn, hasil_id) -> uuid.UUID:
    return buat_baris_juri(conn, hasil_id)


def _tutup_run(conn, run_id) -> None:
    ubah_run(conn, run_id, status="finished", finished_at="2026-11-06T12:00:00+07:00")


# --- Baris penilai: positif ---------------------------------------------------


def test_baris_quality_menyimpan_skor_kriteria_biaya_dan_token(conn, baris_id):
    baris = conn.execute(
        text(
            "SELECT kind::text, status::text, criteria_met, criteria_total, criteria_score, "
            "cost_idr, usd_idr_rate, input_tokens, output_tokens, raw_response->>'text', "
            "created_at IS NOT NULL FROM llm_judge_results WHERE id = :id"
        ),
        {"id": baris_id},
    ).one()

    assert tuple(baris) == (
        "quality",
        "succeeded",
        3,
        4,
        Decimal("0.75"),
        Decimal("85.50"),
        Decimal("16250.0000"),
        1200,
        300,
        "jawaban penilai sintetis",
        True,
    )


def test_quality_dan_format_per_pengulangan_jadi_baris_terpisah(conn, hasil_id):
    """K3: satu baris per (pengulangan, jenis), sesuai #108."""
    for nomor in (1, 2, 3):
        for jenis in ("quality", "format"):
            buat_baris_juri(conn, hasil_id, judge_run_no=nomor, kind=jenis)

    jumlah = conn.execute(
        text("SELECT count(*) FROM llm_judge_results WHERE grading_result_id = :id"),
        {"id": hasil_id},
    ).scalar_one()
    assert jumlah == 6


@pytest.mark.parametrize("level", [0, 5])
def test_level_rubrik_0_sampai_5_di_baris_quality(conn, hasil_id, level):
    """K1/K2: skor 0–5 disimpan terpisah dari skor kriteria."""
    baris = buat_baris_juri(conn, hasil_id, rubric_level=level)

    tersimpan = conn.execute(
        text("SELECT rubric_level FROM llm_judge_results WHERE id = :id"), {"id": baris}
    ).scalar_one()
    assert tersimpan == level


def test_panggilan_gagal_tersimpan_tanpa_skor_dengan_biayanya(conn, hasil_id):
    """AC6: panggilan gagal tetap tercatat dengan biaya; skornya kosong, bukan 0."""
    baris = buat_baris_juri(conn, hasil_id, **JURI_GAGAL)

    hasil = conn.execute(
        text(
            "SELECT status::text, criteria_score, failure_kind, cost_idr "
            "FROM llm_judge_results WHERE id = :id"
        ),
        {"id": baris},
    ).one()
    assert tuple(hasil) == ("failed", None, "invalid_response", Decimal("85.50"))


@pytest.mark.parametrize("skor", [Decimal("0.00"), Decimal("1.00")], ids=["nol", "satu"])
def test_skor_kriteria_di_batas_diterima(conn, hasil_id, skor):
    met = 0 if skor == 0 else 4
    assert buat_baris_juri(conn, hasil_id, criteria_score=skor, criteria_met=met) is not None


# --- Baris penilai: negatif ---------------------------------------------------


def test_pengulangan_dan_jenis_yang_sama_dua_kali_ditolak(conn, hasil_id):
    buat_baris_juri(conn, hasil_id)

    assert galat(conn, lambda: buat_baris_juri(conn, hasil_id)).sqlstate == UNIQUE_VIOLATION


@pytest.mark.parametrize(
    "ubah",
    [
        {"judge_run_no": 0},
        {"criteria_score": Decimal("1.01")},
        {"criteria_score": Decimal("-0.01")},
        {"criteria_met": 5},
        {"criteria_met": -1},
        {"rubric_level": 6},
        {"rubric_level": -1},
        {"kind": "format", "rubric_level": 3},
        {"criteria_score": None},
        {"failure_kind": "timeout"},
        {"input_tokens": -1},
        {"cost_idr": Decimal("-0.01")},
        {"usd_idr_rate": Decimal("0")},
        {"prompt": "   "},
    ],
    ids=[
        "pengulangan-0",
        "skor-di-atas-1",
        "skor-negatif",
        "terpenuhi-melebihi-total",
        "terpenuhi-negatif",
        "level-6",
        "level-negatif",
        "level-di-baris-format",
        "berhasil-tanpa-skor",
        "berhasil-tapi-ada-kegagalan",
        "token-negatif",
        "biaya-negatif",
        "kurs-nol",
        "prompt-kosong",
    ],
)
def test_kolom_baris_penilai_tidak_sah_ditolak(conn, hasil_id, ubah):
    sqlstate = galat(conn, lambda: buat_baris_juri(conn, hasil_id, **ubah)).sqlstate

    assert sqlstate == CHECK_VIOLATION


@pytest.mark.parametrize(
    "ubah",
    [{"criteria_score": Decimal("0.50")}, {"rubric_level": 2}, {"failure_kind": None}],
    ids=["gagal-tapi-ada-skor", "gagal-tapi-ada-level", "gagal-tanpa-jenis-kegagalan"],
)
def test_baris_gagal_yang_tidak_konsisten_ditolak(conn, hasil_id, ubah):
    sqlstate = galat(conn, lambda: buat_baris_juri(conn, hasil_id, **JURI_GAGAL | ubah)).sqlstate

    assert sqlstate == CHECK_VIOLATION


def test_jenis_jebakan_tidak_ada(conn, hasil_id):
    """AC4: klien menghapus penilaian jebakan, jadi jenis 'trap' tidak ada di enum."""
    sqlstate = galat(conn, lambda: buat_baris_juri(conn, hasil_id, kind="trap")).sqlstate

    assert sqlstate == INVALID_TEXT_REPRESENTATION


def test_penilai_untuk_percobaan_gangguan_teknis_ditolak(conn, run_id, sesi_id, admin_id):
    """AC9: percobaan gagal teknis tidak punya jawaban untuk dinilai."""
    gangguan = buat_hasil(conn, run_id, buat_percobaan(conn, sesi_id, admin_id), **HASIL_GANGGUAN)

    sqlstate = galat(conn, lambda: buat_baris_juri(conn, gangguan)).sqlstate

    assert sqlstate == CHECK_VIOLATION


def test_panggilan_gagal_berbiaya_untuk_percobaan_gangguan_diterima(
    conn, run_id, sesi_id, admin_id
):
    """AC6: panggilan penilai yang gagal tapi sudah memakai token tetap boleh dicatat.

    Percobaan menjadi gangguan teknis karena penilainya gagal. Biaya panggilan itu
    harus bisa disimpan supaya masuk ke pengeluaran bulanan produk penilai.
    """
    gangguan = buat_hasil(conn, run_id, buat_percobaan(conn, sesi_id, admin_id), **HASIL_GANGGUAN)

    baris = buat_baris_juri(conn, gangguan, **JURI_GAGAL)

    hasil = conn.execute(
        text("SELECT status::text, cost_idr FROM llm_judge_results WHERE id = :id"),
        {"id": baris},
    ).one()
    assert tuple(hasil) == ("failed", Decimal("85.50"))


def test_baris_baru_ke_run_yang_sudah_berakhir_ditolak(conn, run_id, hasil_id):
    _tutup_run(conn, run_id)

    sqlstate = galat(conn, lambda: buat_baris_juri(conn, hasil_id)).sqlstate

    assert sqlstate == INSUFFICIENT_PRIVILEGE


@pytest.mark.parametrize(
    "perintah",
    [
        "UPDATE llm_judge_results SET criteria_score = 1 WHERE id = :id",
        "DELETE FROM llm_judge_results WHERE id = :id",
        "TRUNCATE llm_judge_results CASCADE",
    ],
    ids=["update", "delete", "truncate"],
)
def test_baris_penilai_hanya_bisa_ditambah(conn, baris_id, perintah):
    assert sqlstate_penolakan(conn, perintah, {"id": baris_id}) == INSUFFICIENT_PRIVILEGE


# --- Kriteria: positif --------------------------------------------------------


def test_kriteria_terpenuhi_dan_tidak_terpenuhi_dengan_alasan(conn, baris_id):
    """AC3, AC5: kode 'unmet' #108 diganti criterion_unmet dari katalog."""
    buat_kriteria(conn, baris_id, "must_contain:0", terpenuhi=True)
    buat_kriteria(conn, baris_id, "must_not_contain:0", terpenuhi=False, kode="criterion_unmet")

    baris = conn.execute(
        text(
            "SELECT criterion_key, is_met, reason_code FROM llm_criterion_results "
            "WHERE llm_judge_result_id = :id ORDER BY criterion_key"
        ),
        {"id": baris_id},
    ).all()
    assert [tuple(b) for b in baris] == [
        ("must_contain:0", True, None),
        ("must_not_contain:0", False, "criterion_unmet"),
    ]


# --- Kriteria: negatif --------------------------------------------------------


def test_kriteria_yang_sama_dua_kali_ditolak(conn, baris_id):
    buat_kriteria(conn, baris_id)

    assert galat(conn, lambda: buat_kriteria(conn, baris_id)).sqlstate == UNIQUE_VIOLATION


def test_alasan_pada_kriteria_yang_terpenuhi_ditolak(conn, baris_id):
    sqlstate = galat(
        conn, lambda: buat_kriteria(conn, baris_id, terpenuhi=True, kode="criterion_unmet")
    ).sqlstate

    assert sqlstate == CHECK_VIOLATION


def test_alasan_jalur_deterministik_pada_kriteria_llm_ditolak(conn, baris_id):
    """Katalog terpisah per jalur (AC5)."""
    sqlstate = galat(
        conn,
        lambda: buat_kriteria(conn, baris_id, terpenuhi=False, kode="regulation_not_cited"),
    ).sqlstate

    assert sqlstate == CHECK_VIOLATION


def test_alasan_nonaktif_pada_kriteria_ditolak(conn, baris_id):
    conn.execute(text("UPDATE reason_codes SET is_active = false WHERE code = 'criterion_unmet'"))

    sqlstate = galat(
        conn, lambda: buat_kriteria(conn, baris_id, terpenuhi=False, kode="criterion_unmet")
    ).sqlstate

    assert sqlstate == CHECK_VIOLATION


def test_alasan_tak_dikenal_pada_kriteria_ditolak(conn, baris_id):
    sqlstate = galat(
        conn, lambda: buat_kriteria(conn, baris_id, terpenuhi=False, kode="unmet")
    ).sqlstate

    assert sqlstate == FOREIGN_KEY_VIOLATION


def test_kriteria_untuk_panggilan_gagal_ditolak(conn, hasil_id):
    """Panggilan gagal tidak menghasilkan penilaian kriteria."""
    gagal = buat_baris_juri(conn, hasil_id, **JURI_GAGAL)

    assert galat(conn, lambda: buat_kriteria(conn, gagal)).sqlstate == CHECK_VIOLATION


def test_kriteria_baru_ke_run_yang_sudah_berakhir_ditolak(conn, run_id, baris_id):
    _tutup_run(conn, run_id)

    assert galat(conn, lambda: buat_kriteria(conn, baris_id)).sqlstate == INSUFFICIENT_PRIVILEGE


@pytest.mark.parametrize(
    "perintah",
    [
        "UPDATE llm_criterion_results SET is_met = false",
        "DELETE FROM llm_criterion_results",
        "TRUNCATE llm_criterion_results",
    ],
    ids=["update", "delete", "truncate"],
)
def test_kriteria_hanya_bisa_ditambah(conn, baris_id, perintah):
    buat_kriteria(conn, baris_id)

    assert sqlstate_penolakan(conn, perintah, {}) == INSUFFICIENT_PRIVILEGE
