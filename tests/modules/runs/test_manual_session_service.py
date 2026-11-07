"""Membuat sesi pengukuran manual. PBI-22 (SCRUM-176) AC1, AC2; SCRUM-230.

AC1: Admin memilih satu produk berjenis manual (produk otomatis ditolak), satu atau
beberapa suite, filter dev/test/both, dan jumlah percobaan 1-5 (standar 1). Hanya kasus
yang disetujui dan bertag yang ikut. AC2: snapshot kasus dibuat saat itu juga dengan
mekanisme PBI-11, bukan menunggu antrean (D8 "snapshot dibuat saat itu juga").

Sesi baru langsung Belum terisi (D8): tanpa antrean, biaya, maupun anggaran (AC10).
Label dan tujuan tetap wajib seperti sesi otomatis, karena kolom D2-nya NOT NULL.

Kontrak #90: produk dikunci dan jenisnya dibaca ulang di transaksi yang sama dengan
INSERT sesi, supaya ganti jenis ke Otomatis yang bersamaan tidak meloloskan sesi manual.

Semua dependency palsu: kunci produk, salinan produk, sumber snapshot, penyimpanan sesi.

Label: Positif = sesi dibuat, Negatif = sesi ditolak, Edge = batas perilaku.
"""

import uuid

import pytest
from pydantic import ValidationError as PydanticValidationError

from app.modules.runs import schemas, session_ports
from app.shared.exceptions import NotFoundError, ValidationError

SUITE_A = uuid.UUID("00000000-0000-0000-0000-0000000000a1")
SUITE_B = uuid.UUID("00000000-0000-0000-0000-0000000000b2")
PRODUCT_ID = uuid.UUID("00000000-0000-0000-0000-0000000000c3")
ADMIN_ID = uuid.UUID("00000000-0000-0000-0000-0000000000d4")
SESSION_ID = uuid.UUID("00000000-0000-0000-0000-0000000000e5")


def _salinan_justisio() -> session_ports.ProductCopy:
    """Salinan produk manual: penyedia dan situs ikut, kolom API kosong (PBI-23 AC3).

    Fungsi, bukan konstanta: kolom vendor yang belum ada membuat tes merah, bukan galat koleksi.
    """
    return session_ports.ProductCopy(
        product_id=PRODUCT_ID,
        name="Justisio",
        measurement_type="manual",
        provider_type=None,
        base_url=None,
        model_name=None,
        model_params=None,
        price_input_usd_per_mtok=None,
        price_output_usd_per_mtok=None,
        monthly_budget_idr=None,
        vendor_name="Justisio",
        site_url="https://justisio.com",
    )


def _item(split: str) -> session_ports.SnapshotItem:
    return session_ports.SnapshotItem(
        case_id=uuid.uuid4(), case_version_id=uuid.uuid4(), split_tag=split, content={}
    )


DEV_1, DEV_2, TEST_1 = _item("dev"), _item("dev"), _item("test")


class _Kunci:
    """Kunci produk palsu: jenis yang terbaca setelah kunci didapat, atau galat produk hilang."""

    def __init__(self, jenis: str = "manual", galat: Exception | None = None) -> None:
        self.jenis = jenis
        self.galat = galat

    def lock_measurement_type(self, product_id):
        if self.galat is not None:
            raise self.galat
        return self.jenis


class _Salinan:
    """Salinan produk palsu; galat meniru ProductCopyAdapter (nonaktif atau terhapus)."""

    def __init__(self, galat: Exception | None = None) -> None:
        self.galat = galat

    def copy(self, product_id):
        if self.galat is not None:
            raise self.galat
        return _salinan_justisio()


class _Snapshot:
    """Kasus disetujui terbaru per suite, urut tetap seperti SnapshotSourceAdapter."""

    def __init__(self, items=(DEV_1, TEST_1, DEV_2)) -> None:
        self.items = list(items)
        self.diminta: list[tuple] = []

    def approved_items(self, suite_ids):
        self.diminta.append(tuple(suite_ids))
        return self.items


class _Penyimpan:
    """Penyimpanan sesi manual palsu yang mencatat sesi, kasus, dan salinan produknya."""

    def __init__(self) -> None:
        self.dibuat: list[tuple] = []

    def create_manual(self, new, *, items, product):
        self.dibuat.append((new, list(items), product))
        return SESSION_ID


def _permintaan(**ubah) -> "schemas.ManualStartRequest":
    nilai = {
        "suite_ids": [SUITE_A, SUITE_B],
        "product_id": PRODUCT_ID,
        "case_filter": "dev",
        "repeat_count": 2,
        "label": "LegalHero November",
        "purpose": "official",
    }
    nilai.update(ubah)
    return schemas.ManualStartRequest(**nilai)


@pytest.fixture
def modul():
    # Diimpor lewat fixture: modul yang belum ada membuat tes merah, bukan galat koleksi.
    from app.modules.runs import manual_session

    return manual_session


def _service(modul, *, kunci=None, salinan=None, snapshot=None, penyimpan=None):
    penyimpan = penyimpan or _Penyimpan()
    layanan = modul.ManualSessionService(
        sessions=penyimpan,
        snapshots=snapshot or _Snapshot(),
        products=salinan or _Salinan(),
        product_lock=kunci or _Kunci(),
    )
    return layanan, penyimpan


def test_sesi_manual_dibuat_belum_terisi_dengan_pilihan_admin(modul):
    """Positif: AC1, pilihan Admin tersimpan dan sesi langsung Belum terisi (D8)."""
    # Arrange
    layanan, penyimpan = _service(modul)

    # Act
    hasil = layanan.create(_permintaan(), created_by=ADMIN_ID)

    # Assert
    sesi, _, _ = penyimpan.dibuat[0]
    assert sesi == session_ports.NewSession(
        label="LegalHero November",
        purpose=schemas.MeasurementPurpose.OFFICIAL,
        product_id=PRODUCT_ID,
        suite_ids=(SUITE_A, SUITE_B),
        case_filter=schemas.CaseFilter.DEV,
        repetitions=2,
        created_by=ADMIN_ID,
    )
    assert hasil == schemas.ManualStartResponse(
        session_id=SESSION_ID,
        status=schemas.SessionStatus.NOT_FILLED,
        case_count=2,
        repetitions=2,
        total_answers=4,
    )


@pytest.mark.parametrize(
    ("filter_kasus", "diharapkan"),
    [("dev", [DEV_1, DEV_2]), ("test", [TEST_1]), ("both", [DEV_1, TEST_1, DEV_2])],
)
def test_snapshot_dibuat_saat_itu_juga_sesuai_filter(modul, filter_kasus, diharapkan):
    """Positif: AC1, AC2, hanya kasus bertag sesuai filter yang dibekukan, urutannya tetap."""
    # Arrange
    layanan, penyimpan = _service(modul)

    # Act
    layanan.create(_permintaan(case_filter=filter_kasus), created_by=ADMIN_ID)

    # Assert
    _, kasus, produk = penyimpan.dibuat[0]
    assert kasus == diharapkan
    assert produk == _salinan_justisio()


def test_produk_otomatis_ditolak(modul):
    """Negatif: AC1, sesi manual hanya untuk produk berjenis manual."""
    # Arrange
    layanan, penyimpan = _service(modul, kunci=_Kunci(jenis="automatic"))

    # Act
    with pytest.raises(ValidationError) as galat:
        layanan.create(_permintaan(), created_by=ADMIN_ID)

    # Assert
    assert galat.value.code == "PRODUCT_NOT_MANUAL"
    assert penyimpan.dibuat == []


@pytest.mark.parametrize(
    ("kunci", "salinan", "jenis_galat", "kode"),
    [
        (
            _Kunci(galat=NotFoundError("x", code="AI_PRODUCT_NOT_FOUND")),
            None,
            NotFoundError,
            "AI_PRODUCT_NOT_FOUND",
        ),
        (
            None,
            _Salinan(galat=ValidationError("x", code="PRODUCT_INACTIVE")),
            ValidationError,
            "PRODUCT_INACTIVE",
        ),
    ],
    ids=["terhapus", "nonaktif"],
)
def test_produk_hilang_atau_nonaktif_ditolak(modul, kunci, salinan, jenis_galat, kode):
    """Negatif: produk yang dihapus atau dinonaktifkan tidak bisa dicatat manual."""
    # Arrange
    layanan, penyimpan = _service(modul, kunci=kunci, salinan=salinan)

    # Act
    with pytest.raises(jenis_galat) as galat:
        layanan.create(_permintaan(), created_by=ADMIN_ID)

    # Assert
    assert galat.value.code == kode
    assert penyimpan.dibuat == []


def test_tanpa_kasus_layak_ditolak(modul):
    """Negatif: AC1, suite tanpa kasus disetujui dan bertag sesuai filter tidak membuat sesi."""
    # Arrange
    layanan, penyimpan = _service(modul, snapshot=_Snapshot(items=[TEST_1]))

    # Act
    with pytest.raises(ValidationError) as galat:
        layanan.create(_permintaan(case_filter="dev"), created_by=ADMIN_ID)

    # Assert
    assert galat.value.code == "NO_ELIGIBLE_CASES"
    assert penyimpan.dibuat == []


@pytest.mark.parametrize("nilai", [None, "tanpa"])
def test_jumlah_percobaan_standar_satu(nilai):
    """Positif: AC1, percobaan standar 1 untuk sesi manual (sesi otomatis standar 3)."""
    # Arrange
    isian = {} if nilai == "tanpa" else {"repeat_count": None}

    # Act
    permintaan = schemas.ManualStartRequest(
        suite_ids=[SUITE_A], product_id=PRODUCT_ID, label="L", purpose="trial", **isian
    )

    # Assert
    assert permintaan.repeat_count == 1


@pytest.mark.parametrize("jumlah", [0, 6])
def test_jumlah_percobaan_di_luar_satu_sampai_lima_ditolak(jumlah):
    """Negatif: AC1, maksimum 5 percobaan per kasus, minimal 1."""
    # Act / Assert
    with pytest.raises(PydanticValidationError):
        _permintaan(repeat_count=jumlah)


def test_label_kosong_ditolak():
    """Negatif: label wajib seperti sesi otomatis, spasi saja dianggap kosong."""
    # Act / Assert
    with pytest.raises(PydanticValidationError):
        _permintaan(label="   ")
