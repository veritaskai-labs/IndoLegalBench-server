"""Bantuan tes PostgreSQL untuk tabel rubrik dan hasil penilaian PBI-14 (SCRUM-222).

Semua isi rubrik di sini sintetis (L3). Rubrik v1 klien tidak boleh masuk repo
publik, jadi nama dimensi, label level, dan prompt sengaja netral.

Fungsi menerima koneksi yang transaksinya di-rollback oleh fixture tes.
"""

import uuid
from decimal import Decimal

from sqlalchemy import Connection, text

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
