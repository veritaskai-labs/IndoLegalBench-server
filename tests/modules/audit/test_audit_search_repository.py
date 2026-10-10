"""Test query pencarian audit log di SQLite (PBI-18 AC3, AC5).

Baris disiapkan langsung lewat model dengan occurred_at tertentu, supaya
rentang waktu bisa diuji tepat. Tidak ada baris users yang dibuat, jadi
listener tidak menambah baris user.added.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.modules.audit import repository
from app.modules.audit.models import AuditLog
from app.modules.audit.repository import SearchCriteria

AWAL = datetime(2026, 10, 1, 0, 0, tzinfo=UTC)
RINA = uuid.UUID("00000000-0000-0000-0000-0000000000b1")
BUDI = uuid.UUID("00000000-0000-0000-0000-0000000000b2")
KASUS_A = uuid.UUID("00000000-0000-0000-0000-0000000000c1")
KASUS_B = uuid.UUID("00000000-0000-0000-0000-0000000000c2")


@pytest.fixture
def baris(db_session) -> list[AuditLog]:
    """Lima baris, hari ke-0 sampai ke-4 sejak AWAL."""
    data = [
        ("suite.created", "suite", RINA, None),
        ("case.created", "case", RINA, KASUS_A),
        ("case.updated", "case", BUDI, KASUS_A),
        ("case.created", "case", BUDI, KASUS_B),
        ("ai_product.registered", "ai_product", None, None),
    ]
    rows = [
        AuditLog(
            occurred_at=AWAL + timedelta(days=hari),
            action=action,
            entity_type=jenis,
            entity_id=case_id or uuid.uuid4(),
            actor_user_id=aktor,
            case_id=case_id,
        )
        for hari, (action, jenis, aktor, case_id) in enumerate(data)
    ]
    db_session.add_all(rows)
    db_session.commit()
    return rows


def _cari(db_session, **kriteria) -> list[str]:
    hasil = repository.search(db_session, SearchCriteria(**kriteria), offset=0, limit=100)
    return [row.action for row in hasil]


def test_tanpa_saringan_terbaru_lebih_dulu(db_session, baris):
    # Act
    hasil = _cari(db_session)

    # Assert
    assert hasil == [
        "ai_product.registered",
        "case.created",
        "case.updated",
        "case.created",
        "suite.created",
    ]


def test_rentang_waktu_inklusif_di_kedua_ujung(db_session, baris):
    # Act
    hasil = _cari(
        db_session,
        occurred_from=AWAL + timedelta(days=1),
        occurred_to=AWAL + timedelta(days=3),
    )

    # Assert
    assert hasil == ["case.created", "case.updated", "case.created"]


def test_saring_jenis_data(db_session, baris):
    # Act + Assert
    assert _cari(db_session, entity_type="ai_product") == ["ai_product.registered"]


def test_saring_beberapa_pelaku(db_session, baris):
    # Act
    hasil = _cari(db_session, actor_ids=(RINA,))

    # Assert
    assert hasil == ["case.created", "suite.created"]


def test_saring_satu_kasus(db_session, baris):
    # Act + Assert
    assert _cari(db_session, case_id=KASUS_B) == ["case.created"]


def test_cakupan_kasus_reviewer(db_session, baris):
    """AC5: hanya baris yang case_id-nya ada di cakupan, baris tanpa kasus ikut tersaring."""
    # Act + Assert
    assert _cari(db_session, case_scope=frozenset({KASUS_A})) == ["case.updated", "case.created"]


def test_cakupan_kosong_tidak_mengembalikan_apa_pun(db_session, baris):
    # Act + Assert
    assert _cari(db_session, case_scope=frozenset()) == []


def test_saringan_digabung_dengan_dan(db_session, baris):
    # Act + Assert
    assert _cari(db_session, entity_type="case", actor_ids=(BUDI,), case_id=KASUS_A) == [
        "case.updated"
    ]


def test_paginasi_dan_jumlah_total(db_session, baris):
    # Arrange
    kriteria = SearchCriteria(entity_type="case")

    # Act
    halaman_dua = repository.search(db_session, kriteria, offset=2, limit=2)
    total = repository.count(db_session, kriteria)

    # Assert
    assert [row.action for row in halaman_dua] == ["case.created"]
    assert total == 3


def test_ambil_satu_baris_berdasarkan_id(db_session, baris):
    # Act
    tersimpan = repository.get_by_id(db_session, baris[2].id)

    # Assert
    assert tersimpan.action == "case.updated"


def test_id_tidak_ada_mengembalikan_none(db_session, baris):
    # Act + Assert
    assert repository.get_by_id(db_session, 999_999) is None
