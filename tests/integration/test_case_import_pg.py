"""Impor CSV di PostgreSQL. PBI-4 SCRUM-81 SCRUM-338, diagram D7 dan D8.

Yang tidak dibuktikan SQLite: CHECK D8 benar-benar dijalankan (origin import wajib punya batch,
hasil baris created wajib menunjuk kasus dan yang lain wajib beralasan, jumlah batch tidak
melebihi total, tag kosong hanya pada draft), foreign key ke users, suites, dan import_batches,
enum PostgreSQL, serta seed jenis peraturan dari migrasi untuk rujukan terstruktur.

Service dipanggil lewat Session yang menumpang transaksi luar
(join_transaction_mode="create_savepoint"): commit service menjadi savepoint, dan seluruh baris
dibatalkan di akhir test. Contoh hukum hanya KTK-001.
"""

import csv
import io
import json
import os
import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.modules.cases import import_service, service
from app.modules.cases.csv_import import TEMPLATE_COLUMNS
from app.modules.cases.schemas import CaseWrite
from app.shared import request_context
from tests.legal_refs import rujukan_ktk_001

POSTGRES_URL = os.environ.get("TEST_POSTGRES_URL", "")

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(not POSTGRES_URL, reason="TEST_POSTGRES_URL tidak diisi"),
]


@pytest.fixture
def db():
    engine = create_engine(POSTGRES_URL)
    with engine.connect() as conn:
        transaksi = conn.begin()
        sesi = Session(bind=conn, join_transaction_mode="create_savepoint")
        user_id = uuid.uuid4()
        conn.execute(
            text(
                "INSERT INTO users (id, email, name, role) "
                "VALUES (:id, 'qa-338@veritask.test', 'QA 338', 'contributor')"
            ),
            {"id": user_id},
        )
        request_context.bind(sesi, user_id=user_id, role="contributor")
        try:
            yield sesi, user_id
        finally:
            sesi.close()
            transaksi.rollback()
    engine.dispose()


def _baris(**isi: str) -> list[str]:
    return [isi.get(kolom, "") for kolom in TEMPLATE_COLUMNS]


def _berkas(*baris: list[str]) -> bytes:
    keluaran = io.StringIO()
    penulis = csv.writer(keluaran, lineterminator="\r\n")
    penulis.writerow(TEMPLATE_COLUMNS)
    penulis.writerows(baris)
    return keluaran.getvalue().encode("utf-8")


def _lengkap(judul: str) -> list[str]:
    return _baris(
        title=judul,
        question="Berapa pesangon PHK?",
        split_tag="test",
        legal_refs=json.dumps([rujukan_ktk_001(), "Teks bebas"]),
        must_contain='["pesangon"]',
    )


def _kasus_manual(sesi: Session, user_id: uuid.UUID, judul: str) -> str:
    suite_id = uuid.uuid4()
    sesi.execute(
        text(
            "INSERT INTO suites (id, name, status, created_by) VALUES (:id, 'Suite 338', 'active', :u)"
        ),
        {"id": suite_id, "u": user_id},
    )
    badan = CaseWrite.model_validate(
        {
            "case_code": "QA-338",
            "identity": {"title": judul, "question": "Berapa pesangon PHK?"},
            "legal_refs": [rujukan_ktk_001()],
            "split_tag": "dev",
        }
    )
    return service.create_case(sesi, suite_id, badan, actor_id=user_id).case_code


def test_impor_lengkap_lolos_semua_check_d8(db):
    """Positif: created, failed, dan skipped tersimpan bersama di PostgreSQL; draft tanpa tag
    diterima, rujukan terstruktur memakai jenis dari seed migrasi."""
    sesi, user_id = db
    kode_manual = _kasus_manual(sesi, user_id, "Kasus manual")

    ringkasan = import_service.import_cases(
        sesi,
        file_name="kasus-lama.csv",
        data=_berkas(
            _lengkap("Kasus baru"),
            _baris(title="Tanpa tag"),
            _baris(title="Rusak", legal_refs="[1]"),
            _lengkap("Kasus manual"),
        ),
        actor_id=user_id,
    )

    assert [(b.row_no, b.status) for b in ringkasan.rows] == [
        (2, "created"),
        (3, "created"),
        (4, "failed"),
        (5, "skipped"),
    ]
    assert ringkasan.rows[3].reason == f"Kembar dengan kasus {kode_manual} yang sudah ada."
    batch = sesi.execute(
        text(
            "SELECT total_rows, success_count, failed_count, skipped_count, imported_by FROM import_batches WHERE id = :id"
        ),
        {"id": ringkasan.batch_id},
    ).one()
    assert tuple(batch) == (4, 2, 1, 1, user_id)
    kasus = sesi.execute(
        text(
            "SELECT c.origin::text, c.import_batch_id, v.status::text, v.split_tag::text "
            "FROM cases c JOIN case_versions v ON v.id = c.current_version_id "
            "WHERE c.import_batch_id = :id ORDER BY c.case_code"
        ),
        {"id": ringkasan.batch_id},
    ).all()
    assert [tuple(k) for k in kasus] == [
        ("import", ringkasan.batch_id, "draft", "test"),
        ("import", ringkasan.batch_id, "draft", None),
    ]
    rujukan = sesi.execute(
        text(
            'SELECT r."order", r.kind::text FROM legal_refs r JOIN cases c '
            'ON c.current_version_id = r.case_version_id WHERE c.id = :id ORDER BY r."order"'
        ),
        {"id": ringkasan.rows[0].case_id},
    ).all()
    assert [tuple(r) for r in rujukan] == [(1, "structured"), (2, "free_text")]


def test_kasus_hasil_impor_tercatat_di_audit_dengan_batch(db):
    """Positif: D8 SCRUM-337, case.created mencatat origin import dan import_batch_id."""
    sesi, user_id = db

    ringkasan = import_service.import_cases(
        sesi, file_name="a.csv", data=_berkas(_lengkap("Audit")), actor_id=user_id
    )

    after = sesi.execute(
        text("SELECT after FROM audit_logs WHERE entity_id = :id AND action = 'case.created'"),
        {"id": str(ringkasan.rows[0].case_id)},
    ).scalar_one()
    assert after["origin"] == "import"
    assert after["import_batch_id"] == str(ringkasan.batch_id)


def test_suite_sementara_milik_pengimpor(db):
    """Positif: keputusan SA, suite sementara aktif dengan FK ke users."""
    sesi, user_id = db

    ringkasan = import_service.import_cases(
        sesi, file_name="b.csv", data=_berkas(_lengkap("Suite")), actor_id=user_id
    )

    suite = sesi.execute(
        text("SELECT status::text, created_by, name FROM suites WHERE id = :id"),
        {"id": ringkasan.suite_id},
    ).one()
    assert (suite.status, suite.created_by, suite.name) == ("active", user_id, ringkasan.suite_name)
