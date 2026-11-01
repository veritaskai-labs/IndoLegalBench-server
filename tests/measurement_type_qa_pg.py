"""Bantuan tes PostgreSQL QA PBI-23 (SCRUM-204): produk, pengguna, dan sesi pengukuran.

Riwayat produk (D1 C2) dibaca dari measurement_sessions. Tabel aslinya dibuat SCRUM-205
(PBI-11, belum ada di staging saat tes ini ditulis). Selama belum ada, tabel_sesi()
membuat tabel khusus tes dengan nama dan kolom yang sama seperti rancangan D2, supaya
trigger a1f8c7e3d092 dan cek riwayat di service benar-benar melihat baris sesi.
Begitu tabel asli ada, tabel itu yang dipakai dan tabel khusus tes tidak dibuat.

Nilai status dan session_type yang dipakai hanya yang ada di enum tabel asli
(queued, finished, finished_with_failures, failed). Status Dibatalkan baru ditambah
PBI-12, jadi belum bisa diuji di tabel asli.
"""

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import Connection, Engine, text
from sqlalchemy.orm import Session

from app.modules.auth.models import User
from app.modules.providers import service
from app.modules.providers.schemas import AiProductCreate
from app.shared.security import Role

# Penanda tabel khusus tes: tabel bertanda ini boleh dihapus lagi oleh tabel_sesi().
PENANDA = "SCRUM-204 tabel sesi khusus tes"

_TABEL_TES = """
    CREATE TABLE measurement_sessions (
        id uuid PRIMARY KEY,
        label varchar(200) NOT NULL,
        purpose text NOT NULL,
        session_type text NOT NULL,
        product_id uuid NOT NULL REFERENCES ai_products(id),
        repetitions smallint NOT NULL,
        split_filter text NOT NULL,
        status text NOT NULL,
        created_by uuid NOT NULL REFERENCES users(id),
        archived_at timestamptz,
        archived_by uuid REFERENCES users(id)
    )
"""


@contextmanager
def tabel_sesi(engine: Engine) -> Iterator[None]:
    """Pastikan measurement_sessions ada selama blok berjalan.

    Tabel asli tidak pernah disentuh. Tabel khusus tes (bertanda PENANDA) dihapus di
    akhir, termasuk sisa dari jalan sebelumnya yang terhenti di tengah.
    """
    with engine.begin() as conn:
        tabel = conn.scalar(text("SELECT to_regclass('measurement_sessions')"))
        komentar = (
            conn.scalar(text("SELECT obj_description('measurement_sessions'::regclass)"))
            if tabel
            else None
        )
        milik_tes = tabel is None or komentar == PENANDA
        if tabel is None:
            conn.execute(text(_TABEL_TES))
            conn.execute(text(f"COMMENT ON TABLE measurement_sessions IS '{PENANDA}'"))
    try:
        yield
    finally:
        if milik_tes:
            with engine.begin() as conn:
                conn.execute(text("DROP TABLE IF EXISTS measurement_sessions"))


def tabel_sesi_ada(engine: Engine) -> bool:
    with engine.connect() as conn:
        return conn.scalar(text("SELECT to_regclass('measurement_sessions')")) is not None


def pengguna(db: Session, role: Role = Role.ADMIN) -> uuid.UUID:
    user = User(email=f"qa-204-{uuid.uuid4().hex}@veritask.test", name="QA 204", role=role)
    db.add(user)
    db.commit()
    return user.id


def daftar_otomatis(db: Session, pembuat: uuid.UUID) -> uuid.UUID:
    """Produk Otomatis lengkap lewat service, dengan nama baru setiap kali."""
    # Nilai sintetis, bukan produk yang didaftarkan Veritask.
    body = AiProductCreate(
        name=f"QA204 Otomatis {uuid.uuid4().hex[:8]}",
        provider_type="openai_compatible",
        base_url="https://api.example.test/v1",
        model_name="model-uji",
        credential="rahasia-uji-204-pg",  # pragma: allowlist secret
        rate_limit_per_minute=30,
        monthly_budget_idr=Decimal("500000.00"),
    )
    return service.create_product(db, body, created_by=pembuat).id


def daftar_manual(db: Session, pembuat: uuid.UUID) -> uuid.UUID:
    body = AiProductCreate(
        name=f"QA204 Manual {uuid.uuid4().hex[:8]}",
        measurement_type="manual",
        vendor_name="Penyedia Uji",
        site_url="https://contoh.test",
    )
    return service.create_product(db, body, created_by=pembuat).id


def tambah_sesi(
    conn: Connection,
    product_id: uuid.UUID,
    pembuat: uuid.UUID,
    *,
    session_type: str = "automatic",
    status: str = "finished",
    arsip: bool = False,
) -> uuid.UUID:
    """Satu baris sesi pengukuran. Tidak commit: pemanggil yang menentukan kapan."""
    session_id = uuid.uuid4()
    conn.execute(
        text(
            "INSERT INTO measurement_sessions (id, label, purpose, session_type, product_id, "
            "repetitions, split_filter, status, created_by, archived_at, archived_by) "
            "VALUES (:id, 'QA 204', 'trial', :jenis, :produk, 1, 'both', :status, :pembuat, "
            ":arsip_at, :arsip_by)"
        ),
        {
            "id": session_id,
            "jenis": session_type,
            "produk": product_id,
            "status": status,
            "pembuat": pembuat,
            "arsip_at": datetime.now(UTC) if arsip else None,
            "arsip_by": pembuat if arsip else None,
        },
    )
    return session_id


def jenis_dan_sesi(conn: Connection, product_id: uuid.UUID) -> tuple[str, list[str]]:
    """Jenis produk dan jenis setiap sesinya, untuk memeriksa "jenis campur"."""
    jenis = conn.scalar(
        text("SELECT measurement_type FROM ai_products WHERE id = :id"), {"id": product_id}
    )
    sesi = conn.scalars(
        text("SELECT session_type FROM measurement_sessions WHERE product_id = :id"),
        {"id": product_id},
    ).all()
    return str(jenis), [str(item) for item in sesi]
