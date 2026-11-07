"""POST /manual-measurements: Admin membuat sesi manual. PBI-22 (SCRUM-176) AC1, AC2, AC10.

D8 "Admin membuat sesi": satu produk manual, suite, filter dev/test/both, percobaan 1-5
(standar 1), snapshot dibuat saat itu juga, dan semua kasus muncul tanpa jawaban. Lewat
HTTP dengan suite, kasus, dan produk sungguhan di database uji. Tanpa antrean, biaya,
maupun anggaran (AC10): sesi tidak punya queued_at dan langsung Belum terisi.

Hanya Admin yang membuat sesi manual (AC1); melihat dan mengisinya milik SCRUM-231/235.

Label: Positif = sesi dibuat, Negatif = sesi ditolak, Edge = batas perilaku.
"""

import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError

from app.modules.providers.models import AiProduct, MeasurementType
from app.modules.runs import repository, schemas, session_ports
from app.modules.runs.models import (
    Attempt,
    AttemptSource,
    AttemptStatus,
    CaseSnapshot,
    CaseSnapshotItem,
    MeasurementSession,
    MeasurementSessionSuite,
    SessionProductCopy,
    SessionStatus,
    SessionType,
)
from app.shared.security import Role
from tests.modules.cases.test_cases import ADMIN_LAIN, _buat, _ganti, _setujui, _suite
from tests.modules.runs.test_summary_endpoint import _tambah_produk

URL = "/manual-measurements"


def _produk_manual(db, *, aktif: bool = True) -> AiProduct:
    """Produk manual seperti LegalHero di catatan klien: penyedia dan situs, tanpa API."""
    produk = AiProduct(
        name=f"LegalHero {'aktif' if aktif else 'nonaktif'}",
        measurement_type=MeasurementType.MANUAL,
        vendor_name="LegalHero",
        site_url="https://legalhero.id",
        is_active=aktif,
        created_by=ADMIN_LAIN,
    )
    db.add(produk)
    db.commit()
    return produk


@pytest.fixture
def siap(as_role, buat_pengguna, db_session):
    """Suite berisi 2 kasus dev dan 1 kasus test yang disetujui, plus satu produk manual."""
    client = as_role(Role.CONTRIBUTOR)
    suite_id = _suite(client)
    for kode, tag in (("PHK-001", "dev"), ("PHK-002", "dev"), ("PHK-003", "test")):
        case_id = _buat(client, suite_id, case_code=kode, split_tag=tag).json()["id"]
        _setujui(db_session, case_id)

    def sebagai(role: Role):
        _ganti(client, buat_pengguna(role, user_id=ADMIN_LAIN, name="Pengguna QA"))
        return client

    sebagai(Role.ADMIN)
    return SimpleNamespace(
        client=client,
        sebagai=sebagai,
        suite_id=suite_id,
        produk=_produk_manual(db_session),
        db=db_session,
    )


def _badan(siap, **ubah) -> dict:
    badan = {
        "suite_ids": [siap.suite_id],
        "product_id": str(siap.produk.id),
        "repeat_count": 2,
        "case_filter": "both",
        "label": "LegalHero November",
        "purpose": "official",
    }
    badan.update(ubah)
    return badan


def _jumlah(db, model) -> int:
    return db.scalar(select(func.count()).select_from(model))


def test_admin_membuat_sesi_manual_yang_langsung_belum_terisi(siap):
    """Positif: AC1, 201 dengan status not_filled dan N = 3 kasus x 2 percobaan."""
    # Act
    respons = siap.sebagai(Role.ADMIN).post(URL, json=_badan(siap))

    # Assert
    assert respons.status_code == 201, respons.text
    isi = respons.json()
    assert isi["status"] == "not_filled"
    assert (isi["case_count"], isi["repetitions"], isi["total_answers"]) == (3, 2, 6)
    sesi = siap.db.get(MeasurementSession, uuid.UUID(isi["session_id"]))
    assert sesi.session_type == SessionType.MANUAL
    assert sesi.status == SessionStatus.NOT_FILLED
    # AC10: tanpa antrean, jadi tidak pernah punya waktu antre atau mulai.
    assert (sesi.queued_at, sesi.started_at) == (None, None)
    assert siap.db.scalars(select(MeasurementSessionSuite.suite_id)).all() == [
        uuid.UUID(siap.suite_id)
    ]


def test_snapshot_salinan_produk_dan_percobaan_kosong_dibuat_saat_itu_juga(siap):
    """Positif: AC2, kasus dibekukan saat sesi dibuat; setiap kasus x percobaan belum terisi."""
    # Act
    siap.sebagai(Role.ADMIN).post(URL, json=_badan(siap, case_filter="dev"))

    # Assert
    assert _jumlah(siap.db, CaseSnapshot) == 1
    assert _jumlah(siap.db, CaseSnapshotItem) == 2
    salinan = siap.db.scalars(select(SessionProductCopy)).one()
    assert (salinan.measurement_type, salinan.vendor_name, salinan.site_url) == (
        "manual",
        "LegalHero",
        "https://legalhero.id",
    )
    percobaan = siap.db.scalars(select(Attempt)).all()
    assert sorted(p.repetition_no for p in percobaan) == [1, 1, 2, 2]
    assert {(p.source, p.status, p.filled_at) for p in percobaan} == {
        (AttemptSource.MANUAL, AttemptStatus.PENDING, None)
    }


def test_tanpa_jumlah_percobaan_memakai_satu(siap):
    """Positif: AC1, standar 1 percobaan per kasus (beban awal 2 platform x 100 kasus x 1)."""
    # Arrange
    badan = _badan(siap)
    del badan["repeat_count"]

    # Act
    respons = siap.sebagai(Role.ADMIN).post(URL, json=badan)

    # Assert
    assert respons.json()["total_answers"] == 3
    assert _jumlah(siap.db, Attempt) == 3


@pytest.mark.parametrize("peran", [Role.CONTRIBUTOR, Role.VIEWER, Role.DEVELOPER_AI])
def test_selain_admin_tidak_bisa_membuat_sesi_manual(siap, peran):
    """Negatif: AC1, hanya Admin yang membuat sesi manual."""
    # Act
    respons = siap.sebagai(peran).post(URL, json=_badan(siap))

    # Assert
    assert respons.status_code == 403
    assert _jumlah(siap.db, MeasurementSession) == 0


def test_produk_otomatis_ditolak(siap):
    """Negatif: AC1, produk otomatis diukur lewat Mulai pengukuran, bukan dicatat manual."""
    # Arrange
    otomatis = _tambah_produk(siap.db)

    # Act
    respons = siap.sebagai(Role.ADMIN).post(URL, json=_badan(siap, product_id=str(otomatis.id)))

    # Assert
    assert respons.status_code == 422
    assert respons.json()["code"] == "PRODUCT_NOT_MANUAL"
    assert _jumlah(siap.db, MeasurementSession) == 0


def test_produk_manual_nonaktif_ditolak(siap):
    """Negatif: produk yang dinonaktifkan Admin tidak bisa dipakai untuk sesi baru."""
    # Arrange
    nonaktif = _produk_manual(siap.db, aktif=False)

    # Act
    respons = siap.sebagai(Role.ADMIN).post(URL, json=_badan(siap, product_id=str(nonaktif.id)))

    # Assert
    assert respons.status_code == 422
    assert respons.json()["code"] == "PRODUCT_INACTIVE"
    assert _jumlah(siap.db, MeasurementSession) == 0


def test_suite_tanpa_kasus_layak_ditolak_tanpa_sisa(siap):
    """Negatif: AC1, suite baru tanpa kasus disetujui tidak meninggalkan sesi setengah jadi."""
    # Arrange
    suite_kosong = _suite(siap.sebagai(Role.CONTRIBUTOR), name="Suite kosong")

    # Act
    respons = siap.sebagai(Role.ADMIN).post(URL, json=_badan(siap, suite_ids=[suite_kosong]))

    # Assert
    assert respons.status_code == 422
    assert respons.json()["code"] == "NO_ELIGIBLE_CASES"
    assert _jumlah(siap.db, MeasurementSession) == 0
    assert _jumlah(siap.db, CaseSnapshot) == 0


@pytest.mark.parametrize("jumlah", [0, 6])
def test_jumlah_percobaan_di_luar_batas_ditolak(siap, jumlah):
    """Negatif: AC1, percobaan 1 sampai 5."""
    # Act
    respons = siap.sebagai(Role.ADMIN).post(URL, json=_badan(siap, repeat_count=jumlah))

    # Assert
    assert respons.status_code == 422
    assert _jumlah(siap.db, MeasurementSession) == 0


def test_galat_di_tengah_pembuatan_tidak_meninggalkan_sesi(db_session, monkeypatch):
    """Negatif: AC2, snapshot gagal ditulis maka sesi dan suitenya ikut dibatalkan."""

    # Arrange
    def putus(*_args, **_kwargs):
        raise OperationalError("INSERT INTO case_snapshots", {}, Exception("koneksi putus"))

    monkeypatch.setattr(repository, "_freeze", putus)
    sesi = session_ports.NewSession(
        label="LegalHero November",
        purpose=schemas.MeasurementPurpose.TRIAL,
        product_id=uuid.uuid4(),
        suite_ids=(uuid.uuid4(),),
        case_filter=schemas.CaseFilter.BOTH,
        repetitions=1,
        created_by=uuid.uuid4(),
    )

    # Act
    with pytest.raises(OperationalError):
        repository.ManualSessionRepository(db_session).create_manual(sesi, items=[], product=None)

    # Assert
    assert _jumlah(db_session, MeasurementSession) == 0
    assert _jumlah(db_session, MeasurementSessionSuite) == 0
