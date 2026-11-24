"""Pencarian daftar kasus di PostgreSQL. PBI-4 SCRUM-81 SCRUM-339, AC3.

Tes API GET /cases berjalan di SQLite. Di sana LIKE sudah tidak peka huruf besar/kecil untuk
huruf ASCII, jadi tes SQLite tidak bisa membuktikan bahwa kata kunci dan kolom memang
dikecilkan (mutan "pola tidak dikecilkan" lolos di SQLite). LIKE PostgreSQL peka huruf, jadi
aturan itu dibuktikan di sini, beserta judul dari JSONB (content->>'title') dan escape LIKE.

Database CI dipakai bersama tes _pg lain, jadi tiap tes memakai suite sendiri dan satu
transaksi yang di-rollback. Dilewati kalau TEST_POSTGRES_URL kosong.
"""

import os
import uuid

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

from app.modules.auth.models import User
from app.modules.cases import service
from app.modules.cases.models import Case, CaseStatus, CaseVersion, SplitTag
from app.modules.suites.models import Suite
from app.shared.security import Role

POSTGRES_URL = os.environ.get("TEST_POSTGRES_URL", "")

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(not POSTGRES_URL, reason="TEST_POSTGRES_URL tidak diisi"),
]


@pytest.fixture
def db():
    engine = create_engine(POSTGRES_URL)
    with sessionmaker(bind=engine)() as sesi:
        yield sesi
        sesi.rollback()
    engine.dispose()


@pytest.fixture
def suite_id(db: Session) -> uuid.UUID:
    penulis = User(
        email=f"cari-{uuid.uuid4().hex}@veritask.test", name="QA cari", role=Role.CONTRIBUTOR
    )
    db.add(penulis)
    db.flush()
    suite = Suite(name=f"QA cari {uuid.uuid4().hex}", created_by=penulis.id)
    db.add(suite)
    db.flush()
    for kode, judul in (
        ("PHK-001", "PHK Sepihak"),
        ("CURI-007", "Pencurian dengan Pemberatan"),
        ("PJK-010", "Denda 2% per bulan"),
    ):
        case_id, version_id = uuid.uuid4(), uuid.uuid4()
        kode_unik = f"{kode}-{uuid.uuid4().hex[:6]}"
        db.add(
            Case(
                id=case_id,
                case_code=kode_unik,
                suite_id=suite.id,
                current_version_id=version_id,
                created_by=penulis.id,
                updated_by=penulis.id,
            )
        )
        db.add(
            CaseVersion(
                id=version_id,
                case_id=case_id,
                version_no=1,
                status=CaseStatus.DRAFT,
                case_code=kode_unik,
                split_tag=SplitTag.DEV,
                content={"title": judul, "question": "Sah?"},
                created_by=penulis.id,
            )
        )
    db.flush()
    return suite.id


def _kode(db: Session, suite_id: uuid.UUID, keyword: str) -> list[str]:
    page = service.list_all_cases(db, keyword=keyword, suite_id=suite_id)
    return sorted(item.case_code.rsplit("-", 1)[0] for item in page.items)


@pytest.mark.parametrize(
    ("keyword", "kode"),
    [
        ("phk-0", ["PHK-001"]),
        ("CURI-0", ["CURI-007"]),
        ("pemberatan", ["CURI-007"]),
        ("SEPIHAK", ["PHK-001"]),
    ],
)
def test_kata_kunci_tidak_peka_huruf_di_postgresql(db, suite_id, keyword, kode):
    """Positif: AC3, kode disimpan huruf besar dan judul huruf campur; ketikan pengguna dalam
    huruf apa pun tetap cocok. Di PostgreSQL ini hanya benar kalau kedua sisi dikecilkan."""
    # Act / Assert
    assert _kode(db, suite_id, keyword) == kode


def test_persen_di_postgresql_dicari_apa_adanya(db, suite_id):
    """Edge (A03): di PostgreSQL pun % harus huruf biasa, bukan "apa saja"."""
    # Act / Assert
    assert _kode(db, suite_id, "2%") == ["PJK-010"]
    assert _kode(db, suite_id, "%") == ["PJK-010"]


def test_kata_kunci_bisa_memakai_index_trigram(db, suite_id):
    """Positif (E5): kata kunci dicari di tengah kode atau judul ("%kata%"). Index btree biasa
    tidak bisa membantu pola seperti itu, jadi tanpa index trigram setiap pencarian membaca dan
    mengecilkan semua baris kedua tabel. Dengan seq scan dimatikan, rencana query endpoint harus
    memakai kedua index trigram (kode di cases, judul di case_versions)."""
    # Arrange
    sql: list[tuple[str, object]] = []

    def catat(conn, cursor, statement, parameters, context, executemany):  # noqa: ARG001
        if not statement.startswith("EXPLAIN"):
            sql.append((statement, parameters))

    db.execute(text("SET LOCAL enable_seqscan = off"))
    engine = db.get_bind()
    event.listen(engine, "before_cursor_execute", catat)
    try:
        service.list_all_cases(db, keyword="pemberatan", suite_id=suite_id)
    finally:
        event.remove(engine, "before_cursor_execute", catat)

    # Act
    rencana = "\n".join(
        baris
        for statement, parameters in sql
        if "case_versions" in statement and "count(*)" in statement.lower()
        for (baris,) in db.connection().exec_driver_sql("EXPLAIN " + statement, parameters)
    )

    # Assert
    assert "ix_cases_case_code_trgm" in rencana, rencana
    assert "ix_case_versions_title_trgm" in rencana, rencana
