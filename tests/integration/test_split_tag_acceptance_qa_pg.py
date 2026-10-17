"""Uji penerimaan perubahan tag di PostgreSQL. Sub task QA SCRUM-166.

PBI-9 AC3 lewat service sungguhan di atas database hasil migration: trigger
versi approved mengizinkan perubahan tag oleh change_split_tag, penanda
was_dev dan baris audit ikut tersimpan, sedangkan isi versi approved tetap
terkunci. Tes HTTP di SQLite tidak punya trigger ini.

Data di-commit sungguhan karena service yang commit. Tidak dibersihkan:
audit_logs append-only dan merujuk pengguna yang dibuat di sini. Setiap tes
memakai id dan email baru, dan database CI dibuang setelah job selesai.
Dilewati kalau TEST_POSTGRES_URL kosong.
"""

import os
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from app.modules.audit.models import AuditLog
from app.modules.auth.models import User
from app.modules.cases import service
from app.modules.cases.models import Case, CaseStatus, CaseVersion, SplitTag
from app.modules.suites.models import Suite
from app.shared import request_context
from app.shared.security import Role

POSTGRES_URL = os.environ.get("TEST_POSTGRES_URL", "")

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(not POSTGRES_URL, reason="TEST_POSTGRES_URL tidak diisi"),
]


@pytest.fixture(scope="module")
def buat_sesi():
    engine = create_engine(POSTGRES_URL)
    yield sessionmaker(bind=engine)
    engine.dispose()


def _pengguna(db: Session, role: Role) -> uuid.UUID:
    user = User(
        email=f"qa166-{role}-{uuid.uuid4().hex}@veritask.test", name=f"QA {role}", role=role
    )
    db.add(user)
    db.flush()
    return user.id


@pytest.fixture
def kasus_disetujui(buat_sesi) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """Kasus dev yang sudah approved, di-commit. Mengembalikan (case_id, version_id, admin_id)."""
    with buat_sesi() as db:
        penulis, admin = _pengguna(db, Role.AUTHOR), _pengguna(db, Role.ADMIN)
        suite = Suite(name=f"QA 166 {uuid.uuid4().hex}", created_by=penulis)
        db.add(suite)
        db.flush()
        case_id, version_id = uuid.uuid4(), uuid.uuid4()
        kode = f"QA166-{uuid.uuid4().hex[:8]}"
        db.add(
            Case(
                id=case_id,
                case_code=kode,
                suite_id=suite.id,
                current_version_id=version_id,
                latest_approved_version_id=version_id,
                created_by=penulis,
                updated_by=penulis,
            )
        )
        db.add(
            CaseVersion(
                id=version_id,
                case_id=case_id,
                version_no=1,
                status=CaseStatus.APPROVED,
                case_code=kode,
                split_tag=SplitTag.DEV,
                content={"title": "PHK sepihak", "question": "Sah?"},
                created_by=penulis,
            )
        )
        db.commit()
        return case_id, version_id, admin


def _ubah_sebagai_admin(buat_sesi, case_id, admin_id, tag: SplitTag, confirm: bool):
    with buat_sesi() as db:
        request_context.bind(db, user_id=admin_id, role=Role.ADMIN, request_id=None)
        return service.change_split_tag(db, case_id, tag, actor_id=admin_id, confirm=confirm)


def test_ac3_positif_admin_mengubah_tag_versi_approved_di_postgresql(buat_sesi, kasus_disetujui):
    # Arrange
    case_id, version_id, admin = kasus_disetujui

    # Act
    hasil = _ubah_sebagai_admin(buat_sesi, case_id, admin, SplitTag.TEST, confirm=True)

    # Assert
    assert (hasil.split_tag, hasil.was_dev, hasil.status) == (
        SplitTag.TEST,
        True,
        CaseStatus.APPROVED,
    )
    with buat_sesi() as db:
        assert db.get(CaseVersion, version_id).split_tag == SplitTag.TEST
        assert db.get(Case, case_id).was_dev is True
        [baris] = db.query(AuditLog).filter_by(action="case.tag_changed", case_id=case_id).all()
        assert baris.after == {"split_tag": "test", "warning": True}
        assert baris.actor_user_id == admin


def test_ac3_negatif_isi_versi_approved_tetap_terkunci_setelah_tag_diubah(
    buat_sesi, kasus_disetujui
):
    """Pengecualian tag tidak membuka kolom lain: isi tetap ditolak trigger."""
    # Arrange
    case_id, version_id, admin = kasus_disetujui
    _ubah_sebagai_admin(buat_sesi, case_id, admin, SplitTag.TEST, confirm=True)

    # Act
    with buat_sesi() as db:
        versi = db.get(CaseVersion, version_id)
        versi.content = {"title": "Diubah diam-diam", "question": "Sah?"}
        with pytest.raises(DBAPIError, match="approved case version is immutable"):
            db.commit()

    # Assert
    with buat_sesi() as db:
        assert db.get(CaseVersion, version_id).content["title"] == "PHK sepihak"


def test_edge_bolak_balik_di_postgresql_pernah_dev_tetap(buat_sesi, kasus_disetujui):
    # Arrange
    case_id, _, admin = kasus_disetujui

    # Act
    _ubah_sebagai_admin(buat_sesi, case_id, admin, SplitTag.TEST, confirm=True)
    kembali = _ubah_sebagai_admin(buat_sesi, case_id, admin, SplitTag.DEV, confirm=False)

    # Assert
    assert (kembali.split_tag, kembali.was_dev) == (SplitTag.DEV, True)
    with buat_sesi() as db:
        assert db.query(AuditLog).filter_by(action="case.tag_changed", case_id=case_id).count() == 2
