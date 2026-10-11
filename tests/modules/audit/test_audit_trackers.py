"""Unit test penerjemah perubahan baris jadi event audit (katalog D6a).

PBI-18 AC1. Tracker adalah fungsi murni: menerima baris dan daftar
perubahan, mengembalikan event. Baris dipalsukan dengan SimpleNamespace,
jadi test ini tidak menyentuh database maupun model modul lain.
"""

import uuid
from datetime import UTC, datetime
from decimal import Decimal
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
        ("case_versions", AuditEntityType.CASE),
        ("review_rounds", AuditEntityType.CASE),
        ("ai_products", AuditEntityType.AI_PRODUCT),
        ("users", AuditEntityType.USER),
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


def test_kasus_dibuat_mencatat_kode():
    # Act
    event = _satu(tracker_for("cases").created(_row(case_code="KTK-001")))

    # Assert
    assert event.action == "case.created"
    assert event.case_id == ROW_ID
    assert event.after == {"case_code": "KTK-001"}


def test_versi_baru_mencatat_nomornya():
    # Act
    event = _satu(tracker_for("case_versions").created(_row(case_id=ROW_ID, version_no=2)))

    # Assert
    assert event.action == "case.version_created"
    assert event.case_id == ROW_ID
    assert event.after == {"version_no": 2}


def test_isi_kasus_diubah_hanya_field_yang_berubah():
    # Arrange
    perubahan = {
        "content": Change(
            {"title": "Lama", "traps": []},
            {"title": "Baru", "traps": [{"text": "x"}]},
        )
    }

    # Act
    event = _satu(tracker_for("case_versions").updated(_row(case_id=ROW_ID), perubahan))

    # Assert
    assert event.action == "case.updated"
    assert event.case_id == ROW_ID
    assert event.before == {"title": "Lama", "traps": []}
    assert event.after == {"title": "Baru", "traps": [{"text": "x"}]}


def test_tag_dev_ke_test_ditandai_peringatan():
    """D6a: case.tag_changed mencatat split_tag lama/baru dan flag warning dev→test."""
    # Act
    event = _satu(
        tracker_for("case_versions").updated(
            _row(case_id=ROW_ID), {"split_tag": Change("dev", "test")}
        )
    )

    # Assert
    assert event.action == "case.tag_changed"
    assert event.before == {"split_tag": "dev"}
    assert event.after == {"split_tag": "test", "warning": True}


def test_tag_test_ke_dev_tanpa_peringatan():
    # Act
    event = _satu(
        tracker_for("case_versions").updated(
            _row(case_id=ROW_ID), {"split_tag": Change("test", "dev")}
        )
    )

    # Assert
    assert event.after == {"split_tag": "dev"}


@pytest.mark.parametrize(
    ("baru", "action"),
    [
        ("approved", "case.approved"),
        ("needs_revision", "case.revision_requested"),
        ("draft", "case.status_changed"),
    ],
)
def test_status_kasus_jadi_event_review(baru, action):
    # Act
    event = _satu(
        tracker_for("case_versions").updated(_row(case_id=ROW_ID), {"status": Change("x", baru)})
    )

    # Assert
    assert event.action == action
    assert event.after == {"status": baru}


def test_versi_masuk_review_dicatat_oleh_round_bukan_versi():
    """case.submitted ditulis ReviewRoundTracker supaya round_no ikut (D6a)."""
    # Act
    events = tracker_for("case_versions").updated(
        _row(case_id=ROW_ID), {"status": Change("draft", "in_review")}
    )

    # Assert
    assert events == []


def test_versi_masuk_review_tetap_mencatat_perubahan_lain():
    # Act
    event = _satu(
        tracker_for("case_versions").updated(
            _row(case_id=ROW_ID),
            {"status": Change("draft", "in_review"), "split_tag": Change("test", "dev")},
        )
    )

    # Assert
    assert event.action == "case.tag_changed"


# --- Review round (SCRUM-143) -------------------------------------------------

VERSI_ID = uuid.UUID("00000000-0000-0000-0000-0000000000d1")
KASUS_ID = uuid.UUID("00000000-0000-0000-0000-0000000000c1")


def test_round_baru_dicatat_sebagai_kasus_diajukan():
    # Arrange
    ronde = _row(case_id=KASUS_ID, case_version_id=VERSI_ID, round_no=2)

    # Act
    event = _satu(tracker_for("review_rounds").created(ronde))

    # Assert
    assert event.action == "case.submitted"
    assert event.entity_id == VERSI_ID
    assert event.case_id == KASUS_ID
    assert event.after == {"status": "in_review", "round_no": 2}


def test_perubahan_round_tidak_dicatat_sendiri():
    """Keputusan round sudah tercatat lewat status versi (case.approved)."""
    # Arrange
    ronde = _row(case_id=KASUS_ID, case_version_id=VERSI_ID, round_no=1)

    # Act
    events = tracker_for("review_rounds").updated(ronde, {})

    # Assert
    assert events == []
    assert tracker_for("review_rounds").fields == ()


def test_nilai_kolom_diubah_jadi_aman_untuk_jsonb():
    """uuid dan Decimal tidak bisa disimpan langsung di kolom JSON."""
    # Arrange
    pemilik = uuid.UUID("00000000-0000-0000-0000-0000000000b2")

    # Act
    event = _satu(tracker_for("suites").updated(_row(), {"created_by": Change(None, pemilik)}))

    # Assert
    assert event.after == {"created_by": str(pemilik)}


# --- AI product ---------------------------------------------------------------


def test_produk_ai_didaftarkan_tanpa_kredensial():
    # Arrange
    row = _row(
        name="GPT",
        provider_type="openai_compatible",
        base_url="https://api.example.test/v1/chat/completions",
        model_name="gpt-x",
        rate_limit_per_minute=60,
        monthly_budget_idr=Decimal("1500000.00"),
        is_active=True,
        credential_encrypted=b"rahasia",
    )

    # Act
    event = _satu(tracker_for("ai_products").created(row))

    # Assert
    assert event.action == "ai_product.registered"
    assert "credential_encrypted" not in event.after
    assert event.after["monthly_budget_idr"] == "1500000.00"
    assert event.after["is_active"] is True


def test_kredensial_diganti_hanya_penanda():
    """PBI-10 AC2: nilai lama maupun baru tidak pernah tercatat."""
    # Act
    event = _satu(
        tracker_for("ai_products").updated(
            _row(), {"credential_encrypted": Change(b"lama", b"baru")}
        )
    )

    # Assert
    assert event.action == "ai_product.credential_rotated"
    assert event.before is None
    assert event.after == {"credential": "rotated"}


@pytest.mark.parametrize(
    ("baru", "action"),
    [(True, "ai_product.activated"), (False, "ai_product.deactivated")],
)
def test_produk_ai_diaktifkan_atau_dinonaktifkan(baru, action):
    # Act
    event = _satu(tracker_for("ai_products").updated(_row(), {"is_active": Change(not baru, baru)}))

    # Assert
    assert event.action == action
    assert event.after == {"is_active": baru}


def test_produk_ai_dihapus_lunak():
    """D6a: ai_product.deleted, soft delete lewat deleted_at (SCRUM-133)."""
    # Arrange
    waktu = datetime(2026, 10, 9, 2, 0, tzinfo=UTC)

    # Act
    event = _satu(tracker_for("ai_products").updated(_row(), {"deleted_at": Change(None, waktu)}))

    # Assert
    assert event.action == "ai_product.deleted"
    assert event.after == {"deleted_at": "2026-10-09T02:00:00+00:00"}


def test_produk_ai_diubah_field_non_rahasia():
    # Act
    event = _satu(
        tracker_for("ai_products").updated(_row(), {"rate_limit_per_minute": Change(60, 120)})
    )

    # Assert
    assert event.action == "ai_product.updated"
    assert event.after == {"rate_limit_per_minute": 120}


# --- User ---------------------------------------------------------------------


def test_anggota_ditambahkan():
    # Act
    event = _satu(tracker_for("users").created(_row(email="a@veritask.test", role=_Status.ACTIVE)))

    # Assert
    assert event.action == "user.added"
    assert event.after == {"email": "a@veritask.test", "role": "active"}


def test_peran_anggota_diubah():
    # Act
    event = _satu(tracker_for("users").updated(_row(), {"role": Change("author", "reviewer")}))

    # Assert
    assert event.action == "user.role_changed"
    assert (event.before, event.after) == ({"role": "author"}, {"role": "reviewer"})


@pytest.mark.parametrize(
    ("baru", "action"), [(False, "user.deactivated"), (True, "user.activated")]
)
def test_anggota_dinonaktifkan_atau_diaktifkan(baru, action):
    # Act
    event = _satu(tracker_for("users").updated(_row(), {"is_active": Change(not baru, baru)}))

    # Assert
    assert event.action == action


def test_perubahan_profil_saat_login_tidak_dicatat():
    """Nama dan zitadel_sub disinkronkan dari IdP saat login, bukan aksi pengguna."""
    # Arrange
    tracker = tracker_for("users")

    # Act + Assert
    assert "name" not in tracker.fields
    assert "zitadel_sub" not in tracker.fields
