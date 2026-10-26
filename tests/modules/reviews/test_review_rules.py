"""Aturan murni PBI-6, SCRUM-145: keputusan round dan siapa melihat verdict.

Tanpa database. Objek assignment cukup punya status dan verdict.
"""

import uuid
from types import SimpleNamespace

import pytest

from app.modules.reviews.decision import counted_decisions, decide
from app.modules.reviews.models import AssignmentStatus, RoundDecision, RoundStatus, VerdictDecision
from app.modules.reviews.visibility import Viewer, can_open_case_review, can_see_verdict
from app.shared.security import Role

APPROVE, REVISE = VerdictDecision.APPROVE, VerdictDecision.REVISE
SAYA = uuid.UUID("00000000-0000-0000-0000-0000000000a1")
REKAN = uuid.UUID("00000000-0000-0000-0000-0000000000a2")
PENULIS = uuid.UUID("00000000-0000-0000-0000-0000000000b1")


def _tugas(status=AssignmentStatus.ACTIVE, decision=None):
    verdict = None if decision is None else SimpleNamespace(decision=decision)
    return SimpleNamespace(status=status, verdict=verdict)


# --- Keputusan round (AC6, D3 langkah 30) ---------------------------------------


@pytest.mark.parametrize(
    ("verdicts", "hasil"),
    [
        ([APPROVE, APPROVE], RoundDecision.APPROVED),
        ([APPROVE, REVISE], RoundDecision.REVISE),
        ([REVISE, APPROVE], RoundDecision.REVISE),
        ([REVISE, REVISE], RoundDecision.REVISE),
    ],
)
def test_keputusan_setelah_dua_verdict(verdicts, hasil):
    # Act + Assert
    assert decide(verdicts, required=2) is hasil


@pytest.mark.parametrize("verdicts", [[], [APPROVE], [REVISE]])
def test_belum_ada_keputusan_sebelum_semua_menilai(verdicts):
    """D2: keputusan menunggu semua verdict, termasuk saat yang pertama revise."""
    # Act + Assert
    assert decide(verdicts, required=2) is None


def test_yang_dihitung_hanya_verdict_assignment_aktif():
    """D4a: verdict dari assignment replaced/void tidak dihitung (AC7)."""
    # Arrange
    tugas = [
        _tugas(decision=APPROVE),
        _tugas(AssignmentStatus.REPLACED, REVISE),
        _tugas(AssignmentStatus.VOID, REVISE),
        _tugas(),
    ]

    # Act
    hasil = counted_decisions(tugas)

    # Assert
    assert hasil == [APPROVE]


def test_verdict_bawaan_round_sebelumnya_ikut_dihitung():
    """AC9: approve yang dibawa ke round 2 adalah verdict biasa di assignment aktif."""
    # Arrange
    tugas = [_tugas(decision=APPROVE), _tugas(decision=APPROVE)]

    # Act + Assert
    assert decide(counted_decisions(tugas), required=2) is RoundDecision.APPROVED


# --- Siapa boleh melihat verdict (AC3) -------------------------------------------


@pytest.mark.parametrize("status", [RoundStatus.OPEN, RoundStatus.DECIDED])
def test_reviewer_selalu_melihat_verdictnya_sendiri(status):
    # Act + Assert
    assert can_see_verdict(Viewer(SAYA, Role.REVIEWER), owner_id=SAYA, round_status=status)


def test_reviewer_tidak_melihat_verdict_rekan_selama_round_open():
    # Act + Assert
    assert not can_see_verdict(
        Viewer(SAYA, Role.REVIEWER), owner_id=REKAN, round_status=RoundStatus.OPEN
    )


@pytest.mark.parametrize("role", [Role.REVIEWER, Role.AUTHOR])
def test_semua_verdict_terbuka_setelah_round_diputuskan(role):
    """D3 langkah 41: kedua verdict terbuka untuk Author, A, B, Admin."""
    # Act + Assert
    assert can_see_verdict(Viewer(SAYA, role), owner_id=REKAN, round_status=RoundStatus.DECIDED)


def test_author_tidak_melihat_verdict_selama_round_open():
    # Act + Assert
    assert not can_see_verdict(
        Viewer(PENULIS, Role.AUTHOR), owner_id=REKAN, round_status=RoundStatus.OPEN
    )


def test_admin_melihat_semua_verdict_kapan_pun():
    """AC3 dan keputusan klien di SCRUM-145, menggantikan asumsi #6 di D2."""
    # Act + Assert
    assert can_see_verdict(Viewer(SAYA, Role.ADMIN), owner_id=REKAN, round_status=RoundStatus.OPEN)


# --- Siapa boleh membuka review sebuah kasus ------------------------------------


@pytest.mark.parametrize(
    ("viewer", "assigned", "boleh"),
    [
        (Viewer(SAYA, Role.ADMIN), False, True),
        (Viewer(PENULIS, Role.AUTHOR), False, True),
        (Viewer(SAYA, Role.AUTHOR), False, False),
        (Viewer(SAYA, Role.REVIEWER), True, True),
        (Viewer(SAYA, Role.REVIEWER), False, False),
        (Viewer(SAYA, Role.VIEWER), True, False),
    ],
    ids=["admin", "penulis", "author-lain", "reviewer-ditugaskan", "reviewer-lain", "viewer"],
)
def test_akses_review_kasus(viewer, assigned, boleh):
    """D5a: Author kasus sendiri, Reviewer yang ditugaskan, Admin. Viewer tidak."""
    # Act + Assert
    assert can_open_case_review(viewer, author_id=PENULIS, assigned=assigned) is boleh


# --- Kontributor (PBI-24, SCRUM-195) ----------------------------------------------


@pytest.mark.parametrize(
    ("viewer", "assigned", "boleh"),
    [
        (Viewer(PENULIS, Role.CONTRIBUTOR), False, True),
        (Viewer(SAYA, Role.CONTRIBUTOR), True, True),
        (Viewer(SAYA, Role.CONTRIBUTOR), False, False),
        (Viewer(SAYA, Role.DEVELOPER_AI), True, False),
    ],
    ids=["penulisnya", "ditugaskan", "tidak-terkait", "developer-ai"],
)
def test_kontributor_membuka_review_sebagai_penulis_atau_reviewer(viewer, assigned, boleh):
    """D10: satu peran memegang dua jalur akses; cukup salah satu relasinya terpenuhi."""
    # Act + Assert
    assert can_open_case_review(viewer, author_id=PENULIS, assigned=assigned) is boleh


def test_kontributor_melihat_verdictnya_sendiri_selama_round_open():
    # Act + Assert
    assert can_see_verdict(
        Viewer(SAYA, Role.CONTRIBUTOR), owner_id=SAYA, round_status=RoundStatus.OPEN
    )


def test_kontributor_tidak_melihat_verdict_rekan_selama_round_open():
    """Blind review tetap berlaku walau penulis dan reviewer kini satu peran (AC3)."""
    # Act + Assert
    assert not can_see_verdict(
        Viewer(PENULIS, Role.CONTRIBUTOR), owner_id=REKAN, round_status=RoundStatus.OPEN
    )
