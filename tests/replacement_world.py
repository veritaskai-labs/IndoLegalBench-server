"""Data sintetis untuk tes ganti versi dengan produk yang sama (SCRUM-371, L3).

Laporan dengan beberapa produk, dan sesi baru untuk produk yang sudah ada, supaya tes bisa
membedakan "produk sama, sesi beda" dari "produk beda". Dibangun di atas reports_factory.
"""

from sqlalchemy.orm import Session

from app.modules.auth.models import User
from app.modules.grading.models import RubricVersion
from app.modules.reports import repository
from app.modules.reports.models import Report
from app.modules.runs.models import MeasurementSession, SessionPurpose, SessionType, SplitFilter
from tests import reports_factory as pabrik


def sesi_baru(db: Session, admin: User, sesi: MeasurementSession) -> MeasurementSession:
    """Sesi lain (mis. bulan berikutnya) untuk produk yang sama dengan `sesi`."""
    baru = MeasurementSession(
        label=f"{sesi.label} ulang",
        purpose=SessionPurpose.OFFICIAL,
        session_type=SessionType.MANUAL,
        product_id=sesi.product_id,
        repetitions=1,
        split_filter=SplitFilter.TEST,
        created_by=admin.id,
    )
    db.add(baru)
    db.flush()
    return baru


def simpan(
    db: Session,
    admin: User,
    rubrik: RubricVersion,
    daftar_sesi: list[MeasurementSession],
    *,
    labels: list[str] | None = None,
    **ubah,
) -> Report:
    """Satu laporan tersimpan yang membandingkan produk dari `daftar_sesi`, sudah di-commit."""
    labels = labels or [f"Produk {i + 1}" for i in range(len(daftar_sesi))]
    report = pabrik.laporan(admin, rubrik, case_basis_count=1, **ubah)
    repository.add_report(
        db,
        report,
        [
            pabrik.sesi_laporan(report, sesi, product_label=label)
            for sesi, label in zip(daftar_sesi, labels, strict=True)
        ],
        [pabrik.hasil(report, sesi, rank=i + 1) for i, sesi in enumerate(daftar_sesi)],
        [pabrik.dasar(report)],
    )
    db.commit()
    return report
