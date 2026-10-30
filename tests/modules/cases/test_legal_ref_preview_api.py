"""POST /legal-refs/preview lewat HTTP (PBI-21 AC6, AC8, AC10; SCRUM-162).

Kontrak yang disetujui PO 18 Okt: body {legal_refs, case_id?}, jawaban satu
item per rujukan {display_text, error}, hanya Author dan Admin. case_id hanya
dipakai untuk membaca kode jenis yang tersimpan di versi terbuka (aturan
REGULATION_TYPE_INACTIVE), dan pratinjau tidak menulis apa pun.

Bukti "sama dengan server": hasil pratinjau dibandingkan langsung dengan
jawaban simpan (POST kasus) untuk rujukan yang sama.

Contoh hukum hanya KTK-001 dan contoh AC10 (dari Aileen, belum dikonfirmasi
tim legal). Nilai lain ("1", kode "kode_lain") adalah input format sintetis.
"""

import uuid
from typing import Any

import pytest
from sqlalchemy import func, select, update

from app.main import app
from app.modules.cases.models import Case, CaseVersion
from app.modules.cases.validation import current_year_wib
from app.modules.legal_refs.models import LegalRef, RegulationType
from app.shared.security import Role
from tests.legal_refs import rujukan_ac10, rujukan_ktk_001
from tests.modules.cases.test_cases import _badan, _buat, _ganti, _suite

PATH = "/legal-refs/preview"
AUTHOR_LAIN = uuid.UUID("00000000-0000-0000-0000-00000000b162")
ADMIN_LAIN = uuid.UUID("00000000-0000-0000-0000-00000000c162")
TEKS_KTK_001 = "Pasal 156 UU 13/2003"
TEKS_AC10 = "Pasal 156 UU 13/2003 jo. Pasal II Angka 5 UU 6/2023"


def _pratinjau(client, *rujukan: Any, case_id: str | None = None):
    body: dict[str, Any] = {"legal_refs": list(rujukan)}
    if case_id is not None:
        body["case_id"] = case_id
    return client.post(PATH, json=body)


def _items(respons) -> list[dict[str, Any]]:
    assert respons.status_code == 200, respons.text
    return respons.json()["items"]


def _nonaktifkan(db_session, code: str) -> None:
    db_session.execute(
        update(RegulationType).where(RegulationType.code == code).values(is_active=False)
    )
    db_session.commit()


def _ubah(rujukan: dict[str, Any], link: int = 0, **isi: Any) -> dict[str, Any]:
    rujukan["links"][link].update(isi)
    return rujukan


def _tanpa(rujukan: dict[str, Any], nama: str, link: int = 0) -> dict[str, Any]:
    del rujukan["links"][link][nama]
    return rujukan


# --- positif -----------------------------------------------------------------


def test_author_melihat_teks_tiap_rujukan(as_role):
    # Arrange
    client = as_role(Role.CONTRIBUTOR)

    # Act
    items = _items(_pratinjau(client, rujukan_ktk_001(), rujukan_ac10()))

    # Assert
    assert items == [
        {"display_text": TEKS_KTK_001, "error": None},
        {"display_text": TEKS_AC10, "error": None},
    ]


def test_admin_boleh_memakai_pratinjau(as_role):
    client = as_role(Role.ADMIN)

    assert _items(_pratinjau(client, rujukan_ktk_001())) == [
        {"display_text": TEKS_KTK_001, "error": None}
    ]


def test_daftar_kosong(as_role):
    client = as_role(Role.CONTRIBUTOR)

    assert _items(_pratinjau(client)) == []


def test_rujukan_salah_tetap_200_dengan_error_per_item(as_role):
    """Error pratinjau adalah data, bukan 422: rujukan lain tetap mendapat teksnya."""
    # Arrange
    client = as_role(Role.CONTRIBUTOR)

    # Act
    items = _items(_pratinjau(client, _ubah(rujukan_ktk_001(), number=""), rujukan_ac10()))

    # Assert
    assert items == [
        {
            "display_text": None,
            "error": {
                "code": "FIELD_REQUIRED",
                "message": "Nomor peraturan wajib diisi.",
                "field": "legal_refs[0].links[0].number",
            },
        },
        {"display_text": TEKS_AC10, "error": None},
    ]


# --- sama dengan simpan ------------------------------------------------------


@pytest.mark.parametrize("kind", ["free_text", "structured"])
@pytest.mark.parametrize("posisi", [0, 1])
def test_links_hilang_memakai_pesan_netral_di_simpan_dan_pratinjau(as_role, kind, posisi):
    """Tanpa konteks kind, pesan schema tidak boleh menyebut bentuk terstruktur."""
    client = as_role(Role.CONTRIBUTOR)
    rujukan = [rujukan_ktk_001()] * posisi + [{"kind": kind, "free_text": "Format teks bebas"}]

    simpan = _buat(client, _suite(client), legal_refs=rujukan)
    items = _items(_pratinjau(client, *rujukan))

    assert simpan.status_code == 422
    assert simpan.json() == {
        "code": "FIELD_REQUIRED",
        "field": f"legal_refs[{posisi}].links",
        "message": "Peraturan wajib diisi.",
    }
    assert items[posisi] == {"display_text": None, "error": simpan.json()}


@pytest.mark.parametrize(
    "rusak",
    [
        pytest.param(lambda: _ubah(rujukan_ktk_001(), number=" "), id="nomor-kosong"),
        pytest.param(lambda: _tanpa(rujukan_ktk_001(), "pasal"), id="pasal-kosong"),
        pytest.param(lambda: _tanpa(rujukan_ac10(), "angka", link=1), id="angka-kosong"),
        pytest.param(lambda: _ubah(rujukan_ktk_001(), year=None), id="tahun-null"),
        pytest.param(lambda: _ubah(rujukan_ktk_001(), pasal="IIII"), id="format-pasal"),
        pytest.param(lambda: _ubah(rujukan_ac10(), angka="1"), id="angka-di-bentuk-pasal"),
        pytest.param(
            lambda: _ubah(rujukan_ac10(), link=1, connector="none"), id="tanpa-penghubung"
        ),
        pytest.param(
            lambda: _ubah(rujukan_ktk_001(), year=current_year_wib() + 1), id="tahun-depan"
        ),
        pytest.param(
            lambda: _ubah(rujukan_ktk_001(), regulation_type="kode_lain"), id="jenis-tidak-dikenal"
        ),
    ],
)
def test_error_pratinjau_sama_dengan_error_simpan(as_role, rusak):
    # Arrange
    client = as_role(Role.CONTRIBUTOR)
    suite_id = _suite(client)

    # Act
    simpan = _buat(client, suite_id, legal_refs=[rusak()])
    items = _items(_pratinjau(client, rusak()))

    # Assert
    assert simpan.status_code == 422
    assert items == [{"display_text": None, "error": simpan.json()}]


@pytest.mark.parametrize("rujukan", [rujukan_ktk_001, rujukan_ac10], ids=["ktk-001", "ac10"])
def test_teks_pratinjau_sama_dengan_display_text_simpan(as_role, rujukan):
    # Arrange
    client = as_role(Role.CONTRIBUTOR)

    # Act
    simpan = _buat(client, _suite(client), legal_refs=[rujukan()])
    items = _items(_pratinjau(client, rujukan()))

    # Assert
    assert simpan.status_code == 201, simpan.text
    assert items == [
        {"display_text": simpan.json()["legal_refs"][0]["display_text"], "error": None}
    ]


# --- jenis nonaktif dan case_id (permintaan PO 18 Okt) -----------------------


def test_rujukan_lama_dengan_jenis_nonaktif_diterima_simpan_dan_pratinjau(as_role, db_session):
    # Arrange
    client = as_role(Role.CONTRIBUTOR)
    case_id = _buat(client, _suite(client), legal_refs=[rujukan_ktk_001()]).json()["id"]
    _nonaktifkan(db_session, "uu")

    # Act
    items = _items(_pratinjau(client, rujukan_ktk_001(), case_id=case_id))
    simpan = client.put(f"/cases/{case_id}", json=_badan(legal_refs=[rujukan_ktk_001()]))

    # Assert
    assert items == [{"display_text": TEKS_KTK_001, "error": None}]
    assert simpan.status_code == 200, simpan.text


def test_jenis_nonaktif_tanpa_case_id_ditolak_simpan_dan_pratinjau(as_role, db_session):
    # Arrange
    client = as_role(Role.CONTRIBUTOR)
    suite_id = _suite(client)
    _nonaktifkan(db_session, "uu")

    # Act
    items = _items(_pratinjau(client, rujukan_ktk_001()))
    simpan = _buat(client, suite_id, legal_refs=[rujukan_ktk_001()])

    # Assert
    assert simpan.status_code == 422
    assert simpan.json()["code"] == "REGULATION_TYPE_INACTIVE"
    assert items == [{"display_text": None, "error": simpan.json()}]


def test_jenis_nonaktif_lain_tetap_ditolak_dengan_case_id(as_role, db_session):
    """case_id hanya membebaskan kode yang sudah tersimpan, bukan semua jenis nonaktif."""
    # Arrange
    client = as_role(Role.CONTRIBUTOR)
    case_id = _buat(client, _suite(client), legal_refs=[rujukan_ktk_001()]).json()["id"]
    _nonaktifkan(db_session, "permen")
    lain = _ubah(rujukan_ktk_001(), regulation_type="permen")

    # Act
    items = _items(_pratinjau(client, lain, case_id=case_id))

    # Assert
    assert items[0]["error"]["code"] == "REGULATION_TYPE_INACTIVE"
    assert items[0]["error"]["field"] == "legal_refs[0].links[0].regulation_type"


def test_pratinjau_tidak_menulis_apa_pun(as_role, db_session):
    # Arrange
    client = as_role(Role.CONTRIBUTOR)
    case_id = _buat(client, _suite(client), legal_refs=[rujukan_ktk_001()]).json()["id"]
    sebelum = _jejak(db_session, case_id)

    # Act
    items = _items(_pratinjau(client, rujukan_ac10(), case_id=case_id))

    # Assert
    assert items[0]["display_text"] == TEKS_AC10
    assert _jejak(db_session, case_id) == sebelum


def _jejak(db_session, case_id: str) -> tuple[Any, ...]:
    """Jumlah versi dan baris rujukan, isi versi terbuka, dan waktu ubah kasus."""
    db_session.expire_all()
    kasus = db_session.get(Case, uuid.UUID(case_id))
    versi = db_session.scalar(select(func.count()).select_from(CaseVersion))
    baris = db_session.scalar(select(func.count()).select_from(LegalRef))
    return versi, baris, kasus.current_version.content, kasus.updated_at


def test_case_id_tidak_ada(as_role):
    client = as_role(Role.CONTRIBUTOR)

    respons = _pratinjau(client, rujukan_ktk_001(), case_id=str(uuid.uuid4()))

    assert respons.status_code == 404
    # Bukan 404 route yang tidak dikenal: kasusnya yang tidak ada.
    assert respons.json()["message"] == "Kasus tidak ditemukan"


def test_case_id_milik_author_lain_ditolak(as_role, buat_pengguna):
    # Arrange
    client = as_role(Role.CONTRIBUTOR)
    case_id = _buat(client, _suite(client)).json()["id"]
    _ganti(client, buat_pengguna(Role.CONTRIBUTOR, user_id=AUTHOR_LAIN))

    # Act
    respons = _pratinjau(client, rujukan_ktk_001(), case_id=case_id)

    # Assert
    assert respons.status_code == 403
    assert respons.json()["code"] == "FORBIDDEN"


def test_admin_boleh_memakai_case_id_kasus_orang_lain(as_role, buat_pengguna):
    # Arrange
    client = as_role(Role.CONTRIBUTOR)
    case_id = _buat(client, _suite(client)).json()["id"]
    _ganti(client, buat_pengguna(Role.ADMIN, user_id=ADMIN_LAIN))

    # Act
    items = _items(_pratinjau(client, rujukan_ktk_001(), case_id=case_id))

    # Assert
    assert items == [{"display_text": TEKS_KTK_001, "error": None}]


# --- peran -------------------------------------------------------------------


def test_belum_login_401(client):
    assert _pratinjau(client, rujukan_ktk_001()).status_code == 401


@pytest.mark.parametrize("role", [Role.VIEWER, Role.DEVELOPER_AI])
def test_yang_tidak_menulis_kasus_403(as_role, role):
    client = as_role(role)

    assert _pratinjau(client, rujukan_ktk_001()).status_code == 403


# --- body rusak: 422 dengan bentuk error tim ---------------------------------


@pytest.mark.parametrize(
    ("body", "code", "field"),
    [
        pytest.param({}, "FIELD_REQUIRED", "legal_refs", id="tanpa-legal-refs"),
        pytest.param({"legal_refs": "x"}, "VALIDATION_ERROR", "legal_refs", id="bukan-daftar"),
        pytest.param(
            {"legal_refs": [5]}, "VALIDATION_ERROR", "legal_refs[0]", id="item-bukan-objek"
        ),
        pytest.param(
            {"legal_refs": [], "case_id": "bukan-uuid"}, "VALIDATION_ERROR", "case_id", id="case-id"
        ),
        pytest.param(
            {"legal_refs": [rujukan_ktk_001()] * 101},
            "VALIDATION_ERROR",
            "legal_refs",
            id="maks-100",
        ),
    ],
)
def test_body_rusak_422(as_role, body, code, field):
    client = as_role(Role.CONTRIBUTOR)

    respons = client.post(PATH, json=body)

    assert respons.status_code == 422
    assert respons.json()["code"] == code
    assert respons.json()["field"] == field


def test_seratus_rujukan_masih_diterima(as_role):
    client = as_role(Role.CONTRIBUTOR)

    items = _items(_pratinjau(client, *([rujukan_ktk_001()] * 100)))

    assert len(items) == 100


# --- kontrak OpenAPI yang disalin client -------------------------------------


def test_openapi_pratinjau():
    # Arrange
    spec = app.openapi()
    skema = spec["components"]["schemas"]

    # Act
    operasi = spec["paths"][PATH]["post"]

    # Assert
    assert operasi["tags"] == ["legal-refs"]
    assert {"200", "401", "403", "404", "422"} <= set(operasi["responses"])
    body = operasi["requestBody"]["content"]["application/json"]["schema"]
    assert body == {"$ref": "#/components/schemas/LegalRefPreviewRequest"}
    permintaan = skema["LegalRefPreviewRequest"]
    assert permintaan["required"] == ["legal_refs"]
    assert permintaan["properties"]["legal_refs"]["type"] == "array"
    assert permintaan["properties"]["legal_refs"]["maxItems"] == 100
    assert permintaan["properties"]["legal_refs"]["items"]["type"] == "object"
    assert {"type": "null"} in permintaan["properties"]["case_id"]["anyOf"]
    jawaban = operasi["responses"]["200"]["content"]["application/json"]["schema"]
    assert jawaban == {"$ref": "#/components/schemas/LegalRefPreviewResponse"}
    item = skema["LegalRefPreviewItem"]
    assert set(item["required"]) == {"display_text", "error"}
    assert {"type": "null"} in item["properties"]["display_text"]["anyOf"]
    assert {"$ref": "#/components/schemas/ErrorBody"} in item["properties"]["error"]["anyOf"]
