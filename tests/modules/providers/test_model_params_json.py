"""Parameter model sebagai JSON bebas. SCRUM-177 SCRUM-247 (revisi UAT klien PBI-23 AC2).

Klien meminta parameter diisi sebagai teks JSON dan dikirim apa adanya ke API
produk AI, supaya model atau parameter baru tidak butuh perubahan kode. Server
tetap menjaga tiga hal: kunci milik sistem (model, prompt, streaming) tidak bisa
ditimpa, kunci rahasia tidak tersimpan (L5), dan ukurannya dibatasi.

Label di docstring: Positif, Negatif, Edge.
"""

import json
import uuid

import pytest
from cryptography.fernet import Fernet

from app.modules.providers import rules
from app.modules.providers.models import AiProduct
from app.shared.config import get_settings
from app.shared.security import Role

_SECRET = "provider-token-uji-247"  # pragma: allowlist secret


@pytest.fixture(autouse=True)
def encryption_key(monkeypatch):
    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", Fernet.generate_key().decode())
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def admin(as_role):
    return as_role(Role.ADMIN)


def _otomatis(**ubah) -> dict:
    # Nilai sintetis, bukan produk yang didaftarkan Veritask.
    body = {
        "name": f"Otomatis 247 {uuid.uuid4().hex[:6]}",
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


def _bersarang(kedalaman: int) -> dict:
    """Object dengan `kedalaman` tingkat object; tingkat terdalam berisi angka."""
    isi: object = 1
    for _ in range(kedalaman):
        isi = {"a": isi}
    assert isinstance(isi, dict)
    return isi


# --- Aturan (rules.model_params_problem) ------------------------------------------------


@pytest.mark.parametrize("params", [None, {}])
def test_kosong_tidak_bermasalah(params):
    """Positif: kosong berarti default penyedia."""
    assert rules.model_params_problem(params) is None


def test_parameter_bebas_dan_bersarang_lolos():
    """Positif: kunci yang tidak dikenal server tetap boleh, karena tiap model berbeda."""
    params = {
        "reasoning_effort": "high",
        "thinking": {"type": "enabled", "budget_tokens": 1024},
        "tools": [{"type": "web_search"}],
        "max_tokens": 512,
    }
    assert rules.model_params_problem(params) is None


@pytest.mark.parametrize("kunci", ["model", "messages", "input", "stream", "MODEL"])
def test_kunci_milik_sistem_ditolak(kunci):
    """Negatif: model dan prompt diatur adaptor; stream membuat jawaban tidak terbaca."""
    masalah = rules.model_params_problem({kunci: "x"})
    assert masalah is not None
    assert masalah.code == rules.MODEL_PARAMS_RESERVED_KEY
    assert masalah.field == "model_params"
    assert kunci in masalah.message


@pytest.mark.parametrize(
    "params",
    [
        {"api_key": "abc"},
        {"Authorization": "Bearer abc"},
        {"x-api-key": "abc"},
        {"password": "abc"},
        {"extra_headers": {"x-goog-api-key": "abc"}},
        {"tools": [{"access_token": "abc"}]},
    ],
)
def test_kunci_rahasia_ditolak_juga_yang_bersarang(params):
    """Negatif (L5): parameter tersimpan apa adanya dan ikut audit log, jadi rahasia ditolak."""
    masalah = rules.model_params_problem(params)
    assert masalah is not None
    assert masalah.code == rules.MODEL_PARAMS_SECRET


@pytest.mark.parametrize("kunci", ["max_tokens", "max_output_tokens", "budget_tokens"])
def test_kunci_berisi_kata_token_bukan_rahasia(kunci):
    """Edge: batas token adalah parameter biasa, jangan tertolak sebagai rahasia."""
    assert rules.model_params_problem({kunci: 100}) is None


def test_batas_ukuran_tepat_lolos_dan_lebih_satu_byte_ditolak():
    """Edge: ukuran dihitung dari JSON ringkas UTF-8."""
    kosong = len(json.dumps({"p": ""}, separators=(",", ":")).encode())
    pas = {"p": "a" * (rules.MODEL_PARAMS_MAX_BYTES - kosong)}
    lebih = {"p": "a" * (rules.MODEL_PARAMS_MAX_BYTES - kosong + 1)}
    assert rules.model_params_problem(pas) is None
    masalah = rules.model_params_problem(lebih)
    assert masalah is not None
    assert masalah.code == rules.MODEL_PARAMS_TOO_LARGE


def test_kedalaman_maksimum_lolos_dan_lebih_satu_ditolak():
    """Edge: list juga dihitung sebagai satu tingkat."""
    assert rules.model_params_problem(_bersarang(rules.MODEL_PARAMS_MAX_DEPTH)) is None
    masalah = rules.model_params_problem(_bersarang(rules.MODEL_PARAMS_MAX_DEPTH + 1))
    assert masalah is not None
    assert masalah.code == rules.MODEL_PARAMS_TOO_LARGE
    # object + 5 list = 6 tingkat
    dalam_list = {"a": [[[[[1]]]]]}
    assert rules.model_params_problem(dalam_list) is not None


# --- API /admin/providers ----------------------------------------------------------------


def test_admin_menyimpan_json_bebas_apa_adanya(admin, db_session):
    """Positif: kunci baru tersimpan dan terbaca kembali tanpa diubah."""
    params = {"reasoning_effort": "high", "thinking": {"type": "enabled"}, "top_p": 0.9}
    respons = admin.post("/admin/providers", json=_otomatis(model_params=params))
    assert respons.status_code == 201, respons.text
    assert respons.json()["model_params"] == params
    produk = db_session.get(AiProduct, uuid.UUID(respons.json()["id"]))
    assert produk.model_params == params


def test_nilai_null_di_dalam_json_tetap_tersimpan(admin, db_session):
    """Edge: null milik parameter (mis. stop: null) bukan tanda kosong, jadi tidak dibuang."""
    respons = admin.post("/admin/providers", json=_otomatis(model_params={"stop": None}))
    assert respons.status_code == 201, respons.text
    assert respons.json()["model_params"] == {"stop": None}


def test_object_kosong_disimpan_sebagai_null(admin, db_session):
    """Edge: {} sama dengan tidak diisi, jadi kolom NULL dan penyedia memakai default."""
    respons = admin.post("/admin/providers", json=_otomatis(model_params={}))
    assert respons.status_code == 201, respons.text
    assert respons.json()["model_params"] is None
    assert db_session.get(AiProduct, uuid.UUID(respons.json()["id"])).model_params is None


@pytest.mark.parametrize("bukan_object", [["temperature", 0.3], "temperature=0.3", 0.3, True])
def test_bukan_object_ditolak_422(admin, bukan_object):
    """Negatif: JSON harus berupa object, bukan array atau nilai tunggal."""
    respons = admin.post("/admin/providers", json=_otomatis(model_params=bukan_object))
    assert respons.status_code == 422
    assert "model_params" in json.dumps(respons.json())


@pytest.mark.parametrize(
    ("params", "kode"),
    [
        ({"model": "lain"}, "MODEL_PARAMS_RESERVED_KEY"),
        ({"api_key": "abc"}, "MODEL_PARAMS_SECRET"),
        ({"p": "a" * 5000}, "MODEL_PARAMS_TOO_LARGE"),
    ],
)
def test_daftar_dengan_parameter_terlarang_ditolak_tanpa_menyimpan(admin, db_session, params, kode):
    """Negatif: kode dan field jelas, dan produk tidak tersimpan."""
    respons = admin.post("/admin/providers", json=_otomatis(model_params=params))
    assert respons.status_code == 422
    assert respons.json()["code"] == kode
    assert respons.json()["field"] == "model_params"
    assert db_session.query(AiProduct).count() == 0


def test_ubah_parameter_mengganti_seluruh_json(admin, db_session):
    """Positif: PATCH mengganti object lama, kunci lama yang tidak dikirim hilang."""
    dibuat = admin.post("/admin/providers", json=_otomatis(model_params={"temperature": 0.3}))
    product_id = dibuat.json()["id"]
    respons = admin.patch(
        f"/admin/providers/{product_id}", json={"model_params": {"reasoning_effort": "low"}}
    )
    assert respons.status_code == 200, respons.text
    assert respons.json()["model_params"] == {"reasoning_effort": "low"}


def test_ubah_parameter_jadi_kosong_menyimpan_null(admin, db_session):
    """Edge: mengosongkan textarea mengirim {} dan kembali ke default penyedia."""
    dibuat = admin.post("/admin/providers", json=_otomatis(model_params={"temperature": 0.3}))
    product_id = dibuat.json()["id"]
    respons = admin.patch(f"/admin/providers/{product_id}", json={"model_params": {}})
    assert respons.status_code == 200, respons.text
    assert respons.json()["model_params"] is None
    db_session.expire_all()
    assert db_session.get(AiProduct, uuid.UUID(product_id)).model_params is None


def test_ubah_dengan_kunci_rahasia_ditolak_dan_nilai_lama_tetap(admin, db_session):
    """Negatif: PATCH memakai aturan yang sama dengan POST."""
    dibuat = admin.post("/admin/providers", json=_otomatis(model_params={"temperature": 0.3}))
    product_id = dibuat.json()["id"]
    respons = admin.patch(
        f"/admin/providers/{product_id}", json={"model_params": {"Authorization": "Bearer x"}}
    )
    assert respons.status_code == 422
    assert respons.json()["code"] == "MODEL_PARAMS_SECRET"
    db_session.expire_all()
    assert db_session.get(AiProduct, uuid.UUID(product_id)).model_params == {"temperature": 0.3}


def test_produk_manual_tetap_tidak_boleh_punya_parameter_model(admin):
    """Negatif: parameter teks bebas Manual ada di manual_params, bukan di sini."""
    body = {
        "name": "Manual 247",
        "measurement_type": "manual",
        "vendor_name": "Penyedia Uji",
        "site_url": "https://contoh.test",
        "model_params": {"temperature": 0.3},
    }
    respons = admin.post("/admin/providers", json=body)
    assert respons.status_code == 422
    assert respons.json()["code"] == "FIELD_NOT_ALLOWED_FOR_TYPE"
