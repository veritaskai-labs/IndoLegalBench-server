"""Job harian retensi audit log (PBI-18 AC7).

Dijalankan cron di host, lihat docs/DEPLOY.md:

    docker compose run --rm api python -m app.modules.audit.retention_job

Sengaja memakai AUDIT_RETENTION_DATABASE_URL (role ilb_retention), bukan
koneksi aplikasi. Trigger audit_logs hanya meloloskan penghapusan dari
role itu, dan hanya untuk catatan lewat 90 hari.
"""

import logging
import sys

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.modules.audit import service as audit_service
from app.shared.config import get_settings

logger = logging.getLogger(__name__)


def main() -> int:
    url = get_settings().audit_retention_database_url
    if not url:
        logger.error("AUDIT_RETENTION_DATABASE_URL is empty, audit retention skipped")
        return 1
    engine = create_engine(url)
    try:
        with Session(engine) as db:
            audit_service.purge_expired(db)
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":  # pragma: no cover, dipanggil cron
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    sys.exit(main())
