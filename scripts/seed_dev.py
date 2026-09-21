"""Isi database lokal dengan empat akun uji, satu per peran.

Jalankan setelah migration:

    alembic upgrade head
    python scripts/seed_dev.py

Akun ini untuk pengembangan dan test manual, bukan data produksi, jadi
seed-nya sengaja TIDAK ditaruh di dalam migration. Kalau ikut migration,
keempatnya akan ikut terpasang di staging dan production juga, dan
PBI-1 AC5 melarang baris users dihapus begitu saja.

zitadel_sub sengaja dibiarkan kosong. Akun dianggap terdaftar di
platform tapi belum pernah login. Saat callback OIDC berjalan nanti,
pencocokan lewat email yang akan mengisi zitadel_sub.

Script ini aman dijalankan berulang kali: akun yang email-nya sudah ada
akan dilewati, tidak ditimpa.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sqlalchemy import select  # noqa: E402

from app.modules.auth.models import User  # noqa: E402
from app.shared.config import get_settings  # noqa: E402
from app.shared.database import get_session_factory  # noqa: E402
from app.shared.security import Role  # noqa: E402

SEED_USERS = [
    ("author@veritask.test", "Uji Author", Role.AUTHOR),
    ("reviewer@veritask.test", "Uji Reviewer", Role.REVIEWER),
    ("admin@veritask.test", "Uji Admin", Role.ADMIN),
    ("viewer@veritask.test", "Uji Viewer", Role.VIEWER),
]


def _target_description(database_url: str) -> str:
    """Tampilkan tujuan koneksi tanpa ikut membocorkan password."""
    if "@" in database_url:
        return database_url.rsplit("@", 1)[-1]
    return database_url


def main() -> None:
    settings = get_settings()

    # Penjaga terakhir. Akun uji tidak boleh pernah masuk production.
    if settings.app_env == "production":
        print("DITOLAK: seed akun uji tidak boleh dijalankan di production.")
        raise SystemExit(1)

    print(f"Environment : {settings.app_env}")
    print(f"Database    : {_target_description(settings.database_url)}")

    session_factory = get_session_factory()
    dibuat = 0
    dilewati = 0

    with session_factory() as session:
        for email, name, role in SEED_USERS:
            sudah_ada = session.scalar(select(User).where(User.email == email))
            if sudah_ada is not None:
                print(f"  lewati  {email:<26} sudah ada dengan peran {sudah_ada.role}")
                dilewati += 1
                continue

            session.add(User(email=email, name=name, role=role))
            print(f"  buat    {email:<26} peran {role}")
            dibuat += 1

        session.commit()

    print(f"Selesai. {dibuat} akun dibuat, {dilewati} dilewati.")


if __name__ == "__main__":
    main()
