"""POST /measurements: tombol Mulai di halaman Buat Pengukuran. SCRUM-76 SCRUM-207.

PBI-11 AC1 (Admin dan Developer AI, label dan Resmi/Uji Coba wajib), AC3
(sesi Menunggu, yang lain mengantre) dan AC5 (validasi ulang anggaran) lewat
HTTP, dengan suite, kasus, dan produk sungguhan di database uji. Alurnya D3
langkah 18-23. Tidak ada endpoint duplikasi sesi (AC1).

Tabel sesi (SCRUM-205) belum ada, jadi penyimpanan sesi diganti antrean
memori yang baru per test. Kurs dan settings di-override seperti test
ringkasan SCRUM-206, supaya tidak ada test yang menyentuh jaringan.

Label: Positif = sesi dibuat, Negatif = sesi ditolak, Edge = batas perilaku.
"""

import uuid
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.main import app
from app.modules.runs import adapters, dependencies, exchange_rate
from app.shared.config import get_settings
from app.shared.security import Role
from tests.modules.cases.test_cases import ADMIN_LAIN, _buat, _ganti, _setujui, _suite
from tests.modules.runs.test_summary_endpoint import _settings, _tambah_produk

URL = "/measurements"


@pytest.fixture
def siap(as_role, buat_pengguna, db_session):
    """Suite berisi 2 kasus dev dan 1 kasus test yang approved, satu produk, antrean kosong."""
    client = as_role(Role.CONTRIBUTOR)
    suite_id = _suite(client)
    for kode, tag in (("PHK-001", "dev"), ("PHK-002", "dev"), ("PHK-003", "test")):
        case_id = _buat(client, suite_id, case_code=kode, split_tag=tag).json()["id"]
        _setujui(db_session, case_id)
    produk = _tambah_produk(db_session)
    antrean = adapters.InMemorySessionQueue()
    app.dependency_overrides[get_settings] = lambda: _settings()
    app.dependency_overrides[dependencies.get_exchange_rate] = lambda: (
        exchange_rate.ConfiguredExchangeRate(Decimal("16000"))
    )
    app.dependency_overrides[dependencies.get_session_store] = lambda: antrean

    def sebagai(role: Role):
        _ganti(client, buat_pengguna(role, user_id=ADMIN_LAIN, name="Pengguna QA"))
        return client

    return SimpleNamespace(
        client=client,
        sebagai=sebagai,
        suite_id=suite_id,
        produk=produk,
        antrean=antrean,
        db=db_session,
    )


def _badan(siap, **ubah) -> dict:
    badan = {
        "suite_ids": [siap.suite_id],
        "product_id": str(siap.produk.id),
        "repeat_count": 3,
        "case_filter": "both",
        "label": "Run Oktober AiYU",
        "purpose": "official",
    }
    badan.update(ubah)
    return badan


def test_admin_memulai_sesi_dan_sesi_menunggu_di_antrean(siap):
    """Positif: Admin menekan Mulai, jawabannya 201 dengan status queued (D3 langkah 23)."""
    # Arrange
    client = siap.sebagai(Role.ADMIN)

    # Act
    response = client.post(URL, json=_badan(siap))

    # Assert
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "queued"
    assert datetime.fromisoformat(body["queued_at"]).tzinfo is not None
    tersimpan = siap.antrean.get(uuid.UUID(body["session_id"]))
    assert tersimpan.new.label == "Run Oktober AiYU"
    assert tersimpan.new.created_by == ADMIN_LAIN


def test_developer_ai_boleh_memulai_sesi(siap):
    """Positif: Developer AI ikut membuat pengukuran (user story PBI-11, D10)."""
    # Arrange
    client = siap.sebagai(Role.DEVELOPER_AI)

    # Act
    response = client.post(URL, json=_badan(siap, purpose="trial"))

    # Assert
    assert response.status_code == 201
    tersimpan = siap.antrean.get(uuid.UUID(response.json()["session_id"]))
    assert tersimpan.new.purpose.value == "trial"


def test_sesi_kedua_mengantre_di_belakang_sesi_pertama(siap):
    """Positif: AC3, pengukuran bisa dimulai kapan saja dan yang berikutnya mengantre."""
    # Arrange
    client = siap.sebagai(Role.ADMIN)
    pertama = client.post(URL, json=_badan(siap, label="Pertama")).json()

    # Act
    kedua = client.post(URL, json=_badan(siap, label="Kedua")).json()

    # Assert
    assert pertama["queued_at"] <= kedua["queued_at"]
    assert kedua["status"] == "queued"
    assert str(siap.antrean.oldest_queued().id) == pertama["session_id"]


@pytest.mark.parametrize("role", [Role.CONTRIBUTOR, Role.VIEWER])
def test_peran_selain_admin_dan_developer_ai_ditolak_403(siap, role):
    """Negatif: hanya Admin dan Developer AI yang boleh memulai pengukuran."""
    # Arrange
    client = siap.sebagai(role)

    # Act
    response = client.post(URL, json=_badan(siap))

    # Assert
    assert response.status_code == 403
    assert response.json()["code"] == "FORBIDDEN"
    assert siap.antrean.get_all() == []


def test_tanpa_login_ditolak_401(client):
    """Negatif: tanpa sesi login tidak ada yang bisa memulai pengukuran."""
    # Arrange: tidak ada sesi

    # Act
    response = client.post(URL, json={})

    # Assert
    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHENTICATED"


@pytest.mark.parametrize(
    "ubah",
    [
        {"label": ""},
        {"label": None},
        {"purpose": "resmi"},
        {"purpose": None},
        {"repeat_count": 0},
    ],
)
def test_label_atau_tujuan_tidak_valid_ditolak_422(siap, ubah):
    """Negatif: AC1, label dan Resmi/Uji Coba wajib, sebelum data apa pun disentuh."""
    # Arrange
    client = siap.sebagai(Role.ADMIN)

    # Act
    response = client.post(URL, json=_badan(siap, **ubah))

    # Assert
    assert response.status_code == 422
    assert siap.antrean.get_all() == []


def test_anggaran_tidak_cukup_saat_mulai_ditolak_dan_tidak_mengantre(siap):
    """Negatif: AC5, anggaran turun setelah ringkasan dibuka, Mulai ditolak dengan pesan yang sama."""
    # Arrange: perkiraan Rp864, anggaran tinggal Rp100
    siap.produk.monthly_budget_idr = Decimal("100.00")
    siap.db.commit()
    client = siap.sebagai(Role.ADMIN)

    # Act
    response = client.post(URL, json=_badan(siap))

    # Assert
    assert response.status_code == 422
    assert response.json()["code"] == "BUDGET_EXCEEDED"
    assert response.json()["product_name"] == "AiYU"
    assert siap.antrean.get_all() == []


def test_produk_dinonaktifkan_sebelum_mulai_ditolak_422(siap):
    """Negatif: AC1, produk harus masih aktif saat Mulai ditekan."""
    # Arrange
    siap.produk.is_active = False
    siap.db.commit()
    client = siap.sebagai(Role.ADMIN)

    # Act
    response = client.post(URL, json=_badan(siap))

    # Assert
    assert response.status_code == 422
    assert response.json()["code"] == "PRODUCT_INACTIVE"
    assert siap.antrean.get_all() == []


def test_produk_yang_tidak_ada_dijawab_404(siap):
    """Negatif: produk dihapus di antara ringkasan dan Mulai."""
    # Arrange
    client = siap.sebagai(Role.ADMIN)

    # Act
    response = client.post(URL, json=_badan(siap, product_id=str(uuid.uuid4())))

    # Assert
    assert response.status_code == 404
    assert response.json()["code"] == "AI_PRODUCT_NOT_FOUND"


def test_filter_tanpa_kasus_layak_ditolak_422(siap):
    """Edge: suite punya kasus, tetapi tidak ada yang cocok dengan filter yang dipilih."""
    # Arrange: suite kedua hanya berisi kasus dev
    client = siap.sebagai(Role.CONTRIBUTOR)
    dev_saja = _suite(client, "Suite dev saja")
    case_id = _buat(client, dev_saja, case_code="DEV-001", split_tag="dev").json()["id"]
    _setujui(siap.db, case_id)
    client = siap.sebagai(Role.ADMIN)

    # Act
    response = client.post(URL, json=_badan(siap, suite_ids=[dev_saja], case_filter="test"))

    # Assert
    assert response.status_code == 422
    assert response.json()["code"] == "NO_ELIGIBLE_CASES"


def test_kontrak_openapi_memuat_mulai_sesi_tanpa_duplikasi(client):
    """Positif: rute Mulai ada di tag measurements, dan tidak ada rute duplikasi sesi (AC1)."""
    # Arrange + Act
    spec = client.get("/openapi.json").json()

    # Assert
    post = spec["paths"][URL]["post"]
    assert post["tags"] == ["measurements"]
    assert "201" in post["responses"]
    assert "StartRequest" in spec["components"]["schemas"]
    assert not any("duplicate" in path or "duplikat" in path for path in spec["paths"])


def test_antrean_dibuat_sekali_lalu_dipakai_bersama_antar_permintaan():
    """Edge: antrean memori harus satu untuk seluruh proses, kalau tidak urutan antrean reset tiap request."""
    # Arrange
    dependencies.get_session_store.cache_clear()

    # Act
    pertama = dependencies.get_session_store()
    kedua = dependencies.get_session_store()

    # Assert
    assert pertama is kedua
    assert isinstance(pertama, adapters.InMemorySessionQueue)
    dependencies.get_session_store.cache_clear()
