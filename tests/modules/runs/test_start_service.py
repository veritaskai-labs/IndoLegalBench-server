"""Memulai sesi pengukuran: validasi ulang lalu masuk antrean. SCRUM-76 SCRUM-207.

PBI-11 AC1 dan AC5: saat Mulai ditekan, produk aktif dan otomatis, kasus layak,
dan anggaran diperiksa ulang dengan aturan yang sama seperti ringkasan
(SummaryService, SCRUM-206), karena keadaan bisa berubah di antara Lihat
ringkasan dan Mulai. AC3: sesi baru selalu Menunggu (D3 langkah 20-23).

Kontrak #90 (SCRUM-204): sebelum sesi disimpan, baris produk dikunci FOR SHARE
dan jenisnya dibaca ulang, supaya ganti jenis ke Manual yang terjadi bersamaan
tidak meloloskan sesi otomatis untuk produk Manual (D1 C6, PBI-23 AC4). Kuncinya
diuji di PostgreSQL di tests/integration/test_start_session_lock_pg.py.

Semua dependency palsu: ringkasan, kunci produk, dan penyimpanan sesi.

Label: Positif = sesi masuk antrean, Negatif = sesi ditolak, Edge = batas perilaku.
"""

import uuid
from datetime import UTC, datetime

import pytest

from app.modules.runs import schemas, service, session_ports
from app.shared.exceptions import NotFoundError, ValidationError

SUITE_A = uuid.UUID("00000000-0000-0000-0000-0000000000a1")
SUITE_B = uuid.UUID("00000000-0000-0000-0000-0000000000b2")
PRODUCT_ID = uuid.UUID("00000000-0000-0000-0000-0000000000c3")
ADMIN_ID = uuid.UUID("00000000-0000-0000-0000-0000000000d4")
SESSION_ID = uuid.UUID("00000000-0000-0000-0000-0000000000e5")
JAM = datetime(2026, 11, 2, 3, 0, tzinfo=UTC)


class _Ringkasan:
    """Ringkasan palsu: mencatat permintaan dan bisa dibuat menolak seperti SCRUM-206."""

    def __init__(self, galat: Exception | None = None) -> None:
        self.galat = galat
        self.diminta: list[schemas.SummaryRequest] = []

    def summarize(self, request):
        self.diminta.append(request)
        if self.galat is not None:
            raise self.galat
        return None


class _Antrean:
    """Penyimpanan sesi palsu yang mencatat setiap sesi baru."""

    def __init__(self) -> None:
        self.dibuat: list[session_ports.NewSession] = []

    def create_queued(self, new):
        self.dibuat.append(new)
        return session_ports.QueuedSession(
            id=SESSION_ID, status=schemas.SessionStatus.QUEUED, queued_at=JAM
        )


def _permintaan(**ubah) -> schemas.StartRequest:
    nilai = {
        "suite_ids": [SUITE_A, SUITE_B],
        "product_id": PRODUCT_ID,
        "case_filter": "dev",
        "repeat_count": 5,
        "label": "Run Oktober",
        "purpose": "trial",
    }
    nilai.update(ubah)
    return schemas.StartRequest(**nilai)


class _Kunci:
    """Kunci produk palsu: jenis yang terbaca setelah kunci didapat, atau galat produk hilang."""

    def __init__(self, jenis: str = "automatic", galat: Exception | None = None) -> None:
        self.jenis = jenis
        self.galat = galat
        self.dikunci: list[uuid.UUID] = []

    def lock_measurement_type(self, product_id):
        self.dikunci.append(product_id)
        if self.galat is not None:
            raise self.galat
        return self.jenis


def _service(ringkasan=None, antrean=None, kunci=None):
    ringkasan = ringkasan or _Ringkasan()
    antrean = antrean or _Antrean()
    start = service.StartSessionService(
        summary=ringkasan, sessions=antrean, product_lock=kunci or _Kunci()
    )
    return start, ringkasan, antrean


def test_sesi_valid_masuk_antrean_dengan_semua_pilihan_pemulai():
    """Positif: pilihan halaman Buat Pengukuran tersimpan apa adanya di sesi baru."""
    # Arrange
    start, _, antrean = _service()

    # Act
    start.start(_permintaan(), created_by=ADMIN_ID)

    # Assert
    assert antrean.dibuat == [
        session_ports.NewSession(
            label="Run Oktober",
            purpose=schemas.MeasurementPurpose.TRIAL,
            product_id=PRODUCT_ID,
            suite_ids=(SUITE_A, SUITE_B),
            case_filter=schemas.CaseFilter.DEV,
            repetitions=5,
            created_by=ADMIN_ID,
        )
    ]


def test_jawaban_berisi_id_status_menunggu_dan_waktu_antrean():
    """Positif: 201 {session_id, status: queued} seperti D3 langkah 23, plus queued_at (nama D2)."""
    # Arrange
    start, _, _ = _service()

    # Act
    hasil = start.start(_permintaan(), created_by=ADMIN_ID)

    # Assert
    assert hasil == schemas.StartResponse(
        session_id=SESSION_ID, status=schemas.SessionStatus.QUEUED, queued_at=JAM
    )


def test_validasi_ulang_memakai_permintaan_yang_sama_dengan_ringkasan():
    """Positif: aturan ringkasan diperiksa ulang tepat sekali, dengan isian yang dikirim."""
    # Arrange
    start, ringkasan, _ = _service()
    permintaan = _permintaan()

    # Act
    start.start(permintaan, created_by=ADMIN_ID)

    # Assert
    assert ringkasan.diminta == [permintaan]


@pytest.mark.parametrize(
    "galat",
    [
        # Negatif: AC5, perkiraan biaya melebihi sisa anggaran sejak ringkasan dibuka.
        ValidationError("melebihi anggaran", code="BUDGET_EXCEEDED"),
        # Negatif: AC1, produk dinonaktifkan Admin sebelum Mulai ditekan.
        ValidationError("nonaktif", code="PRODUCT_INACTIVE"),
        # Negatif: produk manual tidak boleh diukur otomatis (D2 constraint sesi).
        ValidationError("manual", code="PRODUCT_NOT_AUTOMATIC"),
        # Negatif: kasus layak habis, misalnya suite diubah.
        ValidationError("kosong", code="NO_ELIGIBLE_CASES"),
        # Negatif: produk dihapus.
        NotFoundError("tidak ada", code="AI_PRODUCT_NOT_FOUND"),
    ],
)
def test_validasi_ulang_gagal_tidak_membuat_sesi(galat):
    """Negatif: galat ringkasan diteruskan apa adanya dan tidak ada sesi yang dibuat."""
    # Arrange
    start, _, antrean = _service(ringkasan=_Ringkasan(galat))

    # Act
    with pytest.raises(type(galat)) as tertangkap:
        start.start(_permintaan(), created_by=ADMIN_ID)

    # Assert
    assert tertangkap.value is galat
    assert antrean.dibuat == []


def test_suite_ganda_digabung_sebelum_disimpan():
    """Edge: suite yang dipilih dua kali tersimpan sekali, mengikuti SummaryRequest."""
    # Arrange
    start, _, antrean = _service()

    # Act
    start.start(_permintaan(suite_ids=[SUITE_A, SUITE_A, SUITE_B]), created_by=ADMIN_ID)

    # Assert
    assert antrean.dibuat[0].suite_ids == (SUITE_A, SUITE_B)


def test_produk_dikunci_sebelum_sesi_disimpan():
    """Positif: kontrak #90, baris produk dikunci untuk produk yang dipilih sebelum INSERT sesi."""
    # Arrange
    kunci = _Kunci()
    start, _, antrean = _service(kunci=kunci)

    # Act
    start.start(_permintaan(), created_by=ADMIN_ID)

    # Assert
    assert kunci.dikunci == [PRODUCT_ID]
    assert len(antrean.dibuat) == 1


def test_jenis_berubah_ke_manual_setelah_kunci_sesi_ditolak():
    """Negatif: D1 C6, ganti jenis yang menang lebih dulu membuat Mulai ditolak tanpa sesi."""
    # Arrange
    start, _, antrean = _service(kunci=_Kunci(jenis="manual"))

    # Act
    with pytest.raises(ValidationError) as tertangkap:
        start.start(_permintaan(), created_by=ADMIN_ID)

    # Assert
    assert tertangkap.value.code == "PRODUCT_NOT_AUTOMATIC"
    assert antrean.dibuat == []


def test_produk_dihapus_saat_dikunci_tidak_membuat_sesi():
    """Negatif: produk hilang di antara ringkasan dan kunci; 404 diteruskan, tidak ada sesi."""
    # Arrange
    hilang = NotFoundError("Produk AI tidak ditemukan", code="AI_PRODUCT_NOT_FOUND")
    start, _, antrean = _service(kunci=_Kunci(galat=hilang))

    # Act
    with pytest.raises(NotFoundError):
        start.start(_permintaan(), created_by=ADMIN_ID)

    # Assert
    assert antrean.dibuat == []


def test_ringkasan_gagal_tidak_sampai_mengunci_produk():
    """Edge: validasi murah lebih dulu; kunci hanya diambil untuk permintaan yang lolos ringkasan."""
    # Arrange
    kunci = _Kunci()
    galat = ValidationError("melebihi anggaran", code="BUDGET_EXCEEDED")
    start, _, _ = _service(ringkasan=_Ringkasan(galat), kunci=kunci)

    # Act
    with pytest.raises(ValidationError):
        start.start(_permintaan(), created_by=ADMIN_ID)

    # Assert
    assert kunci.dikunci == []


def test_provider_penilai_ikut_tersimpan_di_sesi():
    """Positif: PBI-14 AC6, penilai dipilih saat sesi dibuat dan disimpan bersama sesi."""
    # Arrange
    judge = uuid.UUID("00000000-0000-0000-0000-0000000000f8")
    start, _, antrean = _service()

    # Act
    start.start(_permintaan(judge_provider_id=judge), created_by=ADMIN_ID)

    # Assert
    assert antrean.dibuat[0].judge_provider_id == judge
