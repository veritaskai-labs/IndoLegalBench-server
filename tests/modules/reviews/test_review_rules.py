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
# PBI-24: penulis dan reviewer sama-sama Kontributor; blind review dibedakan lewat
# pemilik verdict (owner_id), bukan lewat peran.


@pytest.mark.parametrize("status", [RoundStatus.OPEN, RoundStatus.DECIDED])
def test_reviewer_selalu_melihat_verdictnya_sendiri(status):
    # Act + Assert
    assert can_see_verdict(Viewer(SAYA, Role.CONTRIBUTOR), owner_id=SAYA, round_status=status)


def test_reviewer_tidak_melihat_verdict_rekan_selama_round_open():
    # Act + Assert
    assert not can_see_verdict(
        Viewer(SAYA, Role.CONTRIBUTOR), owner_id=REKAN, round_status=RoundStatus.OPEN
    )


@pytest.mark.parametrize("viewer_id", [PENULIS, SAYA], ids=["penulis", "reviewer-lain"])
def test_semua_verdict_terbuka_setelah_round_diputuskan(viewer_id):
    """D3 langkah 41: kedua verdict terbuka untuk penulis, A, B, Admin."""
    # Act + Assert
    assert can_see_verdict(
        Viewer(viewer_id, Role.CONTRIBUTOR), owner_id=REKAN, round_status=RoundStatus.DECIDED
    )


def test_penulis_tidak_melihat_verdict_selama_round_open():
    # Act + Assert
    assert not can_see_verdict(
        Viewer(PENULIS, Role.CONTRIBUTOR), owner_id=REKAN, round_status=RoundStatus.OPEN
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
        (Viewer(PENULIS, Role.CONTRIBUTOR), False, True),
        (Viewer(SAYA, Role.CONTRIBUTOR), True, True),
        (Viewer(SAYA, Role.CONTRIBUTOR), False, False),
        (Viewer(SAYA, Role.VIEWER), True, False),
        (Viewer(SAYA, Role.DEVELOPER_AI), True, False),
    ],
    ids=["admin", "penulis", "ditugaskan", "tidak-terkait", "viewer", "developer-ai"],
)
def test_akses_review_kasus(viewer, assigned, boleh):
    """D5a, D10: penulis kasus, Kontributor yang ditugaskan, dan Admin.

    Kontributor memegang dua jalur akses; cukup salah satu relasinya terpenuhi.
    """
    # Act + Assert
    assert can_open_case_review(viewer, author_id=PENULIS, assigned=assigned) is boleh
