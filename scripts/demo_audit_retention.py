"""Demo retensi audit log 90 hari untuk UAT (F-07, PBI-18 AC7).

Penghapusan otomatis tidak terlihat dari layar mana pun, jadi script ini
memperlihatkannya ke klien:

1. membuat dua catatan audit sintetis bertanggal 91 dan 89 hari lalu,
2. menjalankan job retensi yang sama dengan cron malam
   (app.modules.audit.retention_job, role ilb_retention),
3. mencetak tabel sebelum/sesudah: yang 91 hari hilang, yang 89 hari tetap.

Hanya untuk stack dev atau lokal, dan hanya dengan flag konfirmasi:

    docker compose run --rm -T api python - --confirm-dev-stack < scripts/demo_audit_retention.py

Catatan uji ditulis lewat DATABASE_URL, yaitu pemilik tabel, sama seperti
test PostgreSQL menyiapkan data tua. Trigger append-only hanya menolak
UPDATE, DELETE, dan TRUNCATE, jadi INSERT tidak perlu melewati apa pun.
Penghapusan tetap hanya lewat role ilb_retention. Catatan 89 hari ikut
terhapus oleh cron dua malam kemudian, seperti catatan lain.
"""

import argparse
import logging
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import uuid  # noqa: E402

from sqlalchemy import select  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402
from sqlalchemy.exc import ArgumentError  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.modules.audit import retention_job  # noqa: E402
from app.modules.audit.models import AuditEntityType, AuditLog  # noqa: E402
from app.modules.audit.service import RETENTION_DAYS  # noqa: E402
from app.shared.config import get_settings  # noqa: E402
from app.shared.database import get_session_factory  # noqa: E402

CONFIRM_FLAG = "--confirm-dev-stack"

# local = laptop, staging = stack dev di VM. production tidak pernah.
ALLOWED_ENVS = frozenset({"local", "staging"})

# "db" adalah nama service PostgreSQL di dalam jaringan compose stack itu sendiri.
LOCAL_HOSTS = frozenset({"db", "localhost", "127.0.0.1", "::1"})

# Mengapit batas: satu hari lewat dan satu hari kurang dari masa simpan.
DEMO_AGES_DAYS = (RETENTION_DAYS + 1, RETENTION_DAYS - 1)

DEMO_ACTION = "suite.retention_demo"
DEMO_REASON = "DEMO UAT F-07: data sintetis untuk memperlihatkan retensi 90 hari"

_BULAN = ("Jan", "Feb", "Mar", "Apr", "Mei", "Jun", "Jul", "Agu", "Sep", "Okt", "Nov", "Des")


@dataclass(frozen=True)
class DemoRow:
    id: int
    age_days: int
    occurred_at: datetime


def _host(url: str) -> str | None:
    try:
        return make_url(url).host
    except ArgumentError:
        return None


def refusal_reason(
    *, confirmed: bool, app_env: str, database_url: str, retention_url: str
) -> str | None:
    """Alasan menolak jalan, atau None kalau target jelas stack dev atau lokal."""
    if not confirmed:
        return f"tambahkan {CONFIRM_FLAG} untuk memastikan ini stack dev atau lokal."
    if app_env not in ALLOWED_ENVS:
        return f"APP_ENV={app_env!r}. Demo hanya untuk local atau staging, bukan production."
    if not retention_url:
        return "AUDIT_RETENTION_DATABASE_URL kosong, job retensi tidak bisa jalan."
    for nama, url in (
        ("DATABASE_URL", database_url),
        ("AUDIT_RETENTION_DATABASE_URL", retention_url),
    ):
        host = _host(url)
        if host not in LOCAL_HOSTS:
            return f"{nama} mengarah ke host {host!r}, bukan database stack ini sendiri."
    return None


def is_expired(occurred_at: datetime, now: datetime) -> bool:
    """Aturan yang sama dengan trigger dan repository: lebih tua dari batas, bukan tepat di batas."""
    return occurred_at < now - timedelta(days=RETENTION_DAYS)


def seed_demo_rows(db: Session, now: datetime) -> list[DemoRow]:
    rows = [
        AuditLog(
            occurred_at=now - timedelta(days=age),
            action=DEMO_ACTION,
            entity_type=AuditEntityType.SUITE.value,
            entity_id=uuid.uuid4(),
            reason=DEMO_REASON,
        )
        for age in DEMO_AGES_DAYS
    ]
    db.add_all(rows)
    db.commit()
    return [
        DemoRow(id=row.id, age_days=age, occurred_at=now - timedelta(days=age))
        for row, age in zip(rows, DEMO_AGES_DAYS, strict=True)
    ]


def existing_ids(db: Session, ids: Iterable[int]) -> set[int]:
    wanted = list(ids)
    if not wanted:
        return set()
    return set(db.scalars(select(AuditLog.id).where(AuditLog.id.in_(wanted))))


def _should_remain(row: DemoRow, now: datetime) -> bool:
    return not is_expired(row.occurred_at, now)


def _matches(row: DemoRow, after: set[int], now: datetime) -> bool:
    return (row.id in after) == _should_remain(row, now)


def outcome_ok(rows: Sequence[DemoRow], *, after: set[int], now: datetime) -> bool:
    return all(_matches(row, after, now) for row in rows)


def _tanggal(waktu: datetime) -> str:
    waktu = waktu.astimezone(UTC)
    return f"{waktu.day:02d} {_BULAN[waktu.month - 1]} {waktu.year} {waktu:%H:%M} UTC"


def _status(ada: bool) -> str:
    return "Ada" if ada else "Terhapus"


def render_report(rows: Sequence[DemoRow], before: set[int], after: set[int], now: datetime) -> str:
    batas = now - timedelta(days=RETENTION_DAYS)
    garis = "=" * 78
    kepala = (
        f"{'Catatan':<9}{'Tanggal kejadian':<24}{'Umur':<10}"
        f"{'Sebelum job':<13}{'Sesudah job':<13}{'Seharusnya':<11}Cocok"
    )
    baris = [
        garis,
        f" DEMO PENGHAPUSAN OTOMATIS AUDIT LOG, MASA SIMPAN {RETENTION_DAYS} HARI (PBI-18 AC7)",
        garis,
        f"Waktu demo : {_tanggal(now)}",
        f"Aturan     : catatan audit yang lebih tua dari {RETENTION_DAYS} hari dihapus",
        "             permanen oleh job yang berjalan otomatis setiap malam.",
        f"Batas kini : catatan sebelum {_tanggal(batas)} dihapus.",
        "",
        "Dua catatan uji (data sintetis, bukan data asli) dibuat, lalu job",
        "penghapusan yang sama dengan jadwal malam dijalankan sekarang:",
        "",
        kepala,
        "-" * len(kepala),
    ]
    for row in rows:
        baris.append(
            f"{'#' + str(row.id):<9}{_tanggal(row.occurred_at):<24}"
            f"{str(row.age_days) + ' hari':<10}{_status(row.id in before):<13}"
            f"{_status(row.id in after):<13}{_status(_should_remain(row, now)):<11}"
            f"{'Ya' if _matches(row, after, now) else 'TIDAK'}"
        )
    baris.append("")
    if outcome_ok(rows, after=after, now=now):
        tua, muda = DEMO_AGES_DAYS
        baris.append(
            f"HASIL: SESUAI. Catatan {tua} hari terhapus otomatis, "
            f"catatan {muda} hari tetap tersimpan."
        )
    else:
        baris.append("HASIL: TIDAK SESUAI. Lihat kolom Cocok, lalu periksa log job retensi.")
    baris.append(garis)
    return "\n".join(baris)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Demo retensi audit 90 hari untuk UAT.")
    parser.add_argument(CONFIRM_FLAG, dest="confirmed", action="store_true")
    args = parser.parse_args(argv)

    settings = get_settings()
    alasan = refusal_reason(
        confirmed=args.confirmed,
        app_env=settings.app_env,
        database_url=settings.database_url,
        retention_url=settings.audit_retention_database_url,
    )
    if alasan:
        print(f"DITOLAK: {alasan}")
        return 2

    now = datetime.now(UTC)
    with get_session_factory()() as db:
        rows = seed_demo_rows(db, now)
        ids = [row.id for row in rows]
        before = existing_ids(db, ids)
        if retention_job.main() != 0:
            print("GAGAL: job retensi gagal, lihat pesan error di atas.")
            return 1
        after = existing_ids(db, ids)

    print(render_report(rows, before, after, now))
    return 0 if outcome_ok(rows, after=after, now=now) else 1


if __name__ == "__main__":  # pragma: no cover, dijalankan manual saat UAT
    # Hanya baris log job retensi, sama dengan isi audit-retention.log.
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("app.modules.audit").setLevel(logging.INFO)
    sys.exit(main())
