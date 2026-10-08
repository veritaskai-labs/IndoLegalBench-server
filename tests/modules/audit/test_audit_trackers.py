"""Unit test penerjemah perubahan baris jadi event audit (katalog D6a).

PBI-18 AC1. Tracker adalah fungsi murni: menerima baris dan daftar
perubahan, mengembalikan event. Baris dipalsukan dengan SimpleNamespace,
jadi test ini tidak menyentuh database maupun model modul lain.
"""

import uuid
from datetime import UTC, datetime
from enum import StrEnum
from types import SimpleNamespace

import pytest

from app.modules.audit.models import AuditEntityType
from app.modules.audit.trackers import Change, tracker_for

ROW_ID = uuid.UUID("00000000-0000-0000-0000-0000000000a1")


class _Status(StrEnum):
    ACTIVE = "active"


def _row(**kolom) -> SimpleNamespace:
    return SimpleNamespace(id=ROW_ID, **kolom)


def _satu(events):
    assert len(events) == 1
    return events[0]


def test_tabel_di_luar_katalog_tidak_dilacak():
    # Act + Assert
    assert tracker_for("sessions") is None
    assert tracker_for("audit_logs") is None


@pytest.mark.parametrize(
    ("tabel", "jenis"),
    [
        ("suites", AuditEntityType.SUITE),
        ("cases", AuditEntityType.CASE),
    ],
)
def test_setiap_tabel_katalog_punya_tracker(tabel, jenis):
    # Act
    tracker = tracker_for(tabel)

    # Assert
    assert tracker.entity_type is jenis


# --- Suite --------------------------------------------------------------------


def test_suite_dibuat():
    # Arrange
    row = _row(name="Ketenagakerjaan", description=None, status=_Status.ACTIVE)

    # Act
    event = _satu(tracker_for("suites").created(row))

    # Assert
    assert event.action == "suite.created"
    assert event.entity_id == ROW_ID
    assert event.before is None
    assert event.after == {"name": "Ketenagakerjaan", "description": None, "status": "active"}


def test_suite_diubah_hanya_field_yang_berubah():
    # Act
    event = _satu(tracker_for("suites").updated(_row(), {"name": Change("Lama", "Baru")}))

    # Assert
    assert event.action == "suite.updated"
    assert (event.before, event.after) == ({"name": "Lama"}, {"name": "Baru"})


@pytest.mark.parametrize(
    ("lama", "baru", "action"),
    [("active", "archived", "suite.archived"), ("archived", "active", "suite.unarchived")],
)
def test_status_suite_jadi_event_arsip(lama, baru, action):
    # Act
    event = _satu(tracker_for("suites").updated(_row(), {"status": Change(lama, baru)}))

    # Assert
    assert event.action == action
    assert (event.before, event.after) == ({"status": lama}, {"status": baru})


def test_suite_dihapus_lunak():
    # Arrange
    waktu = datetime(2026, 10, 8, 3, 0, tzinfo=UTC)

    # Act
    event = _satu(tracker_for("suites").updated(_row(), {"deleted_at": Change(None, waktu)}))

    # Assert
    assert event.action == "suite.deleted"
    assert event.after == {"deleted_at": "2026-10-08T03:00:00+00:00"}


def test_satu_perubahan_bisa_menghasilkan_dua_event():
    # Arrange
    perubahan = {"status": Change("active", "archived"), "name": Change("A", "B")}

    # Act
    events = tracker_for("suites").updated(_row(), perubahan)

    # Assert
    assert [event.action for event in events] == ["suite.archived", "suite.updated"]


def test_tanpa_perubahan_tidak_ada_event():
    # Act + Assert
    assert tracker_for("suites").updated(_row(), {}) == []


# --- Case ---------------------------------------------------------------------


def test_kasus_dibuat_mencatat_kode_dan_versi():
    # Act
    event = _satu(tracker_for("cases").created(_row(case_code="KTK-001", version=1)))

    # Assert
    assert event.action == "case.created"
    assert event.case_id == ROW_ID
    assert event.after == {"case_code": "KTK-001", "version_no": 1}


def test_isi_kasus_diubah_hanya_field_yang_berubah():
    # Arrange
    perubahan = {"title": Change("Lama", "Baru"), "traps": Change([], [{"text": "x"}])}

    # Act
    event = _satu(tracker_for("cases").updated(_row(), perubahan))

    # Assert
    assert event.action == "case.updated"
    assert event.case_id == ROW_ID
    assert event.before == {"title": "Lama", "traps": []}
    assert event.after == {"title": "Baru", "traps": [{"text": "x"}]}


def test_tag_dev_ke_test_ditandai_peringatan():
    """D6a: case.tag_changed mencatat split_tag lama/baru dan flag warning dev→test."""
    # Act
    event = _satu(tracker_for("cases").updated(_row(), {"split_tag": Change("dev", "test")}))

    # Assert
    assert event.action == "case.tag_changed"
    assert event.before == {"split_tag": "dev"}
    assert event.after == {"split_tag": "test", "warning": True}


def test_tag_test_ke_dev_tanpa_peringatan():
    # Act
    event = _satu(tracker_for("cases").updated(_row(), {"split_tag": Change("test", "dev")}))

    # Assert
    assert event.after == {"split_tag": "dev"}


@pytest.mark.parametrize(
    ("baru", "action"),
    [
        ("in_review", "case.submitted"),
        ("approved", "case.approved"),
        ("needs_revision", "case.revision_requested"),
        ("draft", "case.status_changed"),
    ],
)
def test_status_kasus_jadi_event_review(baru, action):
    # Act
    event = _satu(tracker_for("cases").updated(_row(), {"status": Change("x", baru)}))

    # Assert
    assert event.action == action
    assert event.after == {"status": baru}


def test_nilai_kolom_diubah_jadi_aman_untuk_jsonb():
    """uuid dan Decimal tidak bisa disimpan langsung di kolom JSON."""
    # Arrange
    pemilik = uuid.UUID("00000000-0000-0000-0000-0000000000b2")

    # Act
    event = _satu(tracker_for("suites").updated(_row(), {"created_by": Change(None, pemilik)}))

    # Assert
    assert event.after == {"created_by": str(pemilik)}
