"""SCRUM-258: daftar provider penilai (LLM judge) untuk Admin dan form pengukuran.

Path (keputusan PO 7 Nov, deviasi dari Jira "/judge-providers"): registri lengkap di
/admin/judge-providers (Admin), daftar pilihan penilai aktif di /judge-providers
(Admin dan Developer AI, yang boleh membuat pengukuran, klien No. 35), seperti
/admin/providers dan /ai-products.

Kredensial hanya ditulis (L5, PBI-10 AC2): respons memuat credential_hint dan
has_credential, tidak pernah ciphertext atau plaintext. PATCH sengaja dibatasi pada
kredensial, harga, dan anggaran (keputusan PO K5): nama, jenis, URL, dan model yang
dirujuk sesi lama tidak bisa diganti diam-diam. Aktif/nonaktif lewat endpoint
terpisah, sama seperti produk AI.

Isolasi: SQLite per tes (fixture db_session), login lewat fake OIDC sungguhan
(tests/login.py), kunci enkripsi lewat monkeypatch env.
"""

import logging
import uuid
from decimal import Decimal

import pytest
from cryptography.fernet import Fernet

from app.modules.auth.models import User
from app.modules.auth.seeds import ADMIN_SUB, AUTHOR_SUB, DEVELOPER_AI_SUB, VIEWER_SUB
from app.modules.providers.crypto import decrypt_credential
from app.modules.providers.models import JudgeProvider
from app.shared.config import get_settings
from tests.login import complete_login

ADMIN_URL = "/admin/judge-providers"
OPTIONS_URL = "/judge-providers"
_SECRET = "judge-token-value-4hTz"  # pragma: allowlist secret
_OTHER_SECRET = "judge-replacement-8wQn"  # pragma: allowlist secret


@pytest.fixture
def encryption_key(monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", key)
    get_settings.cache_clear()
    yield key
    get_settings.cache_clear()


@pytest.fixture
def admin(client, db_session, encryption_key):
    complete_login(client, db_session, ADMIN_SUB)
    return client


def _payload(**override) -> dict:
    body = {
        "name": "DeepSeek penilai",
        "provider_type": "openai_compatible",
        "base_url": "https://judge.example.test/v1/chat/completions",
        "model_name": "judge-model-1",
        "credential": _SECRET,
        "price_input_usd_per_mtok": "0.2700",
        "price_output_usd_per_mtok": "1.1000",
        "monthly_budget_idr": "500000.00",
    }
    body.update(override)
    return body


def _buat(client, **override) -> dict:
    response = client.post(ADMIN_URL, json=_payload(**override))
    assert response.status_code == 201, response.text
    return response.json()


def _assert_tanpa_rahasia(body, *secrets: str) -> None:
    teks = str(body)
    assert "credential_encrypted" not in teks
    assert "'credential'" not in teks
    for secret in secrets:
        assert secret not in teks


def _baris(db_session, judge_id: str) -> JudgeProvider:
    db_session.expire_all()
    return db_session.get(JudgeProvider, uuid.UUID(judge_id))


# --- POST /admin/judge-providers --------------------------------------------------


def test_daftar_penilai_menyimpan_kredensial_terenkripsi_dan_hanya_hint(admin, db_session):
    """Positif: AC6, penilai terdaftar aktif; kredensial terenkripsi, respons hanya hint."""
    body = _buat(admin)

    _assert_tanpa_rahasia(body, _SECRET)
    assert body["credential_hint"] == _SECRET[-4:]
    assert body["has_credential"] is True
    assert body["is_active"] is True
    assert body["name"] == "DeepSeek penilai"
    assert body["provider_type"] == "openai_compatible"
    assert body["model_name"] == "judge-model-1"
    assert Decimal(str(body["price_input_usd_per_mtok"])) == Decimal("0.27")
    assert Decimal(str(body["price_output_usd_per_mtok"])) == Decimal("1.1")
    assert Decimal(str(body["monthly_budget_idr"])) == Decimal("500000")
    admin_id = db_session.query(User).filter(User.zitadel_sub == ADMIN_SUB).one().id
    assert body["created_by"] == str(admin_id)
    baris = _baris(db_session, body["id"])
    assert decrypt_credential(baris.credential_encrypted) == _SECRET
    assert baris.credential_encrypted != _SECRET.encode()


def test_daftar_penilai_nonaktif_sejak_awal(admin):
    """Edge: Admin boleh menyiapkan penilai tanpa langsung muncul di dropdown."""
    assert _buat(admin, is_active=False)["is_active"] is False


def test_nama_dan_model_dibersihkan_dari_karakter_kosong_di_tepi(admin):
    """Edge: zero-width di tepi dibuang (strip_blank), sama dengan CHECK database."""
    body = _buat(admin, name="​ Penilai A  ", model_name=" judge-model-1 ")

    assert body["name"] == "Penilai A"
    assert body["model_name"] == "judge-model-1"


@pytest.mark.parametrize(
    "field",
    [
        "name",
        "provider_type",
        "base_url",
        "model_name",
        "credential",
        "price_input_usd_per_mtok",
        "price_output_usd_per_mtok",
        "monthly_budget_idr",
    ],
)
def test_isian_wajib_tidak_boleh_hilang(admin, field):
    """Negatif: tanpa harga atau anggaran biaya penilai tidak bisa dihitung (AC6, No. 63 A)."""
    body = _payload()
    body.pop(field)

    response = admin.post(ADMIN_URL, json=body)

    assert response.status_code == 422


@pytest.mark.parametrize(
    ("field", "nilai"),
    [
        ("name", "​  "),
        ("name", "x" * 201),
        ("model_name", "   "),
        ("base_url", "ftp://judge.example.test"),
        ("base_url", "bukan url"),
        ("credential", "abc"),
        ("price_input_usd_per_mtok", "-0.0001"),
        ("price_output_usd_per_mtok", "0.00001"),
        ("monthly_budget_idr", "0"),
        ("monthly_budget_idr", "-1"),
        ("provider_type", "ollama"),
        ("is_active", None),
    ],
    ids=[
        "nama-kosong",
        "nama-201",
        "model-spasi",
        "url-ftp",
        "url-salah",
        "kredensial-3",
        "harga-negatif",
        "harga-5-desimal",
        "anggaran-nol",
        "anggaran-negatif",
        "jenis-asing",
        "aktif-null",
    ],
)
def test_isian_tidak_sah_ditolak_422(admin, db_session, field, nilai):
    response = admin.post(ADMIN_URL, json=_payload(**{field: nilai}))

    assert response.status_code == 422
    assert db_session.query(JudgeProvider).count() == 0


def test_isian_di_luar_kontrak_ditolak(admin):
    """Negatif: rate_limit dan sejenisnya bukan isian penilai (D6), jangan diam-diam dibuang."""
    response = admin.post(ADMIN_URL, json=_payload(rate_limit_per_minute=30))

    assert response.status_code == 422


def test_harga_nol_diterima(admin):
    """Edge: penilai gratis tetap sah; biayanya 0, bukan tidak diketahui."""
    body = _buat(admin, price_input_usd_per_mtok="0", price_output_usd_per_mtok="0")

    assert Decimal(str(body["price_input_usd_per_mtok"])) == 0


@pytest.mark.parametrize("aktif", [True, False], ids=["aktif", "nonaktif"])
def test_nama_yang_sudah_dipakai_409(admin, aktif):
    """Negatif: nama unik selamanya, juga terhadap penilai nonaktif."""
    _buat(admin, is_active=aktif)

    response = admin.post(ADMIN_URL, json=_payload(name=" DeepSeek penilai "))

    assert response.status_code == 409
    assert response.json()["code"] == "JUDGE_PROVIDER_NAME_TAKEN"


def test_nama_bentrok_yang_lolos_cek_awal_tetap_409(admin, monkeypatch):
    """Corner: dua POST bersamaan sama-sama lolos cek nama; unik database yang menolak,
    jawabannya tetap 409, bukan 500."""
    from app.modules.providers import judge_repository

    _buat(admin)
    monkeypatch.setattr(judge_repository, "get_by_name", lambda db, name: None)

    response = admin.post(ADMIN_URL, json=_payload())

    assert response.status_code == 409
    assert response.json()["code"] == "JUDGE_PROVIDER_NAME_TAKEN"


def test_tanpa_kunci_enkripsi_ditolak_tanpa_menyimpan(client, db_session, monkeypatch):
    """Negatif: kunci enkripsi kosong; tidak pernah menyimpan plaintext."""
    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", "")
    get_settings.cache_clear()
    complete_login(client, db_session, ADMIN_SUB)

    response = client.post(ADMIN_URL, json=_payload())

    get_settings.cache_clear()
    assert response.status_code == 422
    assert response.json()["field"] == "credential"
    assert db_session.query(JudgeProvider).count() == 0


def test_kredensial_tidak_masuk_log(admin, caplog):
    """Negatif: L5, kredensial dan hint tidak ditulis ke log mana pun."""
    with caplog.at_level(logging.DEBUG):
        body = _buat(admin)
        admin.patch(f"{ADMIN_URL}/{body['id']}", json={"credential": _OTHER_SECRET})

    assert _SECRET not in caplog.text
    assert _OTHER_SECRET not in caplog.text


# --- GET /admin/judge-providers ---------------------------------------------------


def test_daftar_admin_kosong(admin):
    response = admin.get(ADMIN_URL)

    assert response.status_code == 200
    assert response.json() == []


def test_daftar_admin_memuat_nonaktif_dengan_aktif_lebih_dulu(admin):
    """Positif: Admin melihat semua penilai; yang aktif di atas, lalu urut nama."""
    _buat(admin, name="Zeta")
    _buat(admin, name="Beta", is_active=False)
    _buat(admin, name="Alfa")

    body = admin.get(ADMIN_URL).json()

    assert [item["name"] for item in body] == ["Alfa", "Zeta", "Beta"]
    _assert_tanpa_rahasia(body, _SECRET)


def test_detail_penilai(admin):
    dibuat = _buat(admin)

    response = admin.get(f"{ADMIN_URL}/{dibuat['id']}")

    assert response.status_code == 200
    assert response.json() == dibuat


def test_detail_penilai_tidak_dikenal_404(admin):
    response = admin.get(f"{ADMIN_URL}/{uuid.uuid4()}")

    assert response.status_code == 404
    assert response.json()["code"] == "JUDGE_PROVIDER_NOT_FOUND"


# --- GET /judge-providers (dropdown SCRUM-211) ------------------------------------


def test_pilihan_hanya_penilai_aktif_dengan_isian_minimal(admin):
    """Positif: dropdown tidak menerima URL, hint, harga, atau anggaran."""
    aktif = _buat(admin, name="Penilai aktif")
    _buat(admin, name="Penilai nonaktif", is_active=False)

    response = admin.get(OPTIONS_URL)

    assert response.status_code == 200
    assert response.json() == [
        {
            "id": aktif["id"],
            "name": "Penilai aktif",
            "provider_type": "openai_compatible",
            "model_name": "judge-model-1",
        }
    ]


def test_pilihan_kosong_kalau_semua_nonaktif(admin):
    """Edge: form tetap bisa dibuka; penilai boleh kosong (keputusan PO K2)."""
    _buat(admin, is_active=False)

    assert admin.get(OPTIONS_URL).json() == []


def test_developer_ai_melihat_pilihan_penilai(client, db_session, encryption_key):
    """Positif: Developer AI boleh membuat pengukuran (No. 35), jadi butuh daftarnya."""
    complete_login(client, db_session, ADMIN_SUB)
    _buat(client)
    client.cookies.clear()
    complete_login(client, db_session, DEVELOPER_AI_SUB)

    response = client.get(OPTIONS_URL)

    assert response.status_code == 200
    assert [item["name"] for item in response.json()] == ["DeepSeek penilai"]


# --- PATCH /admin/judge-providers/{id} --------------------------------------------


def test_ganti_kredensial_mengganti_hint_dan_ciphertext(admin, db_session):
    dibuat = _buat(admin)

    response = admin.patch(f"{ADMIN_URL}/{dibuat['id']}", json={"credential": _OTHER_SECRET})

    assert response.status_code == 200
    body = response.json()
    _assert_tanpa_rahasia(body, _SECRET, _OTHER_SECRET)
    assert body["credential_hint"] == _OTHER_SECRET[-4:]
    assert decrypt_credential(_baris(db_session, dibuat["id"]).credential_encrypted) == (
        _OTHER_SECRET
    )


def test_ubah_harga_dan_anggaran(admin):
    dibuat = _buat(admin)

    body = admin.patch(
        f"{ADMIN_URL}/{dibuat['id']}",
        json={
            "price_input_usd_per_mtok": "0.5",
            "price_output_usd_per_mtok": "2",
            "monthly_budget_idr": "750000",
        },
    ).json()

    assert Decimal(str(body["price_input_usd_per_mtok"])) == Decimal("0.5")
    assert Decimal(str(body["price_output_usd_per_mtok"])) == Decimal("2")
    assert Decimal(str(body["monthly_budget_idr"])) == Decimal("750000")
    assert body["credential_hint"] == _SECRET[-4:]


def test_patch_kosong_tidak_mengubah_apa_pun(admin):
    """Edge: body {} sah dan mengembalikan data yang sama."""
    dibuat = _buat(admin)

    response = admin.patch(f"{ADMIN_URL}/{dibuat['id']}", json={})

    assert response.status_code == 200
    assert response.json()["credential_hint"] == dibuat["credential_hint"]
    assert response.json()["monthly_budget_idr"] == dibuat["monthly_budget_idr"]


@pytest.mark.parametrize(
    "field",
    ["credential", "price_input_usd_per_mtok", "price_output_usd_per_mtok", "monthly_budget_idr"],
)
def test_null_eksplisit_untuk_isian_wajib_ditolak(admin, field):
    """Negatif: catatan PO K5, null bukan "tidak diubah" untuk kolom NOT NULL."""
    dibuat = _buat(admin)

    response = admin.patch(f"{ADMIN_URL}/{dibuat['id']}", json={field: None})

    assert response.status_code == 422


@pytest.mark.parametrize(
    "isi",
    [
        {"name": "Nama baru"},
        {"model_name": "judge-model-2"},
        {"base_url": "https://lain.example.test"},
        {"provider_type": "anthropic_messages"},
        {"is_active": False},
    ],
    ids=["nama", "model", "url", "jenis", "aktif"],
)
def test_patch_di_luar_cakupan_ditolak(admin, isi):
    """Negatif: identitas penilai yang dirujuk sesi lama tidak boleh diganti (K5);
    aktif/nonaktif lewat endpoint sendiri."""
    dibuat = _buat(admin)

    response = admin.patch(f"{ADMIN_URL}/{dibuat['id']}", json=isi)

    assert response.status_code == 422


@pytest.mark.parametrize(
    ("isi", "sah"),
    [
        ({"credential": "abc"}, False),
        ({"credential": "abcd"}, True),
        ({"monthly_budget_idr": "0"}, False),
        ({"price_input_usd_per_mtok": "-1"}, False),
    ],
    ids=["kredensial-3", "kredensial-4", "anggaran-nol", "harga-negatif"],
)
def test_batas_isian_patch(admin, isi, sah):
    dibuat = _buat(admin)

    response = admin.patch(f"{ADMIN_URL}/{dibuat['id']}", json=isi)

    assert response.status_code == (200 if sah else 422)


def test_patch_penilai_tidak_dikenal_404(admin):
    response = admin.patch(f"{ADMIN_URL}/{uuid.uuid4()}", json={"monthly_budget_idr": "1"})

    assert response.status_code == 404


# --- Aktif / nonaktif -------------------------------------------------------------


def test_nonaktifkan_lalu_aktifkan_lagi(admin):
    dibuat = _buat(admin)

    mati = admin.post(f"{ADMIN_URL}/{dibuat['id']}/deactivate")
    pilihan_saat_mati = admin.get(OPTIONS_URL).json()
    hidup = admin.post(f"{ADMIN_URL}/{dibuat['id']}/activate")

    assert mati.status_code == 200
    assert mati.json()["is_active"] is False
    assert pilihan_saat_mati == []
    assert hidup.json()["is_active"] is True
    assert [item["id"] for item in admin.get(OPTIONS_URL).json()] == [dibuat["id"]]


def test_nonaktifkan_dua_kali_tetap_nonaktif(admin):
    """Edge: aksi berulang tidak galat."""
    dibuat = _buat(admin)
    admin.post(f"{ADMIN_URL}/{dibuat['id']}/deactivate")

    response = admin.post(f"{ADMIN_URL}/{dibuat['id']}/deactivate")

    assert response.status_code == 200
    assert response.json()["is_active"] is False


@pytest.mark.parametrize("aksi", ["activate", "deactivate"])
def test_aktif_nonaktif_penilai_tidak_dikenal_404(admin, aksi):
    response = admin.post(f"{ADMIN_URL}/{uuid.uuid4()}/{aksi}")

    assert response.status_code == 404


# --- Hak akses (OWASP A01) --------------------------------------------------------

_ID = "00000000-0000-0000-0000-000000000001"
_ADMIN_ROUTES = [
    ("post", ADMIN_URL),
    ("get", ADMIN_URL),
    ("get", f"{ADMIN_URL}/{_ID}"),
    ("patch", f"{ADMIN_URL}/{_ID}"),
    ("post", f"{ADMIN_URL}/{_ID}/activate"),
    ("post", f"{ADMIN_URL}/{_ID}/deactivate"),
]


def _panggil(client, method: str, url: str):
    if method in {"post", "patch"}:
        return getattr(client, method)(url, json=_payload())
    return getattr(client, method)(url)


@pytest.mark.parametrize(("method", "url"), [*_ADMIN_ROUTES, ("get", OPTIONS_URL)])
def test_tanpa_login_401(client, method, url):
    response = _panggil(client, method, url)

    assert response.status_code == 401


@pytest.mark.parametrize("sub", [VIEWER_SUB, AUTHOR_SUB, DEVELOPER_AI_SUB])
@pytest.mark.parametrize(("method", "url"), _ADMIN_ROUTES)
def test_registri_penilai_hanya_admin(client, db_session, sub, method, url):
    complete_login(client, db_session, sub)

    response = _panggil(client, method, url)

    assert response.status_code == 403
    assert db_session.query(JudgeProvider).count() == 0


@pytest.mark.parametrize("sub", [VIEWER_SUB, AUTHOR_SUB])
def test_pilihan_penilai_tidak_untuk_viewer_dan_kontributor(client, db_session, sub):
    """Negatif: hanya pembuat pengukuran (Admin, Developer AI) yang butuh dropdown ini."""
    complete_login(client, db_session, sub)

    response = client.get(OPTIONS_URL)

    assert response.status_code == 403
