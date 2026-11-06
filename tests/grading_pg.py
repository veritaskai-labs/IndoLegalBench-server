"""Bantuan tes PostgreSQL untuk tabel rubrik dan hasil penilaian PBI-14 (SCRUM-222).

Semua isi rubrik di sini sintetis (L3). Rubrik v1 klien tidak boleh masuk repo
publik, jadi nama dimensi, label level, dan prompt sengaja netral.

Fungsi menerima koneksi yang transaksinya di-rollback oleh fixture tes.
"""

import uuid
from decimal import Decimal

from sqlalchemy import Connection, text

from tests.measurement_pg import buat_item_snapshot

ASPEK_SINTETIS = ("Dimensi A", "Dimensi B", "Dimensi C")


def buat_versi(conn: Connection, admin_id: uuid.UUID, **ubah) -> uuid.UUID:
    """Satu versi rubrik yang belum aktif dan belum terkunci."""
    versi_id = uuid.uuid4()
    nilai = {
        "id": versi_id,
        "version_no": 1,
        "effective_date": "2026-10-27",
        "judge_prompt_quality": "Prompt kualitas sintetis.",
        "judge_prompt_format": "Prompt format sintetis.",
        "approved_by_name": None,
        "approved_at": None,
        "created_by": admin_id,
    } | ubah
    conn.execute(
        text(
            "INSERT INTO rubric_versions (id, version_no, effective_date, judge_prompt_quality, "
            "judge_prompt_format, approved_by_name, approved_at, created_by) VALUES (:id, "
            ":version_no, :effective_date, :judge_prompt_quality, :judge_prompt_format, "
            ":approved_by_name, :approved_at, :created_by)"
        ),
        nilai,
    )
    return versi_id


def buat_aspek(
    conn: Connection,
    versi_id: uuid.UUID,
    nama: str,
    urutan: int,
    bobot: Decimal | None = None,
    jalur: str = "llm",
) -> uuid.UUID:
    aspek_id = uuid.uuid4()
    conn.execute(
        text(
            "INSERT INTO rubric_aspects (id, rubric_version_id, name, grader_path, weight, "
            "definition, sort_order) VALUES (:id, :versi, :nama, "
            "CAST(:jalur AS grader_path_enum), :bobot, 'Definisi sintetis.', :urutan)"
        ),
        {
            "id": aspek_id,
            "versi": versi_id,
            "nama": nama,
            "jalur": jalur,
            "bobot": bobot,
            "urutan": urutan,
        },
    )
    return aspek_id


def buat_level(conn: Connection, versi_id: uuid.UUID, skor: int) -> uuid.UUID:
    """Level sintetis; lulus untuk skor 4–5 sesuai jawaban klien No. 48."""
    level_id = uuid.uuid4()
    conn.execute(
        text(
            "INSERT INTO rubric_levels (id, rubric_version_id, score, label, is_passing) "
            "VALUES (:id, :versi, :skor, :label, :lulus)"
        ),
        {
            "id": level_id,
            "versi": versi_id,
            "skor": skor,
            "label": f"Level {skor}",
            "lulus": skor >= 4,
        },
    )
    return level_id


def buat_deskriptor(conn: Connection, level_id: uuid.UUID, aspek_id: uuid.UUID) -> None:
    conn.execute(
        text(
            "INSERT INTO rubric_level_descriptors (rubric_level_id, rubric_aspect_id, descriptor) "
            "VALUES (:level, :aspek, 'Deskriptor sintetis.')"
        ),
        {"level": level_id, "aspek": aspek_id},
    )


def buat_rubrik_lengkap(
    conn: Connection,
    admin_id: uuid.UUID,
    bobot: tuple[Decimal | None, ...] = (None, None, None),
    **ubah,
) -> uuid.UUID:
    """Versi dengan 3 aspek, level 0–5, dan deskriptor per level per aspek (bentuk rubrik holistik)."""
    versi_id = buat_versi(conn, admin_id, **ubah)
    aspek = [
        buat_aspek(conn, versi_id, nama, urutan, bobot[urutan - 1])
        for urutan, nama in enumerate(ASPEK_SINTETIS, start=1)
    ]
    for skor in range(6):
        level_id = buat_level(conn, versi_id, skor)
        for aspek_id in aspek:
            buat_deskriptor(conn, level_id, aspek_id)
    return versi_id


def aktifkan(conn: Connection, versi_id: uuid.UUID, aktif: bool = True) -> None:
    conn.execute(
        text("UPDATE rubric_versions SET is_active = :aktif WHERE id = :id"),
        {"aktif": aktif, "id": versi_id},
    )


def buat_versi_aktif(conn: Connection, admin_id: uuid.UUID, **ubah) -> uuid.UUID:
    versi_id = buat_rubrik_lengkap(conn, admin_id, **ubah)
    aktifkan(conn, versi_id)
    return versi_id


def buat_percobaan(conn: Connection, sesi_id: uuid.UUID, admin_id: uuid.UUID) -> uuid.UUID:
    """Satu percobaan otomatis untuk satu kasus baru di snapshot sesi."""
    item_id = buat_item_snapshot(conn, sesi_id, admin_id)
    return conn.execute(
        text(
            "INSERT INTO attempts (id, session_id, snapshot_item_id, repetition_no, source) "
            "VALUES (:id, :sesi, :item, 1, 'automatic') RETURNING id"
        ),
        {"id": uuid.uuid4(), "sesi": sesi_id, "item": item_id},
    ).scalar_one()


def buat_run(
    conn: Connection,
    sesi_id: uuid.UUID,
    versi_id: uuid.UUID,
    admin_id: uuid.UUID,
    **ubah,
) -> uuid.UUID:
    nilai = {
        "id": uuid.uuid4(),
        "session_id": sesi_id,
        "rubric_version_id": versi_id,
        "judge_provider_id": None,
        "kind": "initial",
        "triggered_by": admin_id,
    } | ubah
    conn.execute(
        text(
            "INSERT INTO grading_runs (id, session_id, rubric_version_id, judge_provider_id, kind, "
            "triggered_by) VALUES (:id, :session_id, :rubric_version_id, :judge_provider_id, "
            "CAST(:kind AS grading_run_kind_enum), :triggered_by)"
        ),
        nilai,
    )
    return nilai["id"]


def ubah_run(conn: Connection, run_id: uuid.UUID, **kolom) -> None:
    """UPDATE grading_runs; status di-cast ke enum-nya."""
    bagian = [
        f"{nama} = CAST(:{nama} AS grading_run_status_enum)"
        if nama == "status"
        else f"{nama} = :{nama}"
        for nama in kolom
    ]
    conn.execute(
        text(f"UPDATE grading_runs SET {', '.join(bagian)} WHERE id = :id"), {"id": run_id} | kolom
    )


def jalankan_run(conn: Connection, run_id: uuid.UUID) -> None:
    ubah_run(conn, run_id, status="running")


def selesaikan_run(conn: Connection, run_id: uuid.UUID, status: str = "finished") -> None:
    jalankan_run(conn, run_id)
    conn.execute(
        text(
            "UPDATE grading_runs SET status = CAST(:status AS grading_run_status_enum), "
            "finished_at = now() WHERE id = :id"
        ),
        {"id": run_id, "status": status},
    )


HASIL_DET = {
    "is_technical_disruption": False,
    "det_score": Decimal("0.75"),
    "det_regulation_matches": '[{"reference": "UU 1/2023", "matched": true}]',
    "det_article_matches": '[{"reference": "Pasal 1", "matched": false}]',
    "det_ungradable_refs": "[]",
    "det_all_chained_cited": True,
    "det_reference_total": 4,
    "det_reference_correct": 3,
    "det_extracted_total": 5,
    "det_extracted_correct": 3,
    "det_formula": "recall",
}

HASIL_GANGGUAN = dict.fromkeys(HASIL_DET) | {"is_technical_disruption": True}

_KOLOM_HASIL = ", ".join(HASIL_DET)
_NILAI_HASIL = ", ".join(
    f"CAST(:{kolom} AS jsonb)" if kolom.endswith(("_matches", "_refs")) else f":{kolom}"
    for kolom in HASIL_DET
)


def buat_hasil(conn: Connection, run_id: uuid.UUID, percobaan_id: uuid.UUID, **ubah) -> uuid.UUID:
    """Hasil deterministik satu percobaan; ubah menimpa kolom HASIL_DET."""
    hasil_id = uuid.uuid4()
    conn.execute(
        text(
            f"INSERT INTO grading_results (id, grading_run_id, attempt_id, {_KOLOM_HASIL}) "
            f"VALUES (:id, :run, :percobaan, {_NILAI_HASIL})"
        ),
        {"id": hasil_id, "run": run_id, "percobaan": percobaan_id} | HASIL_DET | ubah,
    )
    return hasil_id


def beri_alasan(conn: Connection, hasil_id: uuid.UUID, kode: str) -> None:
    conn.execute(
        text(
            "INSERT INTO grading_result_reasons (grading_result_id, reason_code) "
            "VALUES (:hasil, :kode)"
        ),
        {"hasil": hasil_id, "kode": kode},
    )
