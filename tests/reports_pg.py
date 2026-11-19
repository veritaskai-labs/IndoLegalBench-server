"""Bantuan tes PostgreSQL untuk tabel laporan SCRUM-300 (D1). Data sintetis (L3).

Semua fungsi menerima koneksi yang transaksinya di-rollback oleh fixture tes, jadi
laporan yang dibuat di sini tidak pernah tersimpan (dan trigger "tidak bisa dihapus"
tidak perlu dilonggarkan untuk membersihkannya).
"""

import json
import uuid
from dataclasses import dataclass

from sqlalchemy import Connection, text

from tests.grading_pg import buat_versi
from tests.measurement_pg import buat_admin, buat_kasus, buat_produk, buat_sesi


@dataclass(frozen=True)
class Dunia:
    admin: uuid.UUID
    rubrik: uuid.UUID
    produk_a: uuid.UUID
    produk_b: uuid.UUID
    sesi_a: uuid.UUID
    sesi_b: uuid.UUID


def siapkan(conn: Connection) -> Dunia:
    admin = buat_admin(conn)
    # version_no unik di seluruh tabel; nomor besar acak supaya tidak bentrok data lain.
    rubrik = buat_versi(conn, admin, version_no=1000 + uuid.uuid4().int % 100_000)
    produk_a, produk_b = buat_produk(conn, admin), buat_produk(conn, admin)
    return Dunia(
        admin=admin,
        rubrik=rubrik,
        produk_a=produk_a,
        produk_b=produk_b,
        sesi_a=buat_sesi(conn, produk_a, admin),
        sesi_b=buat_sesi(conn, produk_b, admin),
    )


def buat_laporan(conn: Connection, dunia: Dunia, **ubah) -> uuid.UUID:
    isi = {
        "id": uuid.uuid4(),
        "title": "Laporan Oktober",
        "score_scope": "test",
        "has_dev_scores": False,
        "rubric": dunia.rubrik,
        "admin": dunia.admin,
        "basis": 1,
        "created_at": None,
    } | ubah
    return conn.execute(
        text(
            "INSERT INTO reports (id, title, score_scope, has_dev_scores, measurement_filter, "
            "product_label_mode, rubric_version_id, judge_model_name, case_basis_count, "
            "created_by, created_at) VALUES (:id, :title, "
            "CAST(:score_scope AS report_score_scope_enum), :has_dev_scores, 'all', 'real_name', "
            ":rubric, 'judge-model-sintetis', :basis, :admin, coalesce(:created_at, now())) "
            "RETURNING id"
        ),
        isi,
    ).scalar_one()


def tambah_sesi(
    conn: Connection, laporan: uuid.UUID, sesi: uuid.UUID, produk: uuid.UUID, kind: str = "manual"
) -> None:
    conn.execute(
        text(
            "INSERT INTO report_sessions (report_id, session_id, product_id, product_label, "
            "measurement_kind, is_trial, markers) VALUES (:laporan, :sesi, :produk, 'Produk A', "
            "CAST(:kind AS report_measurement_kind_enum), false, CAST(:markers AS jsonb))"
        ),
        {
            "laporan": laporan,
            "sesi": sesi,
            "produk": produk,
            "kind": kind,
            "markers": json.dumps([{"code": "repetitions_differ", "detail": "1 vs 3"}]),
        },
    )


def tambah_hasil(conn: Connection, laporan: uuid.UUID, sesi: uuid.UUID, **ubah) -> uuid.UUID:
    isi = {
        "id": uuid.uuid4(),
        "laporan": laporan,
        "sesi": sesi,
        "split": "test",
        "failure_rate": None,
        "rank": 1,
    } | ubah
    return conn.execute(
        text(
            "INSERT INTO report_results (id, report_id, session_id, score_split, "
            "cases_graded_count, cases_basis_count, det_score, llm_score, total_score, "
            "failure_rate, rank, repetitions, product_model_version, judge_model_version) "
            "VALUES (:id, :laporan, :sesi, CAST(:split AS report_score_split_enum), 3, 1, 0.75, "
            "0.50, 0.63, :failure_rate, :rank, 1, 'model-a', 'judge-a') RETURNING id"
        ),
        isi,
    ).scalar_one()


def tambah_dasar(
    conn: Connection,
    laporan: uuid.UUID,
    kasus: uuid.UUID,
    versi: uuid.UUID,
    split: str = "test",
) -> None:
    conn.execute(
        text(
            "INSERT INTO report_case_basis (report_id, case_id, case_version_id, split_tag) "
            "VALUES (:laporan, :kasus, :versi, CAST(:split AS case_split_tag_enum))"
        ),
        {"laporan": laporan, "kasus": kasus, "versi": versi, "split": split},
    )


def laporan_utuh(conn: Connection, dunia: Dunia, **ubah) -> uuid.UUID:
    """Laporan Test lengkap: satu sesi, satu hasil, satu kasus dasar."""
    laporan = buat_laporan(conn, dunia, **ubah)
    tambah_sesi(conn, laporan, dunia.sesi_a, dunia.produk_a)
    tambah_hasil(conn, laporan, dunia.sesi_a)
    kasus, versi = buat_kasus(conn, dunia.admin, split_tag="test")
    tambah_dasar(conn, laporan, kasus, versi)
    return laporan


def terbitkan(
    conn: Connection,
    laporan: uuid.UUID,
    dunia: Dunia,
    version_no: int = 1,
    supersedes: uuid.UUID | None = None,
) -> uuid.UUID:
    return conn.execute(
        text(
            "INSERT INTO published_reports (id, report_id, version_no, published_by, "
            "supersedes_id) VALUES (:id, :laporan, :versi, :admin, :lama) RETURNING id"
        ),
        {
            "id": uuid.uuid4(),
            "laporan": laporan,
            "versi": version_no,
            "admin": dunia.admin,
            "lama": supersedes,
        },
    ).scalar_one()


def ubah_status(conn: Connection, laporan: uuid.UUID, status: str) -> None:
    conn.execute(
        text("UPDATE reports SET status = CAST(:status AS report_status_enum) WHERE id = :id"),
        {"id": laporan, "status": status},
    )
