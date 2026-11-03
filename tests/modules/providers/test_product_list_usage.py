"""Kolom turunan di daftar produk Admin. PBI-11 SCRUM-210 (dipindah dari PBI-23 AC6).

Daftar Admin adalah GET /admin/providers. GET /ai-products hanya daftar pilihan
(id, nama, jenis) yang juga dibuka Kontributor, jadi angka anggaran sengaja tidak
dikirim ke sana.

Sisa anggaran bulan ini = batas bulanan produk dikurangi biaya call_logs sejak
tanggal 1 pukul 00.00 WIB, tidak pernah negatif. Produk Manual tidak punya
anggaran (PBI-12 AC10), jadi nilainya null.

Jumlah sesi = semua sesi pengukuran produk, termasuk yang diarsipkan, karena sesi
tidak pernah dihapus (AC10). Berlaku untuk produk Otomatis maupun Manual.

Setiap tes diberi label di docstring-nya:
- Positif: perilaku yang memang diharapkan terjadi.
- Negatif: permintaan yang harus ditolak.
- Edge: batas atau kasus pinggir (pergantian bulan WIB, anggaran terlampaui).
"""

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import event

from app.modules.providers import service
from app.modules.runs.models import (
    CallLog,
    MeasurementSession,
    SessionPurpose,
    SessionStatus,
    SessionType,
    SplitFilter,
)
from app.shared.security import Role
from tests.modules.providers.test_product_lists import _manual, _otomatis

WIB = ZoneInfo("Asia/Jakarta")
SEKARANG = datetime(2026, 11, 2, 10, 0, tzinfo=WIB)


def _simpan(db_session, *produk):
    db_session.add_all(produk)
    db_session.commit()
    return produk


def _biaya(db_session, product_id: uuid.UUID, cost_idr: str, started_at: datetime) -> None:
    """Satu baris call_logs sintetis dengan biaya tertentu."""
    db_session.add(
        CallLog(
            attempt_id=uuid.uuid4(),
            product_id=product_id,
            started_at=started_at,
            ended_at=started_at,
            duration_ms=1,
            input_tokens=0,
            output_tokens=1,
            cost_idr=Decimal(cost_idr),
            usd_idr_rate=Decimal("16000"),
            model_version="model-uji",
            result="ok",
            failure_kind=None,
            raw={},
        )
    )
    db_session.commit()


def _sesi(db_session, product_id: uuid.UUID, *, diarsipkan: bool = False) -> None:
    """Satu sesi pengukuran sintetis yang sudah selesai."""
    pembuat = uuid.uuid4()
    db_session.add(
        MeasurementSession(
            label="Sesi uji",
            purpose=SessionPurpose.TRIAL,
            session_type=SessionType.AUTOMATIC,
            product_id=product_id,
            repetitions=1,
            split_filter=SplitFilter.BOTH,
            status=SessionStatus.FINISHED,
            created_by=pembuat,
            archived_at=SEKARANG if diarsipkan else None,
            archived_by=pembuat if diarsipkan else None,
        )
    )
    db_session.commit()


def _per_nama(baris) -> dict:
    return {item.name: item for item in baris}


def test_daftar_admin_memuat_sisa_anggaran_bulan_ini(as_role, db_session):
    """Positif: sisa anggaran = batas bulanan dikurangi biaya bulan berjalan."""
    # Arrange
    (produk,) = _simpan(db_session, _otomatis("Otomatis A", monthly_budget_idr=Decimal("1500000")))
    sekarang = datetime.now(UTC)
    _biaya(db_session, produk.id, "250.00", sekarang)
    _biaya(db_session, produk.id, "100.00", sekarang)

    # Act
    baris = {b["name"]: b for b in as_role(Role.ADMIN).get("/admin/providers").json()}

    # Assert
    assert Decimal(baris["Otomatis A"]["remaining_budget_idr"]) == Decimal("1499650.00")


def test_produk_manual_tidak_punya_sisa_anggaran(as_role, db_session):
    """Positif: produk Manual tidak memakai anggaran token, jadi nilainya null."""
    # Arrange
    _simpan(db_session, _manual("Manual A"))

    # Act
    baris = {b["name"]: b for b in as_role(Role.ADMIN).get("/admin/providers").json()}

    # Assert
    assert baris["Manual A"]["remaining_budget_idr"] is None


def test_biaya_sebelum_tanggal_1_pukul_00_wib_tidak_dihitung(db_session):
    """Edge: 31 Okt 23.59.59 WIB milik bulan lalu, 1 Nov 00.00.00 WIB milik bulan ini.

    Stempelnya UTC seperti yang ditulis worker (datetime.now(UTC)): 16.59.59Z dan
    17.00.00Z, keduanya masih 31 Okt. Batas yang salah, entah tanggal 1 pukul 00.00
    UTC atau batas WIB yang dibandingkan sebagai jam dinding di SQLite, membuat
    kedua biaya jatuh di sisi yang sama dan tes ini gagal.
    """
    # Arrange
    (produk,) = _simpan(db_session, _otomatis("Otomatis A", monthly_budget_idr=Decimal("1500000")))
    _biaya(db_session, produk.id, "100.00", datetime(2026, 10, 31, 16, 59, 59, tzinfo=UTC))
    _biaya(db_session, produk.id, "250.00", datetime(2026, 10, 31, 17, 0, 0, tzinfo=UTC))

    # Act
    baris = _per_nama(
        service.list_products(db_session, is_active=None, measurement_type=None, now=SEKARANG)
    )

    # Assert
    assert baris["Otomatis A"].remaining_budget_idr == Decimal("1499750.00")


def test_biaya_produk_lain_tidak_mengurangi_anggaran(db_session):
    """Edge: biaya dihitung per produk."""
    # Arrange
    produk_a, produk_b = _simpan(
        db_session,
        _otomatis("Otomatis A", monthly_budget_idr=Decimal("1000")),
        _otomatis("Otomatis B", monthly_budget_idr=Decimal("1000")),
    )
    _biaya(db_session, produk_b.id, "400.00", SEKARANG)

    # Act
    baris = _per_nama(
        service.list_products(db_session, is_active=None, measurement_type=None, now=SEKARANG)
    )

    # Assert
    assert baris["Otomatis A"].remaining_budget_idr == Decimal("1000.00")
    assert baris["Otomatis B"].remaining_budget_idr == Decimal("600.00")


def test_sisa_anggaran_tidak_pernah_negatif(db_session):
    """Edge: panggilan terakhir bisa melewati batas sebelum sesi berhenti (PBI-12 AC10)."""
    # Arrange
    (produk,) = _simpan(db_session, _otomatis("Otomatis A", monthly_budget_idr=Decimal("1000")))
    _biaya(db_session, produk.id, "1500.00", SEKARANG)

    # Act
    baris = _per_nama(
        service.list_products(db_session, is_active=None, measurement_type=None, now=SEKARANG)
    )

    # Assert
    assert baris["Otomatis A"].remaining_budget_idr == Decimal("0.00")


def test_biaya_semua_produk_dibaca_dalam_satu_query(db_session):
    """Edge: jumlah query ke call_logs tidak bertambah per produk (tanpa N+1)."""
    # Arrange
    _simpan(db_session, *[_otomatis(f"Otomatis {i}") for i in range(3)], _manual("Manual A"))
    query_biaya: list[str] = []

    def catat(_conn, _cursor, statement, *_args):
        if "call_logs" in statement:
            query_biaya.append(statement)

    engine = db_session.get_bind()
    event.listen(engine, "before_cursor_execute", catat)
    try:
        # Act
        service.list_products(db_session, is_active=None, measurement_type=None, now=SEKARANG)
    finally:
        event.remove(engine, "before_cursor_execute", catat)

    # Assert
    assert len(query_biaya) == 1


def test_daftar_admin_memuat_jumlah_sesi_per_produk(as_role, db_session):
    """Positif: setiap baris membawa jumlah sesi pengukuran produknya."""
    # Arrange
    otomatis, manual = _simpan(db_session, _otomatis("Otomatis A"), _manual("Manual A"))
    _sesi(db_session, otomatis.id)
    _sesi(db_session, otomatis.id)
    _sesi(db_session, manual.id)

    # Act
    baris = {b["name"]: b for b in as_role(Role.ADMIN).get("/admin/providers").json()}

    # Assert
    assert baris["Otomatis A"]["session_count"] == 2
    assert baris["Manual A"]["session_count"] == 1


def test_produk_tanpa_sesi_berjumlah_nol(db_session):
    """Edge: produk yang belum pernah diukur bernilai 0, bukan null."""
    # Arrange
    _simpan(db_session, _otomatis("Otomatis A"), _manual("Manual A"))

    # Act
    baris = _per_nama(
        service.list_products(db_session, is_active=None, measurement_type=None, now=SEKARANG)
    )

    # Assert
    assert baris["Otomatis A"].session_count == 0
    assert baris["Manual A"].session_count == 0


def test_sesi_yang_diarsipkan_tetap_terhitung(db_session):
    """Edge: arsip hanya menyembunyikan sesi dari daftar sesi, bukan dari riwayat produk."""
    # Arrange
    (produk,) = _simpan(db_session, _otomatis("Otomatis A"))
    _sesi(db_session, produk.id)
    _sesi(db_session, produk.id, diarsipkan=True)

    # Act
    baris = _per_nama(
        service.list_products(db_session, is_active=None, measurement_type=None, now=SEKARANG)
    )

    # Assert
    assert baris["Otomatis A"].session_count == 2


def test_jumlah_sesi_semua_produk_dibaca_dalam_satu_query(db_session):
    """Edge: jumlah query ke measurement_sessions tidak bertambah per produk (tanpa N+1)."""
    # Arrange
    produk = _simpan(db_session, *[_otomatis(f"Otomatis {i}") for i in range(3)], _manual("M"))
    for item in produk:
        _sesi(db_session, item.id)
    query_sesi: list[str] = []

    def catat(_conn, _cursor, statement, *_args):
        if "measurement_sessions" in statement:
            query_sesi.append(statement)

    engine = db_session.get_bind()
    event.listen(engine, "before_cursor_execute", catat)
    try:
        # Act
        service.list_products(db_session, is_active=None, measurement_type=None, now=SEKARANG)
    finally:
        event.remove(engine, "before_cursor_execute", catat)

    # Assert
    assert len(query_sesi) == 1
