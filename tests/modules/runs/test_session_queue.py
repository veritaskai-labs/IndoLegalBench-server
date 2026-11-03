"""Giliran antrean: sesi Menunggu tertua mulai berjalan. SCRUM-76 SCRUM-207.

PBI-11 AC3: hanya satu pengukuran berjalan, yang lain mengantre dan jalan
otomatis saat gilirannya. AC2 dan AC6: saat sesi mulai berjalan, kasus
dibekukan (hanya yang disetujui dan bertag sesuai filter, versi disetujui
terbaru) dan data produk disalin, dalam satu langkah. Alurnya D3 langkah
24-27; "satu langkah" dipenuhi SessionQueueRepository dengan satu transaksi.

Sesi yang tidak bisa dimulai saat gilirannya (produk sudah dihapus, atau tidak
ada lagi kasus layak) ditandai failed (D4: kesalahan fatal) dan antrean lanjut
ke sesi berikutnya, supaya satu sesi rusak tidak menahan antrean selamanya.

SessionQueue memenuhi QueuePort milik worker SCRUM-208 (take_next), jadi
run_next bisa langsung memakainya. Semua dependency di sini palsu.

Label: Positif = sesi mulai berjalan, Negatif = tidak ada yang dijalankan,
Edge = batas perilaku.
"""

import logging
import uuid
from datetime import UTC, datetime
from decimal import Decimal

from app.modules.runs import schemas, service, session_ports, worker
from app.shared.exceptions import NotFoundError

SUITE_A = uuid.UUID("00000000-0000-0000-0000-0000000000a1")
PRODUCT_ID = uuid.UUID("00000000-0000-0000-0000-0000000000c3")
ADMIN_ID = uuid.UUID("00000000-0000-0000-0000-0000000000d4")
SESI_LAMA = uuid.UUID("00000000-0000-0000-0000-0000000000e5")
JAM = datetime(2026, 11, 1, 3, 0, tzinfo=UTC)


def _item(kode: str, tag: str) -> session_ports.SnapshotItem:
    return session_ports.SnapshotItem(
        case_id=uuid.uuid5(uuid.NAMESPACE_URL, kode),
        case_version_id=uuid.uuid5(uuid.NAMESPACE_DNS, kode),
        split_tag=tag,
        content={"sections": {"case_code": kode, "split_tag": tag}},
    )


# Urutan sengaja campur dev dan test, supaya filter terbukti tidak mengubah urutan.
ITEMS = [_item("PHK-001", "dev"), _item("PHK-002", "test"), _item("PHK-003", "dev")]

SALINAN = session_ports.ProductCopy(
    product_id=PRODUCT_ID,
    name="AiYU",
    measurement_type="automatic",
    provider_type="openai_compatible",
    base_url="https://api.example.test/v1/chat/completions",
    model_name="deepseek-flash",
    model_params={"temperature": 0.3},
    rate_limit_per_minute=30,
    price_input_usd_per_mtok=Decimal("1"),
    price_output_usd_per_mtok=Decimal("10"),
    monthly_budget_idr=Decimal("500000.00"),
)


def _menunggu(
    case_filter=schemas.CaseFilter.BOTH, *, sesi_id=SESI_LAMA, product_id=PRODUCT_ID
) -> session_ports.WaitingSession:
    return session_ports.WaitingSession(
        id=sesi_id,
        new=session_ports.NewSession(
            label="Run Oktober",
            purpose=schemas.MeasurementPurpose.OFFICIAL,
            product_id=product_id,
            suite_ids=(SUITE_A,),
            case_filter=case_filter,
            repetitions=3,
            created_by=ADMIN_ID,
        ),
    )


class _Penyimpanan:
    """Penyimpanan sesi palsu: antrean berurutan, bisa ada sesi berjalan atau kalah balapan.

    menunggu boleh satu sesi atau daftar; fail mengeluarkan sesi dari antrean
    seperti UPDATE status = failed di tabel.
    """

    def __init__(self, *, berjalan=False, menunggu=None, start_berhasil=True) -> None:
        self.berjalan = berjalan
        if menunggu is None:
            menunggu = []
        self.menunggu = list(menunggu) if isinstance(menunggu, list) else [menunggu]
        self.start_berhasil = start_berhasil
        self.dimulai: list[tuple] = []
        self.gagal: list[tuple] = []

    def running_exists(self) -> bool:
        return self.berjalan

    def oldest_queued(self):
        return self.menunggu[0] if self.menunggu else None

    def fail(self, session_id, *, finished_at) -> bool:
        self.gagal.append((session_id, finished_at))
        self.menunggu = [sesi for sesi in self.menunggu if sesi.id != session_id]
        return True

    def start(self, session_id, *, items, product, started_at) -> bool:
        self.dimulai.append((session_id, items, product, started_at))
        return self.start_berhasil


class _Snapshot:
    def __init__(self, items=None) -> None:
        self.items = ITEMS if items is None else items
        self.diminta: list[tuple[uuid.UUID, ...]] = []

    def approved_items(self, suite_ids):
        self.diminta.append(tuple(suite_ids))
        return list(self.items)


class _Produk:
    """Produk palsu; id di `terhapus` menjawab seperti providers_service untuk produk yang dihapus."""

    def __init__(self, terhapus=()) -> None:
        self.terhapus = set(terhapus)
        self.diminta: list[uuid.UUID] = []

    def copy(self, product_id):
        self.diminta.append(product_id)
        if product_id in self.terhapus:
            raise NotFoundError("Produk AI tidak ditemukan")
        return SALINAN


def _antrean(penyimpanan, snapshot=None, produk=None):
    snapshot, produk = snapshot or _Snapshot(), produk or _Produk()
    antrean = service.SessionQueue(
        sessions=penyimpanan, snapshots=snapshot, products=produk, clock=lambda: JAM
    )
    return antrean, snapshot, produk


def test_sesi_menunggu_tertua_mulai_berjalan_dengan_snapshot_dan_salinan_produk():
    """Positif: tidak ada sesi berjalan, jadi sesi tertua dijalankan beserta snapshot dan salinannya."""
    # Arrange
    penyimpanan = _Penyimpanan(menunggu=_menunggu())
    antrean, snapshot, produk = _antrean(penyimpanan)

    # Act
    hasil = antrean.take_next()

    # Assert
    assert hasil == SESI_LAMA
    assert penyimpanan.dimulai == [(SESI_LAMA, ITEMS, SALINAN, JAM)]
    assert snapshot.diminta == [(SUITE_A,)]
    assert produk.diminta == [PRODUCT_ID]


def test_filter_dev_hanya_membekukan_kasus_dev_dengan_urutan_tetap():
    """Positif: AC2, kasus yang tidak sesuai filter tidak ikut dibekukan."""
    # Arrange
    penyimpanan = _Penyimpanan(menunggu=_menunggu(schemas.CaseFilter.DEV))
    antrean, _, _ = _antrean(penyimpanan)

    # Act
    antrean.take_next()

    # Assert
    items = penyimpanan.dimulai[0][1]
    assert [item.content["sections"]["case_code"] for item in items] == ["PHK-001", "PHK-003"]


def test_filter_test_hanya_membekukan_kasus_test():
    """Positif: AC2, pilihan test saja."""
    # Arrange
    penyimpanan = _Penyimpanan(menunggu=_menunggu(schemas.CaseFilter.TEST))
    antrean, _, _ = _antrean(penyimpanan)

    # Act
    antrean.take_next()

    # Assert
    assert [item.split_tag for item in penyimpanan.dimulai[0][1]] == ["test"]


def test_item_snapshot_menyimpan_id_kasus_live_sebagai_referensi():
    """Positif: AC6, field id di snapshot berisi reference id kasus live, bukan id baris snapshot."""
    # Arrange
    penyimpanan = _Penyimpanan(menunggu=_menunggu())
    antrean, _, _ = _antrean(penyimpanan)

    # Act
    antrean.take_next()

    # Assert
    pertama = penyimpanan.dimulai[0][1][0]
    assert pertama.case_id == uuid.uuid5(uuid.NAMESPACE_URL, "PHK-001")
    assert pertama.case_version_id == uuid.uuid5(uuid.NAMESPACE_DNS, "PHK-001")


def test_ada_sesi_berjalan_maka_tidak_ada_yang_dijalankan_dan_tidak_ada_snapshot():
    """Negatif: AC3, hanya satu sesi berjalan; snapshot belum diambil sampai giliran tiba."""
    # Arrange
    penyimpanan = _Penyimpanan(berjalan=True, menunggu=_menunggu())
    antrean, snapshot, produk = _antrean(penyimpanan)

    # Act
    hasil = antrean.take_next()

    # Assert
    assert hasil is None
    assert penyimpanan.dimulai == []
    assert snapshot.diminta == []
    assert produk.diminta == []


def test_antrean_kosong_mengembalikan_none():
    """Edge: tidak ada sesi Menunggu, worker tidak mendapat apa-apa."""
    # Arrange
    antrean, snapshot, _ = _antrean(_Penyimpanan(menunggu=None))

    # Act + Assert
    assert antrean.take_next() is None
    assert snapshot.diminta == []


def test_kalah_balapan_dengan_worker_lain_mengembalikan_none():
    """Edge: sesi lain sempat berjalan lebih dulu; penyimpanan menolak, sesi tetap Menunggu."""
    # Arrange
    penyimpanan = _Penyimpanan(menunggu=_menunggu(), start_berhasil=False)
    antrean, _, _ = _antrean(penyimpanan)

    # Act
    hasil = antrean.take_next()

    # Assert
    assert hasil is None
    assert len(penyimpanan.dimulai) == 1


def test_run_next_worker_memakai_antrean_ini_tanpa_adaptor_tambahan():
    """Positif: SessionQueue memenuhi QueuePort SCRUM-208; antrean kosong berarti run_next False."""
    # Arrange
    antrean, _, _ = _antrean(_Penyimpanan(menunggu=None))

    # Act
    hasil = worker.run_next(antrean, store=None, adapter=None)

    # Assert
    assert hasil is False


def test_tanpa_jam_suntikan_waktu_mulai_memakai_utc_sekarang():
    """Edge: started_at dari jam bawaan selalu ber-zona UTC, supaya tidak tergeser ke WIB."""
    # Arrange
    penyimpanan = _Penyimpanan(menunggu=_menunggu())
    antrean = service.SessionQueue(sessions=penyimpanan, snapshots=_Snapshot(), products=_Produk())
    sebelum = datetime.now(UTC)

    # Act
    antrean.take_next()

    # Assert
    started_at = penyimpanan.dimulai[0][3]
    assert started_at.tzinfo is UTC
    assert started_at >= sebelum


SESI_BARU = uuid.UUID("00000000-0000-0000-0000-0000000000f6")
PRODUK_TERHAPUS = uuid.UUID("00000000-0000-0000-0000-0000000000f7")


def test_produk_terhapus_sesi_gagal_dan_antrean_lanjut_ke_sesi_berikutnya():
    """Negatif: produk dihapus setelah Mulai; sesi itu failed, sesi berikutnya tetap berjalan."""
    # Arrange
    rusak = _menunggu(product_id=PRODUK_TERHAPUS)
    sehat = _menunggu(sesi_id=SESI_BARU)
    penyimpanan = _Penyimpanan(menunggu=[rusak, sehat])
    antrean, _, _ = _antrean(penyimpanan, produk=_Produk(terhapus={PRODUK_TERHAPUS}))

    # Act
    hasil = antrean.take_next()

    # Assert
    assert penyimpanan.gagal == [(SESI_LAMA, JAM)]
    assert hasil == SESI_BARU
    assert [mulai[0] for mulai in penyimpanan.dimulai] == [SESI_BARU]


def test_tanpa_kasus_layak_saat_giliran_sesi_gagal_bukan_jalan_kosong():
    """Negatif: kasus layak habis sejak Mulai (filter test, tidak ada kasus test); sesi failed."""
    # Arrange
    penyimpanan = _Penyimpanan(menunggu=_menunggu(schemas.CaseFilter.TEST))
    antrean, _, _ = _antrean(penyimpanan, snapshot=_Snapshot(items=[_item("PHK-001", "dev")]))

    # Act
    hasil = antrean.take_next()

    # Assert
    assert hasil is None
    assert penyimpanan.gagal == [(SESI_LAMA, JAM)]
    assert penyimpanan.dimulai == []


def test_semua_sesi_rusak_antrean_habis_tanpa_macet():
    """Edge: setiap sesi rusak dikeluarkan sekali, lalu take_next berhenti dengan None."""
    # Arrange
    sesi = [_menunggu(product_id=PRODUK_TERHAPUS, sesi_id=uuid.uuid4()) for _ in range(3)]
    penyimpanan = _Penyimpanan(menunggu=sesi)
    antrean, _, _ = _antrean(penyimpanan, produk=_Produk(terhapus={PRODUK_TERHAPUS}))

    # Act
    hasil = antrean.take_next()

    # Assert
    assert hasil is None
    assert [gagal[0] for gagal in penyimpanan.gagal] == [satu.id for satu in sesi]


def test_sesi_yang_gagal_dicatat_di_log_dengan_alasannya(caplog):
    """Positif: sesi yang gagal sebelum mulai meninggalkan jejak alasan untuk Admin dan tim."""
    # Arrange
    penyimpanan = _Penyimpanan(menunggu=_menunggu(product_id=PRODUK_TERHAPUS))
    antrean, _, _ = _antrean(penyimpanan, produk=_Produk(terhapus={PRODUK_TERHAPUS}))

    # Act
    with caplog.at_level(logging.WARNING, logger="app.modules.runs.service"):
        antrean.take_next()

    # Assert
    assert str(SESI_LAMA) in caplog.text
    assert "produk" in caplog.text.lower()
