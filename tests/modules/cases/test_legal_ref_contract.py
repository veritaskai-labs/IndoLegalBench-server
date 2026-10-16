"""Kontrak API rujukan hukum lewat HTTP (PBI-21, SCRUM-160).

Mengunci apa yang dibaca frontend (SCRUM-161/162): bentuk legal_refs di
request dan response kasus, jalur field 422 `legal_refs[i].links[j].x` (AC8),
dan bentuk OpenAPI yang diturunkan client menjadi tipe dan zod.

Contoh hukum hanya KTK-001 dan contoh AC10 (dari Aileen, belum dikonfirmasi
tim legal). Teks bebas dan nilai rusak adalah input format sintetis.
"""

import json
import uuid
from typing import Any

import pytest
from sqlalchemy import update

from app.modules.cases import service as cases_service
from app.modules.cases import validation
from app.modules.cases.models import Case
from app.modules.cases.validation import ANGKA_PATTERN, PASAL_PATTERN
from app.modules.legal_refs.models import RegulationType
from app.shared.security import Role
from tests.legal_refs import rujukan_ac10, rujukan_ktk_001
from tests.modules.cases.test_cases import _badan, _buat, _suite

# Kata kunci JSON Schema yang diterima scripts/gen-zod.mjs di repo client.
# Kata kunci lain membuat `npm run gen:zod` gagal, jadi kontrak tidak boleh memakainya.
KATA_KUNCI_GEN_ZOD = {
    "type",
    "properties",
    "required",
    "items",
    "enum",
    "anyOf",
    "$ref",
    "minLength",
    "maxLength",
    "pattern",
    "minimum",
    "maximum",
    "minItems",
    "title",
    "description",
}

TEKS_BEBAS = {"kind": "free_text", "links": [], "free_text": "Format teks bebas"}


def _link_terbaca(link: dict[str, Any]) -> dict[str, Any]:
    """Link seperti dikembalikan server: semua field opsional hadir, kosong = None."""
    kosong = {"about": None, "pasal": None, "angka": None, "ayat": None, "huruf": None}
    return {**kosong, **link}


def _rujukan_terbaca(rujukan: dict[str, Any], teks: str) -> dict[str, Any]:
    """Rujukan seperti dikembalikan server, dengan display_text yang dibentuk server."""
    return {
        "kind": rujukan["kind"],
        "free_text": rujukan.get("free_text"),
        "links": [_link_terbaca(link) for link in rujukan["links"]],
        "display_text": teks,
    }


TEKS_AC10 = "Pasal 156 UU 13/2003 jo. Pasal II Angka 5 UU 6/2023"
TEKS_KTK_001 = "Pasal 156 UU 13/2003"


# --- positif -----------------------------------------------------------------


def test_buat_kasus_dengan_contoh_ac10_mengembalikan_bentuk_baru(as_role):
    client = as_role(Role.AUTHOR)

    respons = _buat(client, _suite(client), legal_refs=[rujukan_ac10()])

    assert respons.status_code == 201, respons.text
    assert respons.json()["legal_refs"] == [_rujukan_terbaca(rujukan_ac10(), TEKS_AC10)]


def test_lebih_dari_satu_rujukan_tersimpan_berurutan(as_role):
    """AC1: KTK-001, contoh AC10, lalu teks bebas (AC7) dalam satu kasus."""
    client = as_role(Role.AUTHOR)
    daftar = [rujukan_ktk_001(), rujukan_ac10(), TEKS_BEBAS]

    case_id = _buat(client, _suite(client), legal_refs=daftar).json()["id"]
    detail = client.get(f"/cases/{case_id}").json()
    kelengkapan = client.get(f"/cases/{case_id}/completeness").json()

    assert detail["legal_refs"] == [
        _rujukan_terbaca(rujukan_ktk_001(), TEKS_KTK_001),
        _rujukan_terbaca(rujukan_ac10(), TEKS_AC10),
        _rujukan_terbaca(TEKS_BEBAS, "Format teks bebas"),
    ]
    assert kelengkapan["legal_ref_count"] == 3


def test_ubah_kasus_mengganti_rujukan(as_role):
    client = as_role(Role.AUTHOR)
    suite_id = _suite(client)
    case_id = _buat(client, suite_id, legal_refs=[rujukan_ac10()]).json()["id"]
    badan = _badan(legal_refs=[rujukan_ktk_001()])

    respons = client.put(f"/cases/{case_id}", json=badan)

    assert respons.status_code == 200, respons.text
    assert respons.json()["legal_refs"] == [_rujukan_terbaca(rujukan_ktk_001(), TEKS_KTK_001)]


def test_display_text_dari_client_tidak_disimpan(as_role):
    client = as_role(Role.AUTHOR)
    rujukan = {**rujukan_ktk_001(), "display_text": "teks dari client"}

    respons = _buat(client, _suite(client), legal_refs=[rujukan])

    assert respons.status_code == 201
    assert "teks dari client" not in respons.text


# --- negatif: jalur field 422 (AC8) ------------------------------------------


def _rusak(ubah) -> list[dict[str, Any]]:
    """[KTK-001, AC10] dengan rujukan kedua (indeks 1) diubah."""
    daftar = [rujukan_ktk_001(), rujukan_ac10()]
    ubah(daftar[1])
    return daftar


@pytest.mark.parametrize(
    ("ubah", "code", "field"),
    [
        # Contoh jalur di Jira SCRUM-160.
        (lambda r: r["links"][0].pop("pasal"), "FIELD_REQUIRED", "legal_refs[1].links[0].pasal"),
        (lambda r: r["links"][1].pop("angka"), "FIELD_REQUIRED", "legal_refs[1].links[1].angka"),
        (
            lambda r: r["links"][1].update(pasal=" "),
            "FIELD_REQUIRED",
            "legal_refs[1].links[1].pasal",
        ),
        (lambda r: r["links"][0].pop("year"), "FIELD_REQUIRED", "legal_refs[1].links[0].year"),
        (
            lambda r: r["links"][0].update(year=None),
            "FIELD_REQUIRED",
            "legal_refs[1].links[0].year",
        ),
        (
            lambda r: r["links"][1].update(number=" "),
            "FIELD_REQUIRED",
            "legal_refs[1].links[1].number",
        ),
        (lambda r: r.update(links=[]), "FIELD_REQUIRED", "legal_refs[1].links"),
        (lambda r: r.pop("kind"), "FIELD_REQUIRED", "legal_refs[1].kind"),
        (
            lambda r: r["links"][1].update(pasal="IIII"),
            "VALIDATION_ERROR",
            "legal_refs[1].links[1].pasal",
        ),
        (
            lambda r: r["links"][0].update(form="free_text"),
            "VALIDATION_ERROR",
            "legal_refs[1].links[0].form",
        ),
        (
            lambda r: r["links"][1].update(connector="jo."),
            "VALIDATION_ERROR",
            "legal_refs[1].links[1].connector",
        ),
    ],
    ids=[
        "pasal-tanpa-pasal",
        "pasal_angka-tanpa-angka",
        "pasal_angka-pasal-spasi",
        "tahun-hilang",
        "tahun-null",
        "nomor-spasi",
        "structured-tanpa-link",
        "tanpa-kind",
        "romawi-tidak-sah",
        "bentuk-link-salah",
        "penghubung-pakai-titik",
    ],
)
def test_penolakan_menunjuk_rujukan_dan_link_yang_tepat(as_role, ubah, code, field):
    client = as_role(Role.AUTHOR)

    respons = _buat(client, _suite(client), legal_refs=_rusak(ubah))

    assert respons.status_code == 422
    assert respons.json()["code"] == code
    assert respons.json()["field"] == field


@pytest.mark.parametrize("isi", [None, "", "   "])
def test_teks_bebas_kosong_ditolak(as_role, isi):
    """AC7: bentuk Teks Bebas wajib berisi."""
    client = as_role(Role.AUTHOR)

    respons = _buat(client, _suite(client), legal_refs=[{**TEKS_BEBAS, "free_text": isi}])

    assert respons.status_code == 422
    assert respons.json()["code"] == "FIELD_REQUIRED"
    assert respons.json()["field"] == "legal_refs[0].free_text"


def test_bentuk_sprint_1_ditolak_sebagai_perubahan_kontrak(as_role):
    """Breaking change: client lama yang masih mengirim regulation_number dapat 422, bukan 500."""
    client = as_role(Role.AUTHOR)
    lama = {"regulation_type": "uu", "regulation_number": "13", "year": 2003, "pasal": "156"}

    respons = _buat(client, _suite(client), legal_refs=[lama])

    assert respons.status_code == 422
    assert respons.json()["code"] == "FIELD_REQUIRED"
    assert respons.json()["field"] == "legal_refs[0].kind"


def test_ubah_dengan_link_rusak_juga_menunjuk_field(as_role):
    """PUT memakai pemetaan error yang sama dengan POST."""
    client = as_role(Role.AUTHOR)
    case_id = _buat(client, _suite(client), legal_refs=[rujukan_ktk_001()]).json()["id"]
    badan = _badan(legal_refs=_rusak(lambda r: r["links"][1].pop("angka")))

    respons = client.put(f"/cases/{case_id}", json=badan)

    assert respons.status_code == 422
    assert respons.json()["field"] == "legal_refs[1].links[1].angka"


# --- kontrak OpenAPI -----------------------------------------------------------


@pytest.fixture
def spec(client) -> dict[str, Any]:
    return client.get("/openapi.json").json()


def _skema(spec: dict[str, Any], nama: str) -> dict[str, Any]:
    return spec["components"]["schemas"][nama]


def _ref_item(properti: dict[str, Any]) -> str:
    return properti["items"]["$ref"].rsplit("/", 1)[-1]


def _cabang_teks(properti: dict[str, Any]) -> dict[str, Any]:
    """Cabang string dari field opsional (anyOf [string, null])."""
    return next(cabang for cabang in properti["anyOf"] if cabang.get("type") == "string")


def test_case_write_memakai_rujukan_baru(spec):
    tulis = _skema(spec, "CaseWrite")

    assert _ref_item(tulis["properties"]["legal_refs"]) == "LegalRefWrite"
    assert tulis["properties"]["legal_refs"]["minItems"] == 1


def test_rujukan_tulis_tanpa_display_text(spec):
    rujukan = _skema(spec, "LegalRefWrite")

    assert set(rujukan["required"]) == {"kind", "links"}
    assert rujukan["properties"]["kind"]["enum"] == ["structured", "free_text"]
    assert _ref_item(rujukan["properties"]["links"]) == "LegalRefLinkWrite"
    assert "display_text" not in rujukan["properties"]
    assert "display_text" not in _skema(spec, "LegalRefLinkWrite")["properties"]


def test_link_tulis_membawa_aturan_untuk_zod(spec):
    link = _skema(spec, "LegalRefLinkWrite")["properties"]

    assert link["form"]["enum"] == ["name_only", "pasal", "pasal_angka"]
    assert link["connector"]["enum"] == ["none", "jo", "jis"]
    assert _cabang_teks(link["pasal"])["pattern"] == PASAL_PATTERN
    assert _cabang_teks(link["angka"])["pattern"] == ANGKA_PATTERN
    assert link["year"]["type"] == "integer"
    assert link["regulation_type"]["maxLength"] == 150


def test_response_kasus_dan_salinan_review_memakai_rujukan_baca(spec):
    assert _ref_item(_skema(spec, "CaseRead")["properties"]["legal_refs"]) == "LegalRefRead"
    assert _ref_item(_skema(spec, "ReviewCopy")["properties"]["legal_refs"]) == "LegalRefRead"
    tahun = _skema(spec, "LegalRefLinkRead")["properties"]["year"]
    assert {"type": "null"} in tahun["anyOf"]


def test_rujukan_baca_selalu_membawa_display_text(spec):
    """Satu penulisan dari server untuk editor, review, dan riwayat (keputusan 16 Okt)."""
    baca = _skema(spec, "LegalRefRead")

    assert baca["properties"]["display_text"]["type"] == "string"
    assert "display_text" in baca["required"]


def test_nama_field_sprint_1_hilang_dari_kontrak(spec):
    teks = json.dumps(spec)

    assert "regulation_number" not in teks
    assert '"LegalRef"' not in teks


def _kata_kunci(spec: dict[str, Any], nama: str, sudah: set[str]) -> set[str]:
    """Semua kata kunci di skema `nama` dan skema yang dirujuknya."""
    if nama in sudah:
        return set()
    sudah.add(nama)
    terkumpul: set[str] = set()

    def jalan(node: Any) -> None:
        if isinstance(node, dict):
            for kunci, nilai in node.items():
                terkumpul.add(kunci)
                if kunci == "$ref":
                    terkumpul.update(_kata_kunci(spec, nilai.rsplit("/", 1)[-1], sudah))
                elif kunci == "properties":
                    for anak in nilai.values():
                        jalan(anak)
                else:
                    jalan(nilai)
        elif isinstance(node, list):
            for anak in node:
                jalan(anak)

    jalan(_skema(spec, nama))
    return terkumpul


def test_case_write_hanya_memakai_kata_kunci_gen_zod(spec):
    """Tanpa ini, oneOf/const/default yang lolos ke kontrak baru ketahuan saat npm run gen:zod."""
    dipakai = _kata_kunci(spec, "CaseWrite", set())

    assert dipakai - KATA_KUNCI_GEN_ZOD == set()


def test_field_terlarang_lewat_http_memakai_kode_sendiri(as_role):
    """Kode baru FIELD_NOT_ALLOWED (keputusan PO 17 Okt) sampai ke badan error FE."""
    client = as_role(Role.AUTHOR)
    rujukan = rujukan_ac10()
    rujukan["links"][0]["angka"] = "1"

    respons = _buat(client, _suite(client), legal_refs=[rujukan])

    assert respons.status_code == 422
    assert respons.json()["code"] == "FIELD_NOT_ALLOWED"
    assert respons.json()["field"] == "legal_refs[0].links[0].angka"


def test_rantai_tanpa_penghubung_lewat_http(as_role):
    client = as_role(Role.AUTHOR)
    rujukan = rujukan_ac10()
    rujukan["links"][1]["connector"] = "none"

    respons = _buat(client, _suite(client), legal_refs=[rujukan])

    assert respons.status_code == 422
    assert respons.json()["code"] == "CONNECTOR_INVALID"
    assert respons.json()["field"] == "legal_refs[0].links[1].connector"


def test_tahun_di_masa_depan_lewat_http(as_role, monkeypatch):
    monkeypatch.setattr(validation, "current_year_wib", lambda now=None: 2026)
    client = as_role(Role.AUTHOR)
    rujukan = rujukan_ktk_001()
    rujukan["links"][0]["year"] = 2027

    respons = _buat(client, _suite(client), legal_refs=[rujukan])

    assert respons.status_code == 422
    assert respons.json()["code"] == "YEAR_IN_FUTURE"
    assert respons.json()["field"] == "legal_refs[0].links[0].year"


# --- jenis peraturan ada di daftar (AC2) -------------------------------------


def _nonaktifkan(db_session, code: str) -> None:
    db_session.execute(
        update(RegulationType).where(RegulationType.code == code).values(is_active=False)
    )
    db_session.commit()


def _dengan_jenis(code: str) -> dict:
    rujukan = rujukan_ktk_001()
    rujukan["links"][0]["regulation_type"] = code
    return rujukan


def test_jenis_di_luar_daftar_ditolak_lewat_http(as_role):
    client = as_role(Role.AUTHOR)

    respons = _buat(client, _suite(client), legal_refs=[_dengan_jenis("kode_lain")])

    assert respons.status_code == 422
    assert respons.json()["code"] == "REGULATION_TYPE_UNKNOWN"
    assert respons.json()["field"] == "legal_refs[0].links[0].regulation_type"


def test_kasus_baru_tidak_boleh_memakai_jenis_nonaktif(as_role, db_session):
    client = as_role(Role.AUTHOR)
    _nonaktifkan(db_session, "uu")

    respons = _buat(client, _suite(client), legal_refs=[rujukan_ktk_001()])

    assert respons.status_code == 422
    assert respons.json()["code"] == "REGULATION_TYPE_INACTIVE"


def test_jenis_yang_dinonaktifkan_setelah_dipakai_tetap_bisa_disimpan_ulang(as_role, db_session):
    client = as_role(Role.AUTHOR)
    case_id = _buat(client, _suite(client), legal_refs=[rujukan_ktk_001()]).json()["id"]
    _nonaktifkan(db_session, "uu")

    respons = client.put(f"/cases/{case_id}", json=_badan(legal_refs=[rujukan_ktk_001()]))

    assert respons.status_code == 200, respons.text


def test_jenis_nonaktif_lain_tetap_ditolak_saat_mengubah(as_role, db_session):
    client = as_role(Role.AUTHOR)
    case_id = _buat(client, _suite(client), legal_refs=[rujukan_ktk_001()]).json()["id"]
    _nonaktifkan(db_session, "permen")

    respons = client.put(
        f"/cases/{case_id}", json=_badan(legal_refs=[rujukan_ktk_001(), _dengan_jenis("permen")])
    )

    assert respons.status_code == 422
    assert respons.json()["code"] == "REGULATION_TYPE_INACTIVE"
    assert respons.json()["field"] == "legal_refs[1].links[0].regulation_type"


def test_jenis_nonaktif_dari_rujukan_sprint_1_ikut_dianggap_tersimpan(as_role, db_session):
    """Versi lama menyimpan teks "UU"; setelah dibaca ulang itu code uu (AC9)."""
    client = as_role(Role.AUTHOR)
    case_id = _buat(client, _suite(client), legal_refs=[rujukan_ktk_001()]).json()["id"]
    versi = db_session.get(Case, uuid.UUID(case_id)).current_version
    lama = {"regulation_type": "UU", "regulation_number": "13", "year": 2003, "pasal": "156"}
    versi.content = {**versi.content, "legal_refs": [lama]}
    db_session.commit()
    _nonaktifkan(db_session, "uu")

    respons = client.put(f"/cases/{case_id}", json=_badan(legal_refs=[rujukan_ktk_001()]))

    assert respons.status_code == 200, respons.text


# --- bacaan rujukan Sprint 1 dan display_text (AC6, AC9) ----------------------

LAMA_KTK_001 = {"regulation_type": "uu", "regulation_number": "13", "year": 2003, "pasal": "156"}


def _kasus_dengan_isi_lama(client, db_session, *rujukan_lama: dict, kode: str = "PHK-001") -> str:
    """Kasus yang content-nya masih bentuk Sprint 1, seperti versi sebelum PBI-21."""
    case_id = _buat(client, _suite(client, f"Suite {kode}"), case_code=kode).json()["id"]
    versi = db_session.get(Case, uuid.UUID(case_id)).current_version
    versi.content = {**versi.content, "legal_refs": list(rujukan_lama)}
    db_session.commit()
    return case_id


def test_ktk_001_sprint_1_tampil_sama_dalam_bentuk_baru(as_role, db_session):
    """AC9: rujukan lama tampil benar dan siap diedit tanpa ditulis ulang."""
    client = as_role(Role.AUTHOR)
    case_id = _kasus_dengan_isi_lama(client, db_session, LAMA_KTK_001)

    detail = client.get(f"/cases/{case_id}").json()
    kelengkapan = client.get(f"/cases/{case_id}/completeness").json()

    assert detail["legal_refs"] == [_rujukan_terbaca(rujukan_ktk_001(), TEKS_KTK_001)]
    assert kelengkapan["legal_ref_count"] == 1
    assert detail["completeness_pct"] == 100


def test_rujukan_lama_tanpa_tahun_tampil_tapi_belum_lengkap(as_role, db_session):
    """Keputusan PO 17 Okt: rujukan lama tanpa tahun dianggap belum lengkap."""
    client = as_role(Role.AUTHOR)
    case_id = _kasus_dengan_isi_lama(client, db_session, {**LAMA_KTK_001, "year": None})

    detail = client.get(f"/cases/{case_id}").json()
    kelengkapan = client.get(f"/cases/{case_id}/completeness").json()
    daftar = client.get(f"/suites/{detail['suite_id']}/cases").json()

    assert detail["legal_refs"][0]["display_text"] == "Pasal 156 UU 13"
    assert detail["legal_refs"][0]["links"][0]["year"] is None
    assert kelengkapan["legal_ref_count"] == 0
    assert "legal_refs" in [item["field"] for item in kelengkapan["missing"]]
    assert daftar[0]["completeness_pct"] < 100


def test_pengajuan_review_membaca_rujukan_lama(as_role, db_session):
    """Guard pengajuan memakai bacaan yang sama: rujukan lama lengkap tidak menghalangi."""
    client = as_role(Role.AUTHOR)
    lengkap = _kasus_dengan_isi_lama(client, db_session, LAMA_KTK_001)
    tanpa_tahun = _kasus_dengan_isi_lama(
        client, db_session, {**LAMA_KTK_001, "year": None}, kode="PHK-002"
    )

    diterima = client.post(f"/cases/{lengkap}/submit-review")
    ditolak = client.post(f"/cases/{tanpa_tahun}/submit-review")

    assert diterima.status_code == 200, diterima.text
    assert ditolak.status_code == 422
    assert "legal_refs" in [item["field"] for item in ditolak.json()["missing"]]


def test_salinan_review_membawa_display_text(as_role, db_session):
    client = as_role(Role.AUTHOR)
    case_id = _buat(client, _suite(client), legal_refs=[rujukan_ac10()]).json()["id"]
    versi_id = db_session.get(Case, uuid.UUID(case_id)).current_version_id

    salinan = cases_service.review_copies(db_session, {versi_id})[versi_id]

    assert salinan.legal_refs[0].display_text == TEKS_AC10


def test_draf_terbuka_membawa_display_text(as_role, db_session):
    """GET versi yang sedang dikerjakan memakai pembaca yang sama dengan detail."""
    client = as_role(Role.AUTHOR)
    case_id = _kasus_dengan_isi_lama(client, db_session, LAMA_KTK_001)

    respons = client.get(f"/cases/{case_id}/versions/current")

    assert respons.status_code == 200, respons.text
    assert respons.json()["legal_refs"][0]["display_text"] == TEKS_KTK_001
