"""Ganti versi dengan produk yang sama, di PostgreSQL 16. PBI-17 AC9, SCRUM-371.

Dua hal yang SQLite tidak bisa buktikan:
1. Query kandidat (GROUP BY ... HAVING dengan IN pada uuid) berjalan sama di PostgreSQL.
2. Penguncian: dua Admin mengganti versi yang sama bersamaan. SQLite mengabaikan FOR UPDATE;
   di sini yang kedua menunggu kunci versi lama, membaca ulang statusnya setelah yang pertama
   commit, lalu ditolak. Rantai versi tetap lurus dan versi lama tetap tersimpan.

Laporan di PostgreSQL tidak bisa dihapus (trigger beku), jadi setiap tes memakai database
baru yang disalin dari templat termigrasi (tests/trend_qa_pg.py) dan dibuang sesudahnya,
supaya tidak ada data yang tertinggal di database bersama (pelajaran SCRUM-258 E59).
Urutan diatur dengan threading dan pg_stat_activity, tanpa jeda waktu.
"""

import threading
import uuid
from collections.abc import Iterator
from dataclasses import dataclass

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.orm import sessionmaker

from app.modules.reports import publication, repository
from app.modules.reports.models import PublishedReport
from app.modules.reports.replacement import REPORT_PRODUCTS_MISMATCH
from app.shared.exceptions import ConflictError
from tests.integration.test_start_session_lock_pg import BATAS_TUNGGU, _tunggu_kunci
from tests.measurement_pg import buat_kasus, buat_produk, buat_sesi
from tests.reports_pg import buat_laporan as _buat_laporan
from tests.reports_pg import siapkan, tambah_dasar, tambah_hasil, tambah_sesi, terbitkan
from tests.trend_qa_pg import HANYA_POSTGRES, _database_baru, templat_tren  # noqa: F401

pytestmark = HANYA_POSTGRES


@dataclass
class Dunia:
    admin: uuid.UUID
    rubrik: uuid.UUID
    # nama produk -> (produk_id, sesi_id)
    produk: dict[str, tuple[uuid.UUID, uuid.UUID]]


@pytest.fixture
def engine(templat_tren) -> Iterator[Engine]:  # noqa: F811
    with _database_baru(templat_tren, templat=templat_tren.database) as url:
        mesin = create_engine(url, connect_args={"options": "-c lock_timeout=5000"})
        try:
            yield mesin
        finally:
            mesin.dispose()


@pytest.fixture
def dunia(engine) -> Dunia:
    with engine.begin() as conn:
        dasar = siapkan(conn)
        produk_c = buat_produk(conn, dasar.admin)
        return Dunia(
            admin=dasar.admin,
            rubrik=dasar.rubrik,
            produk={
                "A": (dasar.produk_a, dasar.sesi_a),
                "B": (dasar.produk_b, dasar.sesi_b),
                "C": (produk_c, buat_sesi(conn, produk_c, dasar.admin)),
            },
        )


def laporan(engine: Engine, d: Dunia, nama: str, **ubah) -> uuid.UUID:
    """Satu laporan Test tersimpan dengan produk dari `nama` (mis. "AB"), satu transaksi."""
    with engine.begin() as conn:
        return _laporan(conn, d, nama, **ubah)


def _laporan(conn: Connection, d: Dunia, nama: str, **ubah) -> uuid.UUID:
    report_id = _buat_laporan(conn, d, **ubah)
    for urutan, huruf in enumerate(nama, start=1):
        produk_id, sesi_id = d.produk[huruf]
        tambah_sesi(conn, report_id, sesi_id, produk_id)
        tambah_hasil(conn, report_id, sesi_id, rank=urutan)
    kasus, versi = buat_kasus(conn, d.admin, split_tag="test")
    tambah_dasar(conn, report_id, kasus, versi)
    return report_id


def terbit(engine: Engine, d: Dunia, report_id: uuid.UUID) -> uuid.UUID:
    with engine.begin() as conn:
        versi = terbitkan(conn, report_id, d)
        conn.execute(
            text("UPDATE reports SET status = 'published' WHERE id = :id"), {"id": report_id}
        )
        return versi


def test_candidate_queries_run_on_postgresql(engine, dunia):
    # Arrange
    ab = laporan(engine, dunia, "AB")
    terbit(engine, dunia, ab)
    abc = laporan(engine, dunia, "ABC")
    terbit(engine, dunia, abc)
    tersimpan_ba = laporan(engine, dunia, "BA")
    laporan(engine, dunia, "AC")
    Sesi = sessionmaker(bind=engine)

    # Act
    with Sesi() as db:
        untuk_tersimpan = repository.replace_candidates_for_saved(db, tersimpan_ba)
        untuk_terbit = repository.replace_candidates_for_published(db, ab)

    # Assert: superset dan beda produk tidak ikut (laporan dev diuji di SQLite)
    assert [row.report.id for row in untuk_tersimpan] == [ab]
    assert [report.id for report in untuk_terbit] == [tersimpan_ba]


def test_a_mismatch_is_refused_and_nothing_is_written(engine, dunia):
    ab = laporan(engine, dunia, "AB")
    v1 = terbit(engine, dunia, ab)
    ac = laporan(engine, dunia, "AC")
    Sesi = sessionmaker(bind=engine)

    with Sesi() as db, pytest.raises(ConflictError) as galat:
        publication.publish(db, report_id=ac, actor_id=dunia.admin, supersedes_id=v1)

    assert galat.value.code == REPORT_PRODUCTS_MISMATCH
    with Sesi() as db:
        assert db.scalar(select(func.count()).select_from(PublishedReport)) == 1
        status = dict(db.execute(text("SELECT id, status FROM reports")).tuples().all())
    assert (status[ab], status[ac]) == ("published", "saved")


def test_two_admins_replacing_the_same_version_keep_one_linear_chain(engine, dunia):
    # Arrange: v1 = A vs B terbit; dua laporan pengganti A vs B
    ab = laporan(engine, dunia, "AB")
    v1 = terbit(engine, dunia, ab)
    pertama = laporan(engine, dunia, "AB")
    kedua = laporan(engine, dunia, "BA")
    Sesi = sessionmaker(bind=engine)
    hasil: dict[str, object] = {}
    pid_kedua: list[int] = []
    siap = threading.Event()

    def admin_kedua():
        with Sesi() as db:
            pid_kedua.append(db.scalar(text("SELECT pg_backend_pid()")))
            siap.set()
            try:
                hasil["kedua"] = publication.publish(
                    db, report_id=kedua, actor_id=dunia.admin, supersedes_id=v1
                )
            except ConflictError as exc:
                hasil["kedua"] = exc.code

    # Act: admin pertama memegang kunci versi lama, admin kedua menunggu kunci itu
    with Sesi() as db_pertama:
        repository.lock_publication(db_pertama, v1)
        utas = threading.Thread(target=admin_kedua)
        utas.start()
        assert siap.wait(BATAS_TUNGGU)
        _tunggu_kunci(engine, pid_kedua[0])
        hasil["pertama"] = publication.publish(
            db_pertama, report_id=pertama, actor_id=dunia.admin, supersedes_id=v1
        )
    utas.join(BATAS_TUNGGU)

    # Assert: yang kedua membaca ulang status setelah kunci lepas, lalu ditolak
    assert not utas.is_alive()
    assert hasil["pertama"].version_no == 2
    assert hasil["kedua"] == publication.REPORT_NOT_PUBLISHABLE
    with Sesi() as db:
        rantai = db.execute(
            select(PublishedReport.report_id, PublishedReport.version_no).order_by(
                PublishedReport.version_no
            )
        ).all()
        status = dict(db.execute(text("SELECT id, status FROM reports")).tuples().all())
    assert rantai == [(ab, 1), (pertama, 2)]
    assert (status[ab], status[pertama], status[kedua]) == ("superseded", "published", "saved")
