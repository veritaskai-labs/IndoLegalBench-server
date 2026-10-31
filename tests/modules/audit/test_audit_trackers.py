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
from app.modules.providers.models import MeasurementType

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
        ("review_verdicts", AuditEntityType.REVIEW),
        ("review_assignments", AuditEntityType.REVIEW),
        ("ai_products", AuditEntityType.AI_PRODUCT),
        ("users", AuditEntityType.USER),
        ("suite_snapshots", AuditEntityType.SUITE),
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


def test_snapshot_suite_dibuat_dicatat_di_suitenya():
    # Arrange
    suite_id = uuid.UUID("00000000-0000-0000-0000-0000000000b2")
    row = _row(suite_id=suite_id, items=[object(), object()])

    # Act
    event = _satu(tracker_for("suite_snapshots").created(row))

    # Assert
    assert event.action == "suite.snapshot_created"
    assert event.entity_id == suite_id
    assert event.case_id is None
    assert event.before is None
    assert event.after == {"snapshot_id": str(ROW_ID), "case_count": 2}


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


# --- Review verdict (SCRUM-145) --------------------------------------------------


def _verdict_row(
    decision="revise",
    comment="Pasal 151 sudah diubah",
    *,
    carried_from_verdict_id=None,
):
    assignment_id = uuid.UUID("00000000-0000-0000-0000-0000000000e1")
    ronde = SimpleNamespace(case_id=KASUS_ID)
    assignment = SimpleNamespace(id=assignment_id, review_round=ronde)
    return _row(
        assignment_id=assignment_id,
        assignment=assignment,
        decision=decision,
        comment=comment,
        carried_from_verdict_id=carried_from_verdict_id,
    )


def test_verdict_dicatat_di_assignmentnya_dengan_kasus():
    # Act
    event = _satu(tracker_for("review_verdicts").created(_verdict_row()))

    # Assert
    assert event.action == "review.verdict_submitted"
    assert event.entity_id == uuid.UUID("00000000-0000-0000-0000-0000000000e1")
    assert event.case_id == KASUS_ID


def test_komentar_verdict_tidak_disalin_ke_audit():
    """D6a: after hanya decision, komentar tidak disalin."""
    # Act
    event = _satu(tracker_for("review_verdicts").created(_verdict_row()))

    # Assert
    assert event.after == {"decision": "revise"}
    assert "Pasal 151" not in str(event.after)


def test_verdict_tidak_pernah_diubah_jadi_tidak_dipantau():
    # Act + Assert
    assert tracker_for("review_verdicts").fields == ()


def test_approve_yang_dibawa_dicatat_sebagai_approval_carried():
    """SCRUM-146: jangan catat carried approve sebagai verdict_submitted Author."""
    # Arrange
    sumber = uuid.UUID("00000000-0000-0000-0000-0000000000f1")
    row = _verdict_row(decision="approve", comment=None, carried_from_verdict_id=sumber)

    # Act
    event = _satu(tracker_for("review_verdicts").created(row))

    # Assert
    assert event.action == "review.approval_carried"
    assert event.entity_id == uuid.UUID("00000000-0000-0000-0000-0000000000e1")
    assert event.case_id == KASUS_ID
    assert event.after == {
        "decision": "approve",
        "carried_from_verdict_id": str(sumber),
    }


# --- Review assignment (SCRUM-144, D6a) ----------------------------------------


def test_penugasan_baru_dicatat_per_assignment():
    """D6a: review.reviewer_assigned, entity_id = assignment id, after reviewer_id+source."""
    # Arrange
    reviewer_id = uuid.UUID("00000000-0000-0000-0000-0000000000a2")
    tugas = _row(
        reviewer_id=reviewer_id,
        source="system",
        review_round=_row(case_id=KASUS_ID),
    )

    # Act
    event = _satu(tracker_for("review_assignments").created(tugas))

    # Assert
    assert event.action == "review.reviewer_assigned"
    assert event.entity_id == ROW_ID
    assert event.case_id == KASUS_ID
    assert event.after == {"reviewer_id": str(reviewer_id), "source": "system"}


def test_case_id_penugasan_membaca_atribut_review_round():
    """Jangan andalkan __dict__ saja: load_on_pending mengisi lewat atribut.

    SCRUM-147 akan membuat assignment tanpa ReviewTableStore. Kalau tracker
    hanya membaca row.__dict__['review_round'], case_id hilang dan baris
    audit keluar dari scope reviewer (SCRUM-167).
    """

    class _TugasLewatAtribut:
        def __init__(self) -> None:
            self.id = ROW_ID
            self.reviewer_id = uuid.UUID("00000000-0000-0000-0000-0000000000a2")
            self.source = "system"
            self._ronde = _row(case_id=KASUS_ID)

        @property
        def review_round(self):
            return self._ronde

    # Arrange
    tugas = _TugasLewatAtribut()
    assert "review_round" not in tugas.__dict__

    # Act
    event = _satu(tracker_for("review_assignments").created(tugas))

    # Assert
    assert event.case_id == KASUS_ID


def test_penggantian_reviewer_dicatat():
    """D6a: review.reviewer_replaced, wajib mencatat reason."""
    # Arrange
    tugas = _row(
        status="replaced",
        end_reason="Reviewer berhalangan hadir",
        review_round=_row(case_id=KASUS_ID),
    )

    # Act
    event = _satu(
        tracker_for("review_assignments").updated(tugas, {"status": Change("active", "replaced")})
    )

    # Assert
    assert event.action == "review.reviewer_replaced"
    assert event.entity_id == ROW_ID
    assert event.case_id == KASUS_ID
    assert event.reason == "Reviewer berhalangan hadir"
    assert event.before == {"status": "active"}
    assert event.after == {"status": "replaced"}


def test_penonaktifan_reviewer_dicatat():
    """D6a: review.assignment_voided saat reviewer dinonaktifkan."""
    # Arrange
    tugas = _row(
        status="void",
        end_reason="Reviewer deactivated",
        review_round=_row(case_id=KASUS_ID),
    )

    # Act
    event = _satu(
        tracker_for("review_assignments").updated(tugas, {"status": Change("active", "void")})
    )

    # Assert
    assert event.action == "review.assignment_voided"
    assert event.entity_id == ROW_ID
    assert event.case_id == KASUS_ID
    assert event.reason == "Reviewer deactivated"
    assert event.before == {"status": "active"}
    assert event.after == {"status": "void"}


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


# --- Produk AI: jenis pengukuran (PBI-23, SCRUM-201) ---------------------------------------
# Kolom yang ditambah SCRUM-200 ikut tercatat. Kredensial, ciphertext, maupun hint-nya
# (empat karakter terakhir kredensial) tidak pernah masuk before/after.


def test_produk_manual_didaftarkan_dengan_isian_manual_saja():
    """Positif dan edge: registered memuat jenis dan isian Manual; isian Otomatis yang kosong
    tidak ditulis sebagai null."""
    # Arrange
    row = _row(
        name="LegalHero",
        measurement_type=MeasurementType.MANUAL,
        vendor_name="LegalHero",
        site_url="https://legalhero.example.test",
        access_note="Akun tim legal",
        manual_params="Paket Premium",
        is_active=True,
        provider_type=None,
        credential_encrypted=None,
    )

    # Act
    event = _satu(tracker_for("ai_products").created(row))

    # Assert
    assert event.action == "ai_product.registered"
    assert event.after == {
        "name": "LegalHero",
        "measurement_type": "manual",
        "vendor_name": "LegalHero",
        "site_url": "https://legalhero.example.test",
        "access_note": "Akun tim legal",
        "manual_params": "Paket Premium",
        "is_active": True,
    }


def test_produk_otomatis_didaftarkan_dengan_parameter_dan_harga():
    """Positif: jenis, parameter model, dan harga ikut tercatat; kredensial dan hint tidak."""
    # Arrange
    row = _row(
        name="DeepSeek",
        measurement_type=MeasurementType.AUTOMATIC,
        provider_type="openai_compatible",
        base_url="https://api.example.test/v1",
        model_name="deepseek-x",
        rate_limit_per_minute=30,
        monthly_budget_idr=Decimal("500000.00"),
        model_params={"temperature": 0.3},
        price_output_usd_per_mtok=Decimal("1.2000"),
        is_active=True,
        credential_encrypted=b"rahasia",
        credential_hint="ahia",
    )

    # Act
    event = _satu(tracker_for("ai_products").created(row))

    # Assert
    assert event.after["measurement_type"] == "automatic"
    assert event.after["model_params"] == {"temperature": 0.3}
    assert event.after["price_output_usd_per_mtok"] == "1.2000"
    assert not {"credential_encrypted", "credential_hint", "vendor_name"} & set(event.after)


def test_isian_manual_dan_parameter_tercatat_saat_diubah():
    """Positif: bentuk event updated untuk isian baru (pemantauannya diuji lewat API)."""
    # Act
    event = _satu(
        tracker_for("ai_products").updated(
            _row(),
            {
                "site_url": Change("https://lama.test", "https://baru.test"),
                "model_params": Change({"temperature": 0.3}, {"effort": "low"}),
            },
        )
    )

    # Assert
    assert event.action == "ai_product.updated"
    assert event.before == {
        "site_url": "https://lama.test",
        "model_params": {"temperature": 0.3},
    }
    assert event.after == {"site_url": "https://baru.test", "model_params": {"effort": "low"}}


def test_pindah_ke_manual_mencatat_kredensial_dihapus_tanpa_nilai():
    """Edge: kredensial yang dihapus saat pindah jenis bukan "kredensial diganti".

    Dicatat sebagai credential "stored" → "removed" di event updated yang sama:
    penanda tetap yang lolos redaksi, tanpa nilai rahasia apa pun.
    """
    # Act
    events = tracker_for("ai_products").updated(
        _row(),
        {
            "measurement_type": Change(MeasurementType.AUTOMATIC, MeasurementType.MANUAL),
            "credential_encrypted": Change(b"rahasia", None),
            "base_url": Change("https://api.example.test/v1", None),
            "vendor_name": Change(None, "Penyedia"),
        },
    )

    # Assert
    event = _satu(events)
    assert event.action == "ai_product.updated"
    assert event.before == {
        "measurement_type": "automatic",
        "credential": "stored",
        "base_url": "https://api.example.test/v1",
        "vendor_name": None,
    }
    assert event.after["credential"] == "removed"
    assert "credential_encrypted" not in event.after


def test_pindah_ke_otomatis_mencatat_kredensial_pertama_tanpa_nilai():
    """Edge: kredensial pertama saat pindah Manual ke Otomatis bukan "kredensial diganti".

    Kebalikan dari pindah ke Manual: credential "removed" → "stored" di event
    updated yang sama, bukan event credential_rotated (review #81 poin 3).
    """
    # Act
    events = tracker_for("ai_products").updated(
        _row(),
        {
            "measurement_type": Change(MeasurementType.MANUAL, MeasurementType.AUTOMATIC),
            "credential_encrypted": Change(None, b"rahasia"),
            "vendor_name": Change("Penyedia", None),
        },
    )

    # Assert
    event = _satu(events)
    assert event.action == "ai_product.updated"
    assert (event.before["credential"], event.after["credential"]) == ("removed", "stored")
    assert "credential_encrypted" not in event.after


def test_kredensial_dan_hint_tidak_pernah_dipantau():
    """Negatif (pengaman): listener hanya meneruskan kolom di fields, jadi hint (empat
    karakter terakhir kredensial) tidak boleh ada di sana; ciphertext hanya penanda."""
    # Act
    dipantau = set(tracker_for("ai_products").fields)

    # Assert
    assert "credential_hint" not in dipantau
    assert "credential_encrypted" in tracker_for("ai_products").special


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
