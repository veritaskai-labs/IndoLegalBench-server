"""Kontrak registry produk AI, ditulis sebelum implementasinya ada.

PBI-10 (SCRUM-75), sub task QA (SCRUM-119).

Berkas ini sengaja merah. Setiap test di sini menyatakan satu janji yang
sudah disepakati di dokumen, bukan tebakan: acceptance criteria PBI-10,
matriks peran SCRUM-97, dan desain adapter pada docs/pbi10/adapter-design.md
(PR #20). Begitu SCRUM-112 sampai SCRUM-117 mendarat, yang perlu dilakukan
hanya membuat test ini hijau, bukan memikirkan ulang apa yang harus diuji.

Pesan kegagalannya menyebut sub task yang memblokir, jadi daftar merah di
CI bisa dibaca sebagai daftar pekerjaan yang tersisa.

Dua hal yang SENGAJA tidak dikunci di sini, karena belum diputuskan dan
bukan wewenang QA:

- Nama endpoint. Deskripsi SCRUM-119 menyebut /admin/ai-products, dokumen
  SA menyebut /admin/providers, sementara router yang sudah ada memakai
  prefix /providers. Test di bawah menemukan rutenya sendiri dari router,
  jadi keputusan itu tidak didahului.
- Tipe kembalian test_connection(). Dokumen SA mengusulkan ProviderResponse
  menggantikan bool, dan menandainya perlu persetujuan Rafa. Yang diuji di
  sini bukan usulan itu, melainkan AC-3: hasil uji koneksi harus bisa
  menyampaikan ALASAN gagal. bool tidak bisa; bentuk apa pun yang bisa,
  lolos.
"""

import importlib
import inspect
from dataclasses import fields, is_dataclass

import pytest

from app.modules.providers.adapters import base
from app.shared.security import Role

KREDENSIAL_UJI = "rahasia-kredensial-provider-uji"  # pragma: allowlist secret

# Nilai untuk ProviderResponse.error, docs/pbi10/adapter-design.md.
KODE_ERROR_WAJIB = [
    "AUTH_FAILED",
    "MODEL_NOT_FOUND",
    "RATE_LIMITED",
    "TIMEOUT",
    "NETWORK_ERROR",
    "INVALID_RESPONSE",
    "UPSTREAM_ERROR",
]

# Field AdapterConfig, docs/pbi10/adapter-design.md.
FIELD_CONFIG_WAJIB = [
    "base_url",
    "model_name",
    "auth_header_name",
    "auth_scheme",
    "credential",
    "request_config",
    "timeout_s",
]


def _modul(nama: str):
    """Impor modul kalau ada, kembalikan None kalau belum dibuat."""
    try:
        return importlib.import_module(nama)
    except ModuleNotFoundError:
        return None


def _rute_provider():
    """Rute milik modul providers, ditemukan lewat router-nya sendiri.

    Sengaja tidak memakai string jalur, supaya keputusan penamaan tetap
    di tangan SA dan test ini tidak ikut menguncinya.
    """
    from app.main import app
    from app.modules.providers.router import router

    prefix = router.prefix
    return [r for r in app.routes if getattr(r, "path", "").startswith(prefix)]


# ---------------------------------------------------------------------
# Kontrak adapter — SCRUM-112
# ---------------------------------------------------------------------


def test_adapter_config_sudah_ada():
    """Konfigurasi adapter dipisahkan dari modelnya supaya adapter tidak menyentuh tabel."""
    config = getattr(base, "AdapterConfig", None)

    assert config is not None, (
        "AdapterConfig belum ada di adapters/base.py. Diblokir SCRUM-112 "
        "[SA] ERD, connection test sequence & adapter design."
    )
    assert is_dataclass(config), "AdapterConfig harus dataclass sesuai desain SA"

    dimiliki = {f.name for f in fields(config)}
    kurang = [nama for nama in FIELD_CONFIG_WAJIB if nama not in dimiliki]
    assert not kurang, f"AdapterConfig kehilangan field: {kurang}"


def test_kredensial_tidak_ikut_tercetak_saat_config_dilihat():
    """AC-2 dan aturan L5: kredensial tidak bisa dibaca siapa pun, termasuk Admin.

    AdapterConfig memegang kredensial yang sudah didekripsi. Dataclass
    memasukkan seluruh field ke __repr__ secara bawaan, jadi satu baris
    log, satu traceback yang memuat locals, atau satu print saat debug
    sudah cukup menuliskannya ke tempat yang tidak terenkripsi. Kebocoran
    lewat jalur ini melewati seluruh penjagaan di lapisan API.

    Perbaikannya satu kata: credential: str = field(repr=False).
    """
    config_cls = getattr(base, "AdapterConfig", None)
    if config_cls is None:
        pytest.fail(
            "AdapterConfig belum ada, jadi kebocoran lewat repr belum bisa dicegah. "
            "Diblokir SCRUM-112."
        )

    config = config_cls(
        base_url="https://contoh.invalid",
        model_name="model-uji",
        auth_header_name="Authorization",
        auth_scheme="Bearer",
        credential=KREDENSIAL_UJI,
    )

    assert KREDENSIAL_UJI not in repr(config), (
        "Kredensial muncul di repr(AdapterConfig). Pakai "
        "credential: str = field(repr=False) supaya tidak ikut tercetak di log "
        "maupun traceback."
    )
    assert KREDENSIAL_UJI not in str(config), "Kredensial muncul di str(AdapterConfig)"


def test_kode_error_adapter_lengkap():
    """UI perlu membedakan gagal autentikasi, kehabisan kuota, dan jaringan putus."""
    kurang = [nama for nama in KODE_ERROR_WAJIB if not hasattr(base, nama)]

    assert not kurang, (
        f"Kode error adapter belum ada: {kurang}. Tanpa ini, ProviderResponse.error "
        f"hanya berisi teks bebas dan UI tidak bisa membedakan penyebab kegagalan. "
        f"Diblokir SCRUM-112."
    )


def test_adapter_menerima_konfigurasi_lewat_constructor():
    """Method tetap tanpa argumen konfigurasi, sesuai desain SA.

    Diperiksa lewat vars(), bukan inspect.signature, karena kelas yang
    belum punya __init__ sendiri mewarisi object.__init__ yang bertanda
    tangan (*args, **kwargs) dan akan lolos secara semu.
    """
    assert "__init__" in vars(base.ProviderAdapter), (
        "ProviderAdapter belum punya constructor sendiri, jadi adapter belum punya "
        "cara mengetahui base_url dan kredensialnya. Diblokir SCRUM-112."
    )

    parameter = list(inspect.signature(base.ProviderAdapter.__init__).parameters)
    assert "config" in parameter, (
        f"Constructor ProviderAdapter tidak menerima config. Parameternya: {parameter}"
    )


def test_uji_koneksi_bisa_menyampaikan_alasan_gagal():
    """AC-3: "Hasil uji tampil jelas: berhasil, atau gagal beserta alasannya."

    Yang diuji di sini bukan usulan SA agar test_connection mengembalikan
    ProviderResponse, melainkan acceptance criteria-nya. bool tidak bisa
    membawa alasan; bentuk apa pun yang bisa, lolos test ini.
    """
    kembalian = inspect.signature(base.ProviderAdapter.test_connection).return_annotation

    assert kembalian is not bool, (
        "test_connection() masih mengembalikan bool, sehingga alasan gagal dan "
        "versi model hilang. AC-3 PBI-10 menuntut hasil uji koneksi menyertakan "
        "alasannya. Perubahan kontrak ini menunggu persetujuan Rafa (lihat "
        "docs/pbi10/adapter-design.md)."
    )


# ---------------------------------------------------------------------
# Registry adapter — SCRUM-112
# ---------------------------------------------------------------------


def test_registry_bisa_membangun_adapter_dari_tipe_produk():
    """Menambah produk baru berarti menambah satu berkas, bukan menambah percabangan if."""
    from app.modules.providers import adapters

    build = getattr(adapters, "build_adapter", None)

    assert build is not None, (
        "build_adapter belum ada di adapters/__init__.py. Tanpa registry, modul "
        "runs harus tahu tipe tiap produk dan pola Strategy pada desain SA tidak "
        "terwujud. Diblokir SCRUM-112."
    )


def test_registry_menolak_tipe_produk_yang_tidak_dikenal():
    """Tipe asing harus meledak keras, bukan mengembalikan None yang gagal jauh kemudian."""
    from app.modules.providers import adapters

    build = getattr(adapters, "build_adapter", None)
    if build is None:
        pytest.fail("build_adapter belum ada, diblokir SCRUM-112")

    with pytest.raises(Exception):  # noqa: B017 — tipe pengecualiannya belum ditentukan SA
        build("tipe-yang-tidak-pernah-ada", None)


# ---------------------------------------------------------------------
# Penyimpanan kredensial — SCRUM-114
# ---------------------------------------------------------------------


def test_modul_enkripsi_kredensial_sudah_ada():
    """AC-2: kredensial disimpan terenkripsi, bukan teks biasa."""
    crypto = _modul("app.modules.providers.crypto")

    assert crypto is not None, (
        "app/modules/providers/crypto.py belum ada. Desain SA menetapkan "
        "encrypt_credential dan decrypt_credential berbasis Fernet. "
        "Diblokir SCRUM-114 [BE] Credential encryption & AI product table."
    )
    for nama in ("encrypt_credential", "decrypt_credential"):
        assert hasattr(crypto, nama), f"crypto.{nama} belum ada"


def test_kredensial_terenkripsi_tidak_menyerupai_aslinya():
    """Hasil enkripsi tidak boleh memuat potongan kredensial aslinya."""
    crypto = _modul("app.modules.providers.crypto")
    if crypto is None:
        pytest.fail("crypto.py belum ada, diblokir SCRUM-114")

    tersandi = crypto.encrypt_credential(KREDENSIAL_UJI)

    assert KREDENSIAL_UJI not in str(tersandi)
    assert crypto.decrypt_credential(tersandi) == KREDENSIAL_UJI


# ---------------------------------------------------------------------
# Endpoint registry — SCRUM-115, dan izinnya dari matriks SCRUM-97
# ---------------------------------------------------------------------


def test_registry_sudah_punya_endpoint():
    """AC-1: Admin mendaftarkan produk AI beserta batas pemakaiannya."""
    rute = _rute_provider()

    assert rute, (
        "Modul providers belum punya satu pun endpoint. Diblokir SCRUM-115 "
        "[BE] Registry endpoint dan SCRUM-116 [BE] Connection test endpoint."
    )


@pytest.mark.parametrize("peran", [Role.AUTHOR, Role.REVIEWER, Role.VIEWER])
def test_hanya_admin_yang_boleh_menyentuh_registry(as_role, peran):
    """Matriks peran: baris Provider hanya bertanda x pada kolom Admin.

    Selama endpoint-nya belum ada, test ini merah karena tidak ada yang
    bisa diperiksa. Begitu SCRUM-115 mendarat, ia langsung berjalan
    terhadap rute yang sebenarnya tanpa perlu disunting.
    """
    rute = _rute_provider()
    if not rute:
        pytest.fail("Belum ada endpoint provider untuk diperiksa izinnya, diblokir SCRUM-115")

    client = as_role(peran)
    for r in rute:
        for metode in sorted(getattr(r, "methods", set()) - {"HEAD", "OPTIONS"}):
            respons = client.request(metode, r.path)
            assert respons.status_code == 403, (
                f"{peran.value} mendapat {respons.status_code} pada {metode} {r.path}. "
                f"Matriks peran hanya mengizinkan Admin untuk resource Provider."
            )
