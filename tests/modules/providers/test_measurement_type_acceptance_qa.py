"""QA PBI-23 jenis pengukuran lewat API. SCRUM-204 (SCRUM-177).

Hanya skenario SCRUM-204 yang belum punya tes di SCRUM-200 (#79) dan SCRUM-201 (#81):
- variasi parameter dan versi platform sebagai produk terpisah (AC2, AC3);
- ganti jenis setelah uji koneksi: kredensial dan hasil uji ikut terhapus di database,
  dan kembali ke Otomatis berarti uji koneksi harus diulang (AC4);
- nama produk nonaktif tidak bisa dipakai ulang (AC4);
- aturan nonaktif dan hapus PBI-10 berlaku juga untuk produk Manual (AC5, AC7);
- hanya Admin yang menulis produk, untuk semua peran setelah PBI-24 (AC8).

Uji koneksi tidak keluar ke jaringan: run_connection_test di service diganti hasil palsu.
Label di docstring: Positif, Negatif, Edge.
"""

import uuid
from decimal import Decimal

import pytest
from cryptography.fernet import Fernet

from app.modules.providers import service
from app.modules.providers.adapters.base import ConnectionTestResult
from app.modules.providers.models import AiProduct, MeasurementType, ProviderType
from app.shared.config import get_settings
from app.shared.security import Role

_SECRET = "provider-token-uji-204"  # pragma: allowlist secret
_SECRET_BARU = "provider-token-baru-204"  # pragma: allowlist secret
_PRODUK = "/admin/providers"
_PILIHAN = "/ai-products"


@pytest.fixture(autouse=True)
def encryption_key(monkeypatch):
    """Kunci Fernet sementara; kredensial produk Otomatis selalu dienkripsi saat simpan."""
    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", Fernet.generate_key().decode())
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def uji_koneksi_berhasil(monkeypatch):
    """Uji koneksi selalu berhasil tanpa memanggil penyedia sungguhan."""
    monkeypatch.setattr(
        service,
        "run_connection_test",
        lambda **_: ConnectionTestResult(status="ok", latency_ms=12),
    )


@pytest.fixture
def admin(as_role):
    return as_role(Role.ADMIN)


def _otomatis(nama: str, **ubah) -> dict:
    # Nilai sintetis, bukan produk yang didaftarkan Veritask.
    body = {
        "name": nama,
        "measurement_type": "automatic",
        "provider_type": "openai_compatible",
        "base_url": "https://api.example.test/v1",
        "model_name": "model-uji",
        "credential": _SECRET,
        "rate_limit_per_minute": 30,
        "monthly_budget_idr": "1500000.00",
    }
    body.update(ubah)
    return body


def _manual(nama: str, **ubah) -> dict:
    body = {
        "name": nama,
        "measurement_type": "manual",
        "vendor_name": "Penyedia Uji",
        "site_url": "https://contoh.test",
    }
    body.update(ubah)
    return body


def _daftar(client, body: dict) -> str:
    respons = client.post(_PRODUK, json=body)
    assert respons.status_code == 201, respons.text
    return respons.json()["id"]


def _nama_pilihan(client, purpose: str) -> list[str]:
    respons = client.get(_PILIHAN, params={"purpose": purpose})
    assert respons.status_code == 200, respons.text
    return [baris["name"] for baris in respons.json()]


def _baris(db_session, product_id: str) -> AiProduct:
    db_session.expire_all()
    return db_session.get(AiProduct, uuid.UUID(product_id))


# --- AC2, AC3: variasi dan versi adalah produk terpisah -----------------------------------


def test_variasi_parameter_didaftarkan_sebagai_dua_produk_otomatis(admin):
    """Positif: deepseek1 dan deepseek2 dengan temperature berbeda menjadi dua produk."""
    # Act
    satu = _daftar(admin, _otomatis("deepseek1", model_params={"temperature": 0.3}))
    dua = _daftar(admin, _otomatis("deepseek2", model_params={"temperature": 0.7}))

    # Assert
    daftar = admin.get(_PRODUK, params={"measurement_type": "automatic"}).json()
    temperature = {baris["name"]: baris["model_params"]["temperature"] for baris in daftar}
    assert satu != dua
    assert temperature == {"deepseek1": 0.3, "deepseek2": 0.7}


def test_versi_platform_didaftarkan_sebagai_dua_produk_manual(admin):
    """Positif: "HO Pro" dan "HO Premium" adalah dua produk Manual, bukan satu produk berversi."""
    # Act
    _daftar(admin, _manual("HO Pro", manual_params="Paket Pro"))
    _daftar(admin, _manual("HO Premium", manual_params="Paket Premium"))

    # Assert
    daftar = admin.get(_PRODUK, params={"measurement_type": "manual"}).json()
    assert [(b["name"], b["manual_params"]) for b in daftar] == [
        ("HO Premium", "Paket Premium"),
        ("HO Pro", "Paket Pro"),
    ]


# --- AC4: ganti jenis pada produk tanpa riwayat -------------------------------------------


def test_produk_yang_baru_diuji_koneksi_tetap_bisa_pindah_ke_manual(
    admin, db_session, uji_koneksi_berhasil
):
    """Positif: uji koneksi bukan riwayat. Saat pindah ke Manual, kredensial terenkripsi,
    hint, dan hasil uji koneksi terhapus di database, bukan hanya disembunyikan di respons."""
    # Arrange
    product_id = _daftar(admin, _otomatis("Pindah Manual"))
    uji = admin.post(f"{_PRODUK}/{product_id}/test-connection")
    assert uji.json()["status"] == "ok"
    assert _baris(db_session, product_id).last_test_status is not None

    # Act
    respons = admin.patch(
        f"{_PRODUK}/{product_id}",
        json={
            "measurement_type": "manual",
            "vendor_name": "Penyedia",
            "site_url": "https://x.test",
        },
    )

    # Assert
    assert respons.status_code == 200, respons.text
    assert respons.json()["has_credential"] is False
    assert _SECRET not in respons.text
    baris = _baris(db_session, product_id)
    assert baris.measurement_type == MeasurementType.MANUAL
    tersisa = {
        kolom: getattr(baris, kolom)
        for kolom in (
            "credential_encrypted",
            "credential_hint",
            "provider_type",
            "base_url",
            "model_name",
            "rate_limit_per_minute",
            "monthly_budget_idr",
            "last_test_at",
            "last_test_status",
            "last_test_message",
            "last_test_error_category",
        )
        if getattr(baris, kolom) is not None
    }
    assert tersisa == {}


def test_kembali_ke_otomatis_wajib_uji_koneksi_ulang(admin, db_session, uji_koneksi_berhasil):
    """Edge: Otomatis → Manual → Otomatis. Hasil uji koneksi lama tidak ikut kembali, jadi
    produk tampil belum diuji sampai Admin menguji kredensial barunya."""
    # Arrange
    product_id = _daftar(admin, _otomatis("Bolak Balik"))
    admin.post(f"{_PRODUK}/{product_id}/test-connection")
    admin.patch(
        f"{_PRODUK}/{product_id}",
        json={
            "measurement_type": "manual",
            "vendor_name": "Penyedia",
            "site_url": "https://x.test",
        },
    )
    kembali = _otomatis("Bolak Balik", credential=_SECRET_BARU)
    del kembali["name"]

    # Act
    respons = admin.patch(f"{_PRODUK}/{product_id}", json=kembali)

    # Assert
    assert respons.status_code == 200, respons.text
    hasil = respons.json()
    assert (hasil["measurement_type"], hasil["last_test_status"], hasil["last_test_at"]) == (
        "automatic",
        None,
        None,
    )
    assert hasil["credential_hint"] == _SECRET_BARU[-4:]
    baris = _baris(db_session, product_id)
    assert (baris.vendor_name, baris.site_url) == (None, None)

    # Act 2: uji koneksi ulang mengisi hasilnya lagi.
    admin.post(f"{_PRODUK}/{product_id}/test-connection")

    # Assert 2
    assert admin.get(f"{_PRODUK}/{product_id}").json()["last_test_status"] == "ok"


@pytest.mark.parametrize(
    ("body", "terkunci"),
    [
        pytest.param({"name": "Nama Baru"}, False, id="hanya-nama"),
        pytest.param({"rate_limit_per_minute": 45}, False, id="hanya-batas"),
        pytest.param({"measurement_type": "automatic"}, True, id="jenis-sama"),
        pytest.param(
            {"measurement_type": "manual", "vendor_name": "Penyedia", "site_url": "https://x.test"},
            True,
            id="ganti-jenis",
        ),
    ],
)
def test_baris_produk_dikunci_hanya_bila_jenis_ikut_dikirim(admin, monkeypatch, body, terkunci):
    """Edge (review #90): FOR UPDATE hanya dibutuhkan bila jenis bisa berubah. PATCH tanpa
    measurement_type (nama, batas) tidak perlu menahan baris produk, jadi tidak mengunci."""
    # Arrange
    product_id = _daftar(admin, _otomatis("Kunci Seperlunya"))
    asli = service.repository.get_by_id
    panggilan: list[bool] = []

    def catat(db, pid, *, for_update=False):
        panggilan.append(for_update)
        return asli(db, pid, for_update=for_update)

    monkeypatch.setattr(service.repository, "get_by_id", catat)

    # Act
    respons = admin.patch(f"{_PRODUK}/{product_id}", json=body)

    # Assert
    assert respons.status_code == 200, respons.text
    assert panggilan == [terkunci]


# --- AC4: nama unik selamanya --------------------------------------------------------------


def test_nama_produk_nonaktif_tidak_bisa_dipakai_produk_baru(admin):
    """Negatif: nama milik produk nonaktif tetap terpakai, juga bila jenis produk barunya beda."""
    # Arrange
    product_id = _daftar(admin, _otomatis("Nama Nonaktif"))
    assert admin.post(f"{_PRODUK}/{product_id}/deactivate").status_code == 200

    # Act
    respons = admin.post(_PRODUK, json=_manual("Nama Nonaktif"))

    # Assert
    assert respons.status_code == 409, respons.text
    assert respons.json()["code"] == "AI_PRODUCT_NAME_TAKEN"


def test_ganti_nama_ke_nama_produk_terhapus_ditolak(admin):
    """Edge: aturan unik selamanya juga berlaku saat mengganti nama lewat PATCH."""
    # Arrange
    lama = _daftar(admin, _manual("Nama Terhapus"))
    assert admin.delete(f"{_PRODUK}/{lama}").status_code == 204
    lain = _daftar(admin, _manual("Nama Lain"))

    # Act
    respons = admin.patch(f"{_PRODUK}/{lain}", json={"name": "Nama Terhapus"})

    # Assert
    assert respons.status_code == 409, respons.text
    assert respons.json()["code"] == "AI_PRODUCT_NAME_TAKEN"


# --- AC5, AC7: aturan PBI-10 untuk produk Manual ------------------------------------------


def test_produk_manual_nonaktif_hilang_dari_pilihan_sesi_baru(admin):
    """Positif: menonaktifkan produk Manual mengeluarkannya dari pilihan pencatatan manual,
    dan mengaktifkannya lagi mengembalikannya. Produk Otomatis tidak pernah ikut."""
    # Arrange
    product_id = _daftar(admin, _manual("Justisio Uji"))
    _daftar(admin, _otomatis("Otomatis Lain"))
    assert _nama_pilihan(admin, "manual_entry") == ["Justisio Uji"]

    # Act + Assert
    assert admin.post(f"{_PRODUK}/{product_id}/deactivate").status_code == 200
    assert _nama_pilihan(admin, "manual_entry") == []
    assert admin.post(f"{_PRODUK}/{product_id}/activate").status_code == 200
    assert _nama_pilihan(admin, "manual_entry") == ["Justisio Uji"]


def test_produk_manual_terhapus_hilang_dari_semua_daftar(admin):
    """Positif: soft delete PBI-10 pada produk Manual: hilang dari daftar dan pilihan, 404 di
    detail, dan barisnya tetap ada (nama tidak bisa dipakai ulang)."""
    # Arrange
    product_id = _daftar(admin, _manual("LegalHero Uji"))

    # Act
    respons = admin.delete(f"{_PRODUK}/{product_id}")

    # Assert
    assert respons.status_code == 204, respons.text
    assert admin.get(f"{_PRODUK}/{product_id}").status_code == 404
    assert admin.get(_PRODUK).json() == []
    assert _nama_pilihan(admin, "manual_entry") == []
    assert admin.post(_PRODUK, json=_manual("LegalHero Uji")).status_code == 409


# --- AC8: hanya Admin yang menulis produk --------------------------------------------------


def _produk_tersimpan(db_session) -> uuid.UUID:
    """Satu produk Otomatis aktif, ditulis langsung supaya tes peran tidak butuh Admin."""
    produk = AiProduct(
        name="Produk Peran",
        measurement_type=MeasurementType.AUTOMATIC,
        provider_type=ProviderType.OPENAI_COMPATIBLE,
        base_url="https://api.example.test/v1",
        model_name="model-uji",
        credential_encrypted=b"ciphertext-not-the-secret",
        credential_hint="cret",
        rate_limit_per_minute=30,
        monthly_budget_idr=Decimal("1500000.00"),
        created_by=uuid.uuid4(),
    )
    db_session.add(produk)
    db_session.commit()
    return produk.id


_TULIS = [
    pytest.param("post", "", _manual("Produk Baru"), id="daftar"),
    pytest.param(
        "patch",
        "/{id}",
        {"measurement_type": "manual", "vendor_name": "Penyedia", "site_url": "https://x.test"},
        id="ganti-jenis",
    ),
    pytest.param("post", "/{id}/deactivate", None, id="nonaktifkan"),
    pytest.param("post", "/{id}/activate", None, id="aktifkan"),
    pytest.param("delete", "/{id}", None, id="hapus"),
    pytest.param("post", "/{id}/test-connection", None, id="uji-koneksi"),
]


@pytest.mark.parametrize("role", [Role.CONTRIBUTOR, Role.DEVELOPER_AI, Role.VIEWER])
@pytest.mark.parametrize(("metode", "jalur", "body"), _TULIS)
def test_selain_admin_tidak_bisa_menulis_produk(as_role, db_session, role, metode, jalur, body):
    """Negatif: Kontributor, Developer AI, dan Viewer mendapat 403 pada setiap aksi tulis,
    dan produk tidak berubah sedikit pun."""
    # Arrange
    product_id = _produk_tersimpan(db_session)
    client = as_role(role)
    kwargs = {"json": body} if body is not None else {}

    # Act
    respons = client.request(metode.upper(), _PRODUK + jalur.format(id=product_id), **kwargs)

    # Assert
    assert respons.status_code == 403, respons.text
    db_session.expire_all()
    [produk] = db_session.query(AiProduct).all()
    assert (produk.measurement_type, produk.is_active, produk.deleted_at) == (
        MeasurementType.AUTOMATIC,
        True,
        None,
    )


@pytest.mark.parametrize(("metode", "jalur", "body"), _TULIS)
def test_tanpa_login_tidak_bisa_menulis_produk(client, db_session, metode, jalur, body):
    """Negatif: tanpa sesi login setiap aksi tulis 401."""
    # Arrange
    product_id = _produk_tersimpan(db_session)
    kwargs = {"json": body} if body is not None else {}

    # Act
    respons = client.request(metode.upper(), _PRODUK + jalur.format(id=product_id), **kwargs)

    # Assert
    assert respons.status_code == 401, respons.text
