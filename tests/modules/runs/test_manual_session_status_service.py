"""ManualSessionStatusService dengan penyimpanan palsu. PBI-22 (SCRUM-176) AC8, AC13; SCRUM-230.

Review #116: hitung ulang, Tandai selesai, dan halaman sesi manual lewat service di balik
port ManualStatusStore, sama seperti pembuatan sesi. Di sini aturannya diuji tanpa
database; jalurnya ke tabel diuji di test_manual_session_status.py dan
test_manual_session_detail.py.

Label: Positif = perilaku yang diharapkan, Negatif = ditolak, Edge = batas perilaku.
"""

import uuid
from datetime import UTC, datetime

import pytest

from app.modules.runs import session_ports
from app.modules.runs.models import SessionStatus
from app.shared.exceptions import ConflictError, NotFoundError

SESI = uuid.UUID("00000000-0000-0000-0000-0000000000e5")
ADMIN = uuid.UUID("00000000-0000-0000-0000-0000000000d4")
ITEM_A = uuid.UUID("00000000-0000-0000-0000-00000000000a")
ITEM_B = uuid.UUID("00000000-0000-0000-0000-00000000000b")
HARI_1 = datetime(2026, 11, 1, 9, 0, tzinfo=UTC)
HARI_3 = datetime(2026, 11, 3, 9, 0, tzinfo=UTC)
HARI_8 = datetime(2026, 11, 8, 9, 0, tzinfo=UTC)
JAM = datetime(2026, 11, 7, 10, 0, tzinfo=UTC)


def _percobaan(item, nomor, diperoleh=None):
    return session_ports.ManualAttemptRow(
        id=uuid.uuid4(),
        snapshot_item_id=item,
        repetition_no=nomor,
        filled=diperoleh is not None,
        answered_at_platform=diperoleh,
    )


class _Penyimpan:
    """ManualStatusStore palsu: status, percobaan, dan catatan setiap tulisan."""

    def __init__(self, status="not_filled", percobaan=(), ada=True) -> None:
        self.status = SessionStatus(status) if ada else None
        self.percobaan = list(percobaan)
        self.rentang: list[tuple] = []
        self.perpindahan: list[tuple] = []

    def lock_manual(self, session_id):
        return self.status

    def manual_attempts(self, session_id):
        return self.percobaan

    def set_answer_dates(self, session_id, start, end):
        self.rentang.append((start, end))

    def change_status(self, session_id, target, *, at, finalized_by=None, commit):
        self.perpindahan.append((SessionStatus(target), finalized_by, commit))

    def read_manual(self, session_id):
        if self.status is None:
            return None
        return session_ports.ManualSessionRow(
            id=SESI,
            label="LegalHero November",
            purpose="official",
            status=self.status,
            case_filter="both",
            repetitions=1,
            created_by=ADMIN,
            created_at=HARI_1,
            answer_date_from=HARI_1,
            answer_date_to=HARI_8,
            finalized_at=None,
            finalized_by=None,
            product_name="LegalHero",
            vendor_name="LegalHero",
            site_url="https://legalhero.id",
            manual_params="Premium",
        )

    def snapshot_cases(self, session_id):
        return [
            session_ports.ManualCaseRow(
                snapshot_item_id=item,
                case_id=uuid.uuid4(),
                body={"sections": {"case_code": kode, "identity.question": f"Tanya {kode}?"}},
            )
            for item, kode in ((ITEM_B, "PHK-002"), (ITEM_A, "PHK-001"))
        ]


@pytest.fixture
def layanan():
    # Diimpor lewat fixture: kelas yang belum ada membuat tes merah, bukan galat koleksi.
    from app.modules.runs.manual_session_status import ManualSessionStatusService

    return ManualSessionStatusService


def test_hitung_ulang_menulis_status_baru_tanpa_commit(layanan):
    """Positif: D8, 1 dari 2 terisi menjadi Terisi sebagian; SCRUM-231 yang commit."""
    # Arrange
    penyimpan = _Penyimpan(percobaan=[_percobaan(ITEM_A, 1, HARI_3), _percobaan(ITEM_B, 1)])

    # Act
    hasil = layanan(penyimpan).recompute(SESI, now=JAM)

    # Assert
    assert hasil == SessionStatus.PARTIALLY_FILLED
    assert penyimpan.perpindahan == [(SessionStatus.PARTIALLY_FILLED, None, False)]
    assert penyimpan.rentang == [(HARI_3, HARI_3)]


def test_hitung_ulang_tanpa_perubahan_tidak_menulis_status(layanan):
    """Edge: status yang sama tidak menulis perpindahan (dan tidak ada catatan audit)."""
    # Arrange
    penyimpan = _Penyimpan(percobaan=[_percobaan(ITEM_A, 1), _percobaan(ITEM_B, 1)])

    # Act
    hasil = layanan(penyimpan).recompute(SESI, now=JAM)

    # Assert
    assert hasil == SessionStatus.NOT_FILLED
    assert penyimpan.perpindahan == []
    assert penyimpan.rentang == [(None, None)]


@pytest.mark.parametrize(
    ("penyimpan", "galat", "kode"),
    [
        (_Penyimpan(ada=False), NotFoundError, "MANUAL_SESSION_NOT_FOUND"),
        (_Penyimpan(status="running"), ConflictError, "MANUAL_SESSION_STATUS_INVALID"),
    ],
    ids=["tidak-ada", "status-d4"],
)
def test_hitung_ulang_ditolak_untuk_sesi_yang_bukan_sesi_manual(layanan, penyimpan, galat, kode):
    """Negatif: sesi hilang 404; baris manual lama berstatus D4 409 (review #116)."""
    # Act
    with pytest.raises(galat) as tertangkap:
        layanan(penyimpan).recompute(SESI, now=JAM)

    # Assert
    assert tertangkap.value.code == kode
    assert penyimpan.perpindahan == []


def test_tandai_selesai_menulis_pelaku_dan_commit(layanan):
    """Positif: AC8, D9, Lengkap menjadi Selesai dengan pelakunya, dalam transaksi sendiri."""
    # Arrange
    penyimpan = _Penyimpan(status="complete")

    # Act
    hasil = layanan(penyimpan).finalize(SESI, finalized_by=ADMIN, now=JAM)

    # Assert
    assert penyimpan.perpindahan == [(SessionStatus.FINALIZED, ADMIN, True)]
    assert (hasil.status, hasil.finalized_by, hasil.finalized_at) == (
        SessionStatus.FINALIZED,
        ADMIN,
        JAM,
    )


@pytest.mark.parametrize(
    ("penyimpan", "galat", "kode"),
    [
        (_Penyimpan(status="partially_filled"), ConflictError, "MANUAL_SESSION_INCOMPLETE"),
        (_Penyimpan(ada=False), NotFoundError, "MANUAL_SESSION_NOT_FOUND"),
    ],
    ids=["belum-lengkap", "tidak-ada"],
)
def test_tandai_selesai_ditolak_tanpa_menulis(layanan, penyimpan, galat, kode):
    """Negatif: AC8, Selesai hanya untuk sesi manual yang semua jawabannya terisi."""
    # Act
    with pytest.raises(galat) as tertangkap:
        layanan(penyimpan).finalize(SESI, finalized_by=ADMIN, now=JAM)

    # Assert
    assert tertangkap.value.code == kode
    assert penyimpan.perpindahan == []


def test_halaman_sesi_disusun_dari_baris_penyimpanan(layanan):
    """Positif: AC8, AC13, kartu urut kode, progres seluruh sesi, peringatan dari batas hari."""
    # Arrange
    penyimpan = _Penyimpan(percobaan=[_percobaan(ITEM_A, 1, HARI_1), _percobaan(ITEM_B, 1)])

    # Act
    hasil = layanan(penyimpan).detail(SESI, unfilled_only=False, warning_days=2)

    # Assert
    assert [kasus.case_code for kasus in hasil.cases] == ["PHK-001", "PHK-002"]
    assert [kasus.filled for kasus in hasil.cases] == [True, False]
    assert (hasil.filled_answers, hasil.total_answers) == (1, 2)
    assert hasil.date_range_warning is True
    assert hasil.manual_params == "Premium"


def test_filter_belum_terisi_tidak_mengubah_progres(layanan):
    """Positif: AC8, filter hanya menyaring kartu; X dari N tetap seluruh sesi."""
    # Arrange
    penyimpan = _Penyimpan(percobaan=[_percobaan(ITEM_A, 1, HARI_1), _percobaan(ITEM_B, 1)])

    # Act
    hasil = layanan(penyimpan).detail(SESI, unfilled_only=True, warning_days=None)

    # Assert
    assert [kasus.case_code for kasus in hasil.cases] == ["PHK-002"]
    assert (hasil.filled_answers, hasil.total_answers, hasil.date_range_warning) == (1, 2, False)


def test_halaman_sesi_yang_tidak_ada_404(layanan):
    """Negatif: id yang bukan sesi manual."""
    # Act
    with pytest.raises(NotFoundError) as galat:
        layanan(_Penyimpan(ada=False)).detail(SESI, unfilled_only=False, warning_days=None)

    # Assert
    assert galat.value.code == "MANUAL_SESSION_NOT_FOUND"
