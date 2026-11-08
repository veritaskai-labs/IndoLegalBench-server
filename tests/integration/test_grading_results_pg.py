"""SCRUM-222: grading_runs, grading_results, dan katalog alasan di PostgreSQL. PBI-14 D6.

Satu grading_run = satu kali "Nilai" atau "Nilai ulang" untuk satu sesi (AC7). Run
mengunci versi rubrik yang dipakai (syarat Adra 6 Nov), jadi hasil lama tidak berubah
ketika rubrik baru terbit. Nilai ulang membuat run baru; run lama tetap ada dan hanya
penanda is_latest yang pindah (jawaban klien No. 68 A).

grading_results menyimpan komponen deterministik #107 (CitationScore) per percobaan,
tanpa skor gabungan (keputusan PO K7, AC1). det_score boleh kosong kalau tidak ada
acuan yang bisa dinilai (catatan SA 6 Nov). judge_provider_id ber-FK ke judge_providers
sejak SCRUM-258.

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
    aktifkan,
    beri_alasan,
    buat_hasil,
    buat_percobaan,
    buat_rubrik_lengkap,
    buat_run,
    buat_versi_aktif,
    jalankan_run,
    selesaikan_run,
    ubah_run,
)
from tests.judge_pg import buat_produk_penilai
from tests.measurement_pg import (
    CHECK_VIOLATION,
    FOREIGN_KEY_VIOLATION,
    INSUFFICIENT_PRIVILEGE,
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

KODE_USULAN = {
    ("regulation_not_cited", "deterministic"),
    ("article_not_cited", "deterministic"),
    ("chained_refs_incomplete", "deterministic"),
    ("criterion_unmet", "llm"),
}


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
def versi_id(conn, admin_id) -> uuid.UUID:
    return buat_versi_aktif(conn, admin_id)


@pytest.fixture
def run_id(conn, sesi_id, versi_id, admin_id) -> uuid.UUID:
    run = buat_run(conn, sesi_id, versi_id, admin_id)
    jalankan_run(conn, run)
    return run


@pytest.fixture
def percobaan_id(conn, sesi_id, admin_id) -> uuid.UUID:
    return buat_percobaan(conn, sesi_id, admin_id)


def _run(conn, run_id) -> tuple:
    return tuple(
        conn.execute(
            text(
                "SELECT status::text, is_latest, triggered_at IS NOT NULL, finished_at IS NOT NULL "
                "FROM grading_runs WHERE id = :id"
            ),
            {"id": run_id},
        ).one()
    )


# --- grading_runs: positif ------------------------------------------------------


def test_run_baru_menunggu_dan_belum_terbaru(conn, sesi_id, versi_id, admin_id):
    run = buat_run(conn, sesi_id, versi_id, admin_id)

    assert _run(conn, run) == ("queued", False, True, False)


def test_run_berjalan_lalu_selesai_dan_jadi_terbaru(conn, sesi_id, versi_id, admin_id):
    run = buat_run(conn, sesi_id, versi_id, admin_id)

    selesaikan_run(conn, run)
    ubah_run(conn, run, is_latest=True)

    assert _run(conn, run) == ("finished", True, True, True)


def test_nilai_ulang_memindahkan_penanda_terbaru_tanpa_menghapus_run_lama(
    conn, sesi_id, versi_id, admin_id
):
    """AC7, No. 68 A: run lama tetap ada, hanya is_latest yang pindah."""
    lama = buat_run(conn, sesi_id, versi_id, admin_id)
    selesaikan_run(conn, lama)
    ubah_run(conn, lama, is_latest=True)
    baru = buat_run(conn, sesi_id, versi_id, admin_id, kind="regrade")
    selesaikan_run(conn, baru)

    ubah_run(conn, lama, is_latest=False)
    ubah_run(conn, baru, is_latest=True)

    assert _run(conn, lama) == ("finished", False, True, True)
    assert _run(conn, baru) == ("finished", True, True, True)


def test_run_memakai_versi_yang_sudah_dinonaktifkan(conn, sesi_id, admin_id):
    """Corner: nilai ulang dengan rubrik lama tetap sah selama versinya pernah aktif."""
    versi = buat_versi_aktif(conn, admin_id)
    aktifkan(conn, versi, aktif=False)

    run = buat_run(conn, sesi_id, versi, admin_id)

    assert _run(conn, run)[0] == "queued"


@pytest.mark.parametrize("ada_penilai", [False, True], ids=["kosong", "penilai-terdaftar"])
def test_run_menyimpan_penilai_atau_kosong(conn, sesi_id, versi_id, admin_id, ada_penilai):
    """SCRUM-271: penilai ber-FK ke ai_products (produk otomatis); kosong tetap boleh."""
    penilai = buat_produk_penilai(conn, admin_id) if ada_penilai else None
    run = buat_run(conn, sesi_id, versi_id, admin_id, judge_provider_id=penilai)

    tersimpan = conn.execute(
        text("SELECT judge_provider_id FROM grading_runs WHERE id = :id"), {"id": run}
    ).scalar_one()
    assert tersimpan == penilai


@pytest.mark.parametrize("akhir", ["finished", "failed"])
def test_run_berakhir_selesai_atau_gagal(conn, sesi_id, versi_id, admin_id, akhir):
    run = buat_run(conn, sesi_id, versi_id, admin_id)

    selesaikan_run(conn, run, status=akhir)

    assert _run(conn, run)[0] == akhir


# --- grading_runs: negatif ------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "terbaru", "selesai"),
    [
        ("running", False, False),
        ("finished", False, True),
        ("failed", False, True),
        ("queued", True, False),
    ],
    ids=["langsung-berjalan", "langsung-selesai", "langsung-gagal", "langsung-terbaru"],
)
def test_run_baru_wajib_mulai_dari_antrean(
    conn, sesi_id, versi_id, admin_id, status, terbaru, selesai
):
    """Review Kak Aileen #111 (non-blocking): INSERT tidak boleh melompati status queued.

    finished_at ikut diisi untuk status akhir supaya CHECK ck_grading_runs_finished_at
    lolos, jadi yang menolak memang aturan run baru, bukan CHECK itu.
    """
    sqlstate = sqlstate_penolakan(
        conn,
        "INSERT INTO grading_runs (id, session_id, rubric_version_id, kind, triggered_by, "
        "status, is_latest, finished_at) VALUES (:id, :sesi, :versi, 'initial', :admin, "
        "CAST(:status AS grading_run_status_enum), :terbaru, "
        "CASE WHEN :selesai THEN now() END)",
        {
            "id": uuid.uuid4(),
            "sesi": sesi_id,
            "versi": versi_id,
            "admin": admin_id,
            "status": status,
            "terbaru": terbaru,
            "selesai": selesai,
        },
    )

    assert sqlstate == INSUFFICIENT_PRIVILEGE


def test_run_dengan_versi_yang_belum_pernah_aktif_ditolak(conn, sesi_id, admin_id):
    """Isi versi draf masih bisa bertambah, jadi belum boleh dipakai menilai."""
    draf = buat_rubrik_lengkap(conn, admin_id)

    sqlstate = galat(conn, lambda: buat_run(conn, sesi_id, draf, admin_id)).sqlstate

    assert sqlstate == CHECK_VIOLATION


def test_run_pertama_harus_initial(conn, sesi_id, versi_id, admin_id):
    sqlstate = galat(
        conn, lambda: buat_run(conn, sesi_id, versi_id, admin_id, kind="regrade")
    ).sqlstate

    assert sqlstate == CHECK_VIOLATION


def test_run_kedua_harus_regrade(conn, sesi_id, versi_id, admin_id):
    buat_run(conn, sesi_id, versi_id, admin_id)

    sqlstate = galat(conn, lambda: buat_run(conn, sesi_id, versi_id, admin_id)).sqlstate

    assert sqlstate == CHECK_VIOLATION


def test_dua_run_terbaru_untuk_satu_sesi_ditolak(conn, sesi_id, versi_id, admin_id):
    lama = buat_run(conn, sesi_id, versi_id, admin_id)
    ubah_run(conn, lama, is_latest=True)
    baru = buat_run(conn, sesi_id, versi_id, admin_id, kind="regrade")

    sqlstate = galat(conn, lambda: ubah_run(conn, baru, is_latest=True)).sqlstate

    assert sqlstate == UNIQUE_VIOLATION


def test_run_terbaru_di_sesi_lain_tidak_saling_menghalangi(conn, versi_id, admin_id):
    produk = buat_produk(conn, admin_id)
    sesi_a, sesi_b = buat_sesi(conn, produk, admin_id), buat_sesi(conn, produk, admin_id)
    run_a = buat_run(conn, sesi_a, versi_id, admin_id)
    run_b = buat_run(conn, sesi_b, versi_id, admin_id)

    ubah_run(conn, run_a, is_latest=True)
    ubah_run(conn, run_b, is_latest=True)

    assert _run(conn, run_b)[1] is True


@pytest.mark.parametrize(
    ("dari", "ke"),
    [("finished", "running"), ("failed", "finished"), ("finished", "queued")],
)
def test_run_yang_sudah_berakhir_tidak_bisa_berpindah_status(
    conn, sesi_id, versi_id, admin_id, dari, ke
):
    run = buat_run(conn, sesi_id, versi_id, admin_id)
    selesaikan_run(conn, run, status=dari)

    sqlstate = galat(conn, lambda: ubah_run(conn, run, status=ke)).sqlstate

    assert sqlstate == INSUFFICIENT_PRIVILEGE


def test_run_tidak_bisa_kembali_ke_antrean(conn, run_id):
    sqlstate = galat(conn, lambda: ubah_run(conn, run_id, status="queued")).sqlstate

    assert sqlstate == INSUFFICIENT_PRIVILEGE


def test_run_berakhir_tanpa_waktu_selesai_ditolak(conn, run_id):
    sqlstate = galat(conn, lambda: ubah_run(conn, run_id, status="finished")).sqlstate

    assert sqlstate == CHECK_VIOLATION


@pytest.mark.parametrize(
    "kolom",
    ["rubric_version_id", "session_id", "triggered_by", "judge_provider_id"],
)
def test_identitas_run_tidak_bisa_diubah(conn, run_id, kolom):
    """Nilai baru harus benar-benar beda dan valid untuk FK, supaya yang menolak trigger."""
    if kolom == "triggered_by":
        nilai = buat_admin(conn)
    elif kolom == "judge_provider_id":
        nilai = buat_produk_penilai(conn, buat_admin(conn))
    else:
        nilai = uuid.uuid4()

    sqlstate = sqlstate_penolakan(
        conn,
        f"UPDATE grading_runs SET {kolom} = :nilai WHERE id = :id",
        {"nilai": nilai, "id": run_id},
    )

    assert sqlstate == INSUFFICIENT_PRIVILEGE


@pytest.mark.parametrize(
    "perintah",
    ["DELETE FROM grading_runs WHERE id = :id", "TRUNCATE grading_runs CASCADE"],
    ids=["delete", "truncate"],
)
def test_run_tidak_bisa_dihapus(conn, run_id, perintah):
    assert sqlstate_penolakan(conn, perintah, {"id": run_id}) == INSUFFICIENT_PRIVILEGE


def test_versi_rubrik_yang_dipakai_run_tetap_tidak_bisa_dihapus(conn, run_id, versi_id):
    sqlstate = sqlstate_penolakan(
        conn, "DELETE FROM rubric_versions WHERE id = :id", {"id": versi_id}
    )

    assert sqlstate == INSUFFICIENT_PRIVILEGE


# --- grading_results: positif ---------------------------------------------------


def test_hasil_deterministik_menyimpan_semua_komponen_citation_score(conn, run_id, percobaan_id):
    """AC2, catatan SA 6 Nov: semua field CitationScore disimpan, matches sebagai jsonb."""
    hasil = buat_hasil(conn, run_id, percobaan_id)

    baris = conn.execute(
        text(
            "SELECT det_score, det_regulation_matches->0->>'reference', "
            "jsonb_typeof(det_article_matches), det_all_chained_cited, det_reference_total, "
            "det_reference_correct, det_extracted_total, det_extracted_correct, det_formula, "
            "created_at IS NOT NULL FROM grading_results WHERE id = :id"
        ),
        {"id": hasil},
    ).one()
    assert tuple(baris) == (Decimal("0.75"), "UU 1/2023", "array", True, 4, 3, 5, 3, "recall", True)


def test_skor_kosong_kalau_tidak_ada_acuan_yang_bisa_dinilai(conn, run_id, percobaan_id):
    """SA 6 Nov: hanya KUHPerdata/HIR → skor None, bukan 0. Rangkai pun bisa tak diketahui."""
    hasil = buat_hasil(
        conn,
        run_id,
        percobaan_id,
        det_score=None,
        det_all_chained_cited=None,
        det_reference_total=0,
        det_reference_correct=0,
        det_ungradable_refs='[{"display_text": "KUHPerdata"}]',
    )

    skor = conn.execute(
        text("SELECT det_score FROM grading_results WHERE id = :id"), {"id": hasil}
    ).scalar_one()
    assert skor is None


@pytest.mark.parametrize("skor", [Decimal("0.00"), Decimal("1.00")], ids=["nol", "satu"])
def test_skor_di_batas_nol_dan_satu_diterima(conn, run_id, percobaan_id, skor):
    assert buat_hasil(conn, run_id, percobaan_id, det_score=skor) is not None


def test_gangguan_teknis_disimpan_tanpa_komponen_deterministik(conn, run_id, percobaan_id):
    """AC9: percobaan gagal teknis tetap punya baris hasil, ditandai gangguan."""
    hasil = buat_hasil(conn, run_id, percobaan_id, **HASIL_GANGGUAN)

    baris = conn.execute(
        text(
            "SELECT is_technical_disruption, det_score, det_formula "
            "FROM grading_results WHERE id = :id"
        ),
        {"id": hasil},
    ).one()
    assert tuple(baris) == (True, None, None)


def test_percobaan_yang_sama_dinilai_lagi_di_run_baru(
    conn, sesi_id, versi_id, admin_id, run_id, percobaan_id
):
    """Nilai ulang menulis baris baru; baris run lama tetap."""
    buat_hasil(conn, run_id, percobaan_id)
    baru = buat_run(conn, sesi_id, versi_id, admin_id, kind="regrade")
    jalankan_run(conn, baru)

    buat_hasil(conn, baru, percobaan_id, det_score=Decimal("0.50"))

    jumlah = conn.execute(
        text("SELECT count(*) FROM grading_results WHERE attempt_id = :id"), {"id": percobaan_id}
    ).scalar_one()
    assert jumlah == 2


# --- grading_results: negatif ---------------------------------------------------


@pytest.mark.parametrize(
    "ubah",
    [
        {"det_score": Decimal("1.01")},
        {"det_score": Decimal("-0.01")},
        {"det_reference_correct": 5},
        {"det_extracted_correct": 6},
        {"det_reference_total": -1, "det_reference_correct": -1},
        {"det_formula": "f1"},
        {"det_formula": None},
        {"det_regulation_matches": None},
        {"is_technical_disruption": True},
    ],
    ids=[
        "skor-di-atas-1",
        "skor-negatif",
        "benar-melebihi-acuan",
        "benar-melebihi-ekstrak",
        "jumlah-negatif",
        "rumus-tak-dikenal",
        "rumus-kosong",
        "matches-kosong",
        "gangguan-tapi-ada-komponen",
    ],
)
def test_komponen_deterministik_tidak_sah_ditolak(conn, run_id, percobaan_id, ubah):
    sqlstate = galat(conn, lambda: buat_hasil(conn, run_id, percobaan_id, **ubah)).sqlstate

    assert sqlstate == CHECK_VIOLATION


def test_satu_percobaan_dua_kali_dalam_satu_run_ditolak(conn, run_id, percobaan_id):
    buat_hasil(conn, run_id, percobaan_id)

    sqlstate = galat(conn, lambda: buat_hasil(conn, run_id, percobaan_id)).sqlstate

    assert sqlstate == UNIQUE_VIOLATION


def test_percobaan_dari_sesi_lain_ditolak(conn, run_id, admin_id):
    sesi_lain = buat_sesi(conn, buat_produk(conn, admin_id), admin_id)
    percobaan_lain = buat_percobaan(conn, sesi_lain, admin_id)

    sqlstate = galat(conn, lambda: buat_hasil(conn, run_id, percobaan_lain)).sqlstate

    assert sqlstate == FOREIGN_KEY_VIOLATION


@pytest.mark.parametrize("akhir", ["finished", "failed"])
def test_hasil_baru_ke_run_yang_sudah_berakhir_ditolak(
    conn, sesi_id, versi_id, admin_id, percobaan_id, akhir
):
    run = buat_run(conn, sesi_id, versi_id, admin_id)
    selesaikan_run(conn, run, status=akhir)

    sqlstate = galat(conn, lambda: buat_hasil(conn, run, percobaan_id)).sqlstate

    assert sqlstate == INSUFFICIENT_PRIVILEGE


@pytest.mark.parametrize(
    "perintah",
    [
        "UPDATE grading_results SET det_score = 1 WHERE id = :id",
        "DELETE FROM grading_results WHERE id = :id",
        "TRUNCATE grading_results CASCADE",
    ],
    ids=["update", "delete", "truncate"],
)
def test_hasil_hanya_bisa_ditambah(conn, run_id, percobaan_id, perintah):
    hasil = buat_hasil(conn, run_id, percobaan_id)

    assert sqlstate_penolakan(conn, perintah, {"id": hasil}) == INSUFFICIENT_PRIVILEGE


# --- Katalog alasan (AC5) -------------------------------------------------------


def test_migrasi_mengisi_empat_kode_netral_bertanda_usulan(conn):
    """K8: isi klien belum ada; kode netral ditandai usulan supaya tidak terbaca sebagai daftar klien."""
    baris = conn.execute(
        text(
            "SELECT code, grader_path::text FROM reason_codes "
            "WHERE is_proposed AND is_active AND code = ANY(:kode)"
        ),
        {"kode": [kode for kode, _ in KODE_USULAN]},
    ).all()

    assert {tuple(b) for b in baris} == KODE_USULAN


def test_alasan_ditautkan_ke_hasil(conn, run_id, percobaan_id):
    hasil = buat_hasil(conn, run_id, percobaan_id)

    beri_alasan(conn, hasil, "regulation_not_cited")
    beri_alasan(conn, hasil, "article_not_cited")

    jumlah = conn.execute(
        text("SELECT count(*) FROM grading_result_reasons WHERE grading_result_id = :id"),
        {"id": hasil},
    ).scalar_one()
    assert jumlah == 2


def test_kode_alasan_bisa_dinonaktifkan(conn):
    conn.execute(text("UPDATE reason_codes SET is_active = false WHERE code = 'criterion_unmet'"))

    aktif = conn.execute(
        text("SELECT is_active FROM reason_codes WHERE code = 'criterion_unmet'")
    ).scalar_one()
    assert aktif is False


def test_alasan_ganda_untuk_hasil_yang_sama_ditolak(conn, run_id, percobaan_id):
    hasil = buat_hasil(conn, run_id, percobaan_id)
    beri_alasan(conn, hasil, "regulation_not_cited")

    sqlstate = galat(conn, lambda: beri_alasan(conn, hasil, "regulation_not_cited")).sqlstate

    assert sqlstate == UNIQUE_VIOLATION


def test_kode_alasan_tak_dikenal_ditolak(conn, run_id, percobaan_id):
    hasil = buat_hasil(conn, run_id, percobaan_id)

    sqlstate = galat(conn, lambda: beri_alasan(conn, hasil, "kode_karangan")).sqlstate

    assert sqlstate == FOREIGN_KEY_VIOLATION


def test_kode_alasan_jalur_llm_pada_hasil_deterministik_ditolak(conn, run_id, percobaan_id):
    """Review Kak Aileen #111 (blocking 2): katalog terpisah per jalur (AC5).

    grading_result_reasons menautkan alasan ke hasil deterministik; alasan LLM ada di
    llm_criterion_results. criterion_unmet (jalur llm) tidak boleh masuk ke sini.
    """
    hasil = buat_hasil(conn, run_id, percobaan_id)

    sqlstate = galat(conn, lambda: beri_alasan(conn, hasil, "criterion_unmet")).sqlstate

    assert sqlstate == CHECK_VIOLATION


def test_kode_alasan_nonaktif_tidak_bisa_dipakai(conn, run_id, percobaan_id):
    hasil = buat_hasil(conn, run_id, percobaan_id)
    conn.execute(text("UPDATE reason_codes SET is_active = false WHERE code = 'article_not_cited'"))

    sqlstate = galat(conn, lambda: beri_alasan(conn, hasil, "article_not_cited")).sqlstate

    assert sqlstate == CHECK_VIOLATION


def test_kode_alasan_yang_sudah_dipakai_tidak_bisa_dihapus(conn, run_id, percobaan_id):
    hasil = buat_hasil(conn, run_id, percobaan_id)
    beri_alasan(conn, hasil, "regulation_not_cited")

    sqlstate = sqlstate_penolakan(
        conn, "DELETE FROM reason_codes WHERE code = 'regulation_not_cited'", {}
    )

    assert sqlstate == FOREIGN_KEY_VIOLATION


@pytest.mark.parametrize(
    "perintah",
    [
        "UPDATE grading_result_reasons SET reason_code = 'article_not_cited'",
        "DELETE FROM grading_result_reasons",
        "TRUNCATE grading_result_reasons",
    ],
    ids=["update", "delete", "truncate"],
)
def test_tautan_alasan_hanya_bisa_ditambah(conn, run_id, percobaan_id, perintah):
    hasil = buat_hasil(conn, run_id, percobaan_id)
    beri_alasan(conn, hasil, "regulation_not_cited")

    assert sqlstate_penolakan(conn, perintah, {}) == INSUFFICIENT_PRIVILEGE
