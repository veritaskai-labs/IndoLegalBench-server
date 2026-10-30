"""POST /measurements/summary. SCRUM-76 SCRUM-206.

PBI-11 AC2, AC3 and AC5 through HTTP, with real suites, cases and a real
product in the test database. Only the exchange rate and the settings are
overridden, so no test reaches the network.

Reference numbers: 3 approved cases (2 dev, 1 test) times 3 repeats is 9
calls. At 500 answer tokens, 10 USD per million output tokens, a rate of
16.000 rupiah and a 20 percent margin that is 864,00 rupiah.
"""

import uuid
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.main import app
from app.modules.providers.models import AiProduct, ProviderType
from app.modules.runs import dependencies, exchange_rate
from app.shared.config import Settings, get_settings
from app.shared.security import Role
from tests.modules.cases.test_cases import ADMIN_LAIN, _buat, _ganti, _setujui, _suite
from tests.modules.conftest import USER_ID_QA

URL = "/measurements/summary"
HARGA = {"openai_compatible:deepseek-flash": Decimal("10")}


def _settings(**ubah) -> Settings:
    nilai = {
        "cost_model_prices": HARGA,
        "cost_default_avg_answer_tokens": Decimal("500"),
    }
    nilai.update(ubah)
    return Settings(_env_file=None, **nilai)


def _tambah_produk(db, **ubah) -> AiProduct:
    nilai = {
        "name": "AiYU",
        "provider_type": ProviderType.OPENAI_COMPATIBLE,
        "base_url": "https://api.example.test/v1/chat/completions",
        "model_name": "deepseek-flash",
        "credential_encrypted": b"terenkripsi",
        "credential_hint": "9xQ2",
        "rate_limit_per_minute": 30,
        "monthly_budget_idr": Decimal("500000.00"),
        "created_by": USER_ID_QA,
    }
    nilai.update(ubah)
    produk = AiProduct(**nilai)
    db.add(produk)
    db.commit()
    return produk


@pytest.fixture
def siap(as_role, buat_pengguna, db_session):
    """Suite berisi 2 kasus dev dan 1 kasus test yang approved, plus satu produk AI."""
    client = as_role(Role.CONTRIBUTOR)
    suite_id = _suite(client)
    for kode, tag in (("PHK-001", "dev"), ("PHK-002", "dev"), ("PHK-003", "test")):
        case_id = _buat(client, suite_id, case_code=kode, split_tag=tag).json()["id"]
        _setujui(db_session, case_id)
    produk = _tambah_produk(db_session)
    app.dependency_overrides[get_settings] = _settings
    app.dependency_overrides[dependencies.get_exchange_rate] = lambda: (
        exchange_rate.ConfiguredExchangeRate(Decimal("16000"))
    )

    def sebagai(role: Role):
        _ganti(client, buat_pengguna(role, user_id=ADMIN_LAIN, name="Pengguna QA"))
        return client

    return SimpleNamespace(
        client=client,
        sebagai=sebagai,
        suite_id=suite_id,
        produk=produk,
        db=db_session,
    )


def _badan(siap, **ubah) -> dict:
    badan = {
        "suite_ids": [siap.suite_id],
        "product_id": str(siap.produk.id),
        "repeat_count": 3,
        "case_filter": "both",
    }
    badan.update(ubah)
    return badan


def test_admin_mendapat_ringkasan_lengkap_dan_hanya_kolom_ringkasan(siap):
    # Arrange
    client = siap.sebagai(Role.ADMIN)

    # Act
    response = client.post(URL, json=_badan(siap))

    # Assert: tepat enam kolom, tanpa kredensial, alamat produk, atau isi kasus
    assert response.status_code == 200
    assert response.json() == {
        "case_count": 3,
        "total_calls": 9,
        "case_filter": "both",
        "repeat_count": 3,
        "estimated_cost_idr": "864.00",
        "remaining_budget_idr": "500000.00",
    }


def test_developer_ai_boleh_meminta_ringkasan(siap):
    # Arrange
    client = siap.sebagai(Role.DEVELOPER_AI)

    # Act
    response = client.post(URL, json=_badan(siap))

    # Assert
    assert response.status_code == 200
    assert response.json()["case_count"] == 3


def test_pengulangan_dan_filter_memakai_nilai_bawaan_saat_tidak_dikirim(siap):
    # Arrange
    client = siap.sebagai(Role.ADMIN)
    badan = {"suite_ids": [siap.suite_id], "product_id": str(siap.produk.id)}

    # Act
    response = client.post(URL, json=badan)

    # Assert
    assert response.status_code == 200
    assert response.json()["repeat_count"] == 3
    assert response.json()["case_filter"] == "both"


def test_filter_dev_hanya_menghitung_kasus_dev(siap):
    # Arrange
    client = siap.sebagai(Role.ADMIN)

    # Act
    response = client.post(URL, json=_badan(siap, case_filter="dev", repeat_count=5))

    # Assert: 2 kasus x 5 = 10 panggilan
    assert response.status_code == 200
    assert response.json()["case_count"] == 2
    assert response.json()["total_calls"] == 10
    assert response.json()["case_filter"] == "dev"


@pytest.mark.parametrize("role", [Role.CONTRIBUTOR, Role.VIEWER])
def test_peran_selain_admin_dan_developer_ai_ditolak_403(siap, role):
    # Arrange
    client = siap.sebagai(role)

    # Act
    response = client.post(URL, json=_badan(siap))

    # Assert
    assert response.status_code == 403
    assert response.json()["code"] == "FORBIDDEN"


def test_tanpa_login_ditolak_401(client):
    # Arrange: tidak ada sesi

    # Act
    response = client.post(URL, json={"suite_ids": [], "product_id": str(uuid.uuid4())})

    # Assert
    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHENTICATED"


@pytest.mark.parametrize(
    "ubah",
    [
        {"repeat_count": 0},
        {"repeat_count": 6},
        {"repeat_count": -1},
        {"case_filter": "semua"},
        {"suite_ids": []},
        {"product_id": "bukan-uuid"},
    ],
)
def test_isian_tidak_valid_ditolak_422_sebelum_menyentuh_data(siap, ubah):
    # Arrange
    client = siap.sebagai(Role.ADMIN)

    # Act
    response = client.post(URL, json=_badan(siap, **ubah))

    # Assert
    assert response.status_code == 422


def test_suite_yang_tidak_ada_dijawab_404(siap):
    # Arrange
    client = siap.sebagai(Role.ADMIN)

    # Act
    response = client.post(URL, json=_badan(siap, suite_ids=[str(uuid.uuid4())]))

    # Assert
    assert response.status_code == 404
    assert response.json()["message"] == "Suite tidak ditemukan"


def test_produk_yang_tidak_ada_dijawab_404(siap):
    # Arrange
    client = siap.sebagai(Role.ADMIN)

    # Act
    response = client.post(URL, json=_badan(siap, product_id=str(uuid.uuid4())))

    # Assert
    assert response.status_code == 404
    assert response.json()["code"] == "AI_PRODUCT_NOT_FOUND"


def test_produk_nonaktif_ditolak_422(siap):
    # Arrange
    siap.produk.is_active = False
    siap.db.commit()
    client = siap.sebagai(Role.ADMIN)

    # Act
    response = client.post(URL, json=_badan(siap))

    # Assert
    assert response.status_code == 422
    assert response.json()["code"] == "PRODUCT_INACTIVE"


def test_suite_hanya_berisi_draf_ditolak_422_tanpa_kasus_layak(siap):
    # Arrange: suite kedua hanya punya satu kasus yang belum disetujui
    client = siap.sebagai(Role.CONTRIBUTOR)
    kosong = _suite(client, "Suite draf")
    assert _buat(client, kosong, case_code="DRF-001").status_code == 201
    client = siap.sebagai(Role.ADMIN)

    # Act
    response = client.post(URL, json=_badan(siap, suite_ids=[kosong]))

    # Assert
    assert response.status_code == 422
    assert response.json()["code"] == "NO_ELIGIBLE_CASES"


def test_biaya_melebihi_sisa_anggaran_ditolak_422_dengan_nama_produk_dan_sisa(siap):
    # Arrange: anggaran bulanan hanya Rp100, perkiraan Rp864
    siap.produk.monthly_budget_idr = Decimal("100.00")
    siap.db.commit()
    client = siap.sebagai(Role.ADMIN)

    # Act
    response = client.post(URL, json=_badan(siap))

    # Assert
    body = response.json()
    assert response.status_code == 422
    assert body["code"] == "BUDGET_EXCEEDED"
    assert body["product_name"] == "AiYU"
    assert body["remaining_budget_idr"] == "100.00"
    assert body["estimated_cost_idr"] == "864.00"


def test_harga_model_belum_dikonfigurasi_dijawab_500_bukan_422(siap):
    # Arrange
    app.dependency_overrides[get_settings] = lambda: _settings(cost_model_prices={})
    client = siap.sebagai(Role.ADMIN)

    # Act
    response = client.post(URL, json=_badan(siap))

    # Assert
    assert response.status_code == 500
    assert response.json()["code"] == "PRICE_NOT_CONFIGURED"


def test_kurs_belum_tersedia_dijawab_500(siap):
    # Arrange
    app.dependency_overrides[dependencies.get_exchange_rate] = lambda: (
        exchange_rate.ConfiguredExchangeRate(None)
    )
    client = siap.sebagai(Role.ADMIN)

    # Act
    response = client.post(URL, json=_badan(siap))

    # Assert
    assert response.status_code == 500
    assert response.json()["code"] == "EXCHANGE_RATE_NOT_CONFIGURED"


def test_rata_rata_token_bawaan_kosong_dijawab_500_karena_riwayat_belum_ada(siap):
    # Arrange
    app.dependency_overrides[get_settings] = lambda: _settings(cost_default_avg_answer_tokens=None)
    client = siap.sebagai(Role.ADMIN)

    # Act
    response = client.post(URL, json=_badan(siap))

    # Assert
    assert response.status_code == 500
    assert response.json()["code"] == "AVG_ANSWER_TOKENS_NOT_CONFIGURED"


def test_kontrak_openapi_memuat_rute_ringkasan_di_measurements(client):
    # Arrange + Act
    spec = client.get("/openapi.json").json()

    # Assert
    post = spec["paths"][URL]["post"]
    assert post["tags"] == ["measurements"]
    assert "SummaryRequest" in spec["components"]["schemas"]
    assert "SummaryResponse" in spec["components"]["schemas"]
    assert not any(path.startswith("/runs") for path in spec["paths"])


def test_kurs_dibuat_sekali_lalu_dipakai_bersama_antar_permintaan():
    # Arrange
    dependencies.get_exchange_rate.cache_clear()

    # Act
    pertama = dependencies.get_exchange_rate()
    kedua = dependencies.get_exchange_rate()

    # Assert: cache kurs harian hidup di satu objek, bukan dibuat ulang tiap request
    assert pertama is kedua
    assert isinstance(pertama, exchange_rate.JisdorExchangeRate)
    dependencies.get_exchange_rate.cache_clear()
