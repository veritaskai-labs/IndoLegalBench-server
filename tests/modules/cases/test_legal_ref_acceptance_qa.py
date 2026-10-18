"""QA rujukan hukum lewat HTTP, seperti Author memakai editor (PBI-21, SCRUM-163).

Hanya skenario Jira SCRUM-163 yang belum dicakup tes SCRUM-160/162 lewat HTTP:
Tentang kosong dan sangat panjang (AC3), teks bebas apa adanya (AC7), rantai 4
peraturan dengan jis. (AC6), nomor alfanumerik, tahun berjalan (AC8), kasus lama
tanpa rujukan, dan menyimpan ulang kasus Sprint 1 (AC9).

Contoh hukum hanya KTK-001 dan contoh AC10 (dari Aileen, belum dikonfirmasi tim
legal). "5/PUU-XX/2022" berasal dari teks Jira SCRUM-163 dan hanya dipakai
sebagai input format nomor. Nilai lain (Pasal 1 UU 1/2020, ayat "1", huruf "a",
teks bebas, jenis "Jenis Uji Tidak Dikenal") adalah input format sintetis.
"""

import uuid
from typing import Any

import pytest

from app.modules.cases.models import Case
from app.modules.cases.validation import current_year_wib
from app.shared.security import Role
from tests.legal_refs import link_pasal, rujukan_ktk_001
from tests.modules.cases.test_cases import _badan, _buat, _suite
from tests.modules.cases.test_legal_ref_contract import LAMA_KTK_001, _kasus_dengan_isi_lama

PREVIEW = "/legal-refs/preview"
TENTANG_MAKS = 500


def _detail(client, case_id: str) -> dict[str, Any]:
    respons = client.get(f"/cases/{case_id}")
    assert respons.status_code == 200, respons.text
    return respons.json()


def _pratinjau(client, *rujukan: dict[str, Any]) -> list[dict[str, Any]]:
    respons = client.post(PREVIEW, json={"legal_refs": list(rujukan)})
    assert respons.status_code == 200, respons.text
    return respons.json()["items"]


def _untuk_ditulis(rujukan_terbaca: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Rujukan dari GET dikirim balik apa adanya, tanpa display_text, seperti editor."""
    return [
        {key: nilai for key, nilai in rujukan.items() if key != "display_text"}
        for rujukan in rujukan_terbaca
    ]


def _simpan_ulang(client, case_id: str) -> Any:
    """Author membuka kasus lalu menekan Simpan tanpa mengubah rujukan."""
    rujukan = _untuk_ditulis(_detail(client, case_id)["legal_refs"])
    return client.put(f"/cases/{case_id}", json=_badan(legal_refs=rujukan))


def _nama_saja(**link: Any) -> dict[str, Any]:
    return {
        "kind": "structured",
        "links": [{"form": "name_only", "connector": "none", "regulation_type": "uu", **link}],
    }


# --- AC3: Tentang opsional dan sangat panjang -----------------------------------


@pytest.mark.parametrize(
    "tentang",
    [None, "", "x" * TENTANG_MAKS],
    ids=["tidak-dikirim", "kosong", "tepat-500"],
)
def test_ac3_positif_tentang_kosong_atau_500_karakter_tersimpan(as_role, tentang):
    # Arrange
    client = as_role(Role.AUTHOR)
    tambahan = {} if tentang is None else {"about": tentang}
    rujukan = {"kind": "structured", "links": [link_pasal("156", **tambahan)]}

    # Act
    respons = _buat(client, _suite(client), legal_refs=[rujukan])

    # Assert
    assert respons.status_code == 201, respons.text
    link = respons.json()["legal_refs"][0]["links"][0]
    assert link["about"] == (tentang or None)
    assert respons.json()["legal_refs"][0]["display_text"] == "Pasal 156 UU 13/2003"


def test_ac3_negatif_tentang_501_karakter_ditolak_di_field_yang_tepat(as_role):
    # Arrange
    client = as_role(Role.AUTHOR)
    panjang = {"kind": "structured", "links": [link_pasal("1", about="x" * (TENTANG_MAKS + 1))]}

    # Act
    respons = _buat(client, _suite(client), legal_refs=[rujukan_ktk_001(), panjang])

    # Assert
    assert respons.status_code == 422
    assert respons.json()["code"] == "VALIDATION_ERROR"
    assert respons.json()["field"] == "legal_refs[1].links[0].about"


# --- AC7: teks bebas apa adanya ---------------------------------------------------


def test_ac7_positif_teks_bebas_tersimpan_apa_adanya_dan_ditandai_free_text(as_role):
    # Arrange
    client = as_role(Role.AUTHOR)
    isi = "Baris pertama format uji.\nBaris kedua  dua spasi; (kurung), titik."
    teks_bebas = {"kind": "free_text", "links": [], "free_text": f"  {isi}  "}

    # Act
    dibuat = _buat(client, _suite(client), legal_refs=[rujukan_ktk_001(), teks_bebas])
    terbaca = _detail(client, dibuat.json()["id"])["legal_refs"]

    # Assert
    assert dibuat.status_code == 201, dibuat.text
    assert terbaca[1] == {"kind": "free_text", "free_text": isi, "links": [], "display_text": isi}
    assert terbaca[0]["kind"] == "structured"


# --- AC6: rantai 4 peraturan dengan jis. ------------------------------------------


def test_ac6_edge_rantai_empat_peraturan_dengan_jis_tersimpan_berurutan(as_role):
    # Arrange
    client = as_role(Role.AUTHOR)
    links = [link_pasal("1", number="1", year=2020)] + [
        {**link_pasal(str(i), number=str(i), year=2019 + i), "connector": "jis"}
        for i in range(2, 5)
    ]
    rantai = {"kind": "structured", "links": links}
    harapan = (
        "Pasal 1 UU 1/2020 jis. Pasal 2 UU 2/2021 jis. Pasal 3 UU 3/2022 jis. Pasal 4 UU 4/2023"
    )

    # Act
    dibuat = _buat(client, _suite(client), legal_refs=[rantai])
    terbaca = _detail(client, dibuat.json()["id"])["legal_refs"][0]
    pratinjau = _pratinjau(client, rantai)

    # Assert
    assert dibuat.status_code == 201, dibuat.text
    assert [link["number"] for link in terbaca["links"]] == ["1", "2", "3", "4"]
    assert [link["connector"] for link in terbaca["links"]] == ["none", "jis", "jis", "jis"]
    assert terbaca["display_text"] == harapan
    assert pratinjau == [{"display_text": harapan, "error": None}]


# --- nomor alfanumerik --------------------------------------------------------------


def test_edge_nomor_alfanumerik_dari_jira_tidak_mengulang_tahun(as_role):
    """Input format dari teks Jira, bukan contoh tim legal (aturan display 3)."""
    # Arrange
    client = as_role(Role.AUTHOR)
    rujukan = _nama_saja(number="5/PUU-XX/2022", year=2022)

    # Act
    dibuat = _buat(client, _suite(client), legal_refs=[rujukan])
    pratinjau = _pratinjau(client, rujukan)

    # Assert
    assert dibuat.status_code == 201, dibuat.text
    assert dibuat.json()["legal_refs"][0]["links"][0]["number"] == "5/PUU-XX/2022"
    assert dibuat.json()["legal_refs"][0]["display_text"] == "UU 5/PUU-XX/2022"
    assert pratinjau == [{"display_text": "UU 5/PUU-XX/2022", "error": None}]


# --- AC8: tahun berjalan -----------------------------------------------------------


def test_ac8_edge_tahun_sama_dengan_tahun_berjalan_diterima(as_role):
    """Memakai jam sungguhan (WIB), tanpa monkeypatch: begini yang dialami Author."""
    # Arrange
    client = as_role(Role.AUTHOR)
    tahun = current_year_wib()
    rujukan = {"kind": "structured", "links": [link_pasal("1", number="1", year=tahun)]}

    # Act
    dibuat = _buat(client, _suite(client), legal_refs=[rujukan])
    pratinjau = _pratinjau(client, rujukan)

    # Assert
    assert dibuat.status_code == 201, dibuat.text
    assert pratinjau == [{"display_text": f"Pasal 1 UU 1/{tahun}", "error": None}]


# --- AC9: kasus lama ----------------------------------------------------------------


@pytest.mark.parametrize("hapus_key", [True, False], ids=["tanpa-key", "daftar-kosong"])
def test_ac9_edge_kasus_lama_tanpa_rujukan_tetap_bisa_dibuka(as_role, db_session, hapus_key):
    # Arrange
    client = as_role(Role.AUTHOR)
    case_id = _buat(client, _suite(client)).json()["id"]
    versi = db_session.get(Case, uuid.UUID(case_id)).current_version
    isi = {key: nilai for key, nilai in versi.content.items() if key != "legal_refs"}
    versi.content = isi if hapus_key else {**isi, "legal_refs": []}
    db_session.commit()

    # Act
    detail = client.get(f"/cases/{case_id}")
    draf = client.get(f"/cases/{case_id}/versions/current")
    kelengkapan = client.get(f"/cases/{case_id}/completeness").json()

    # Assert
    assert detail.status_code == 200, detail.text
    assert draf.status_code == 200, draf.text
    assert detail.json()["legal_refs"] == []
    assert kelengkapan["legal_ref_count"] == 0
    assert "legal_refs" in [item["field"] for item in kelengkapan["missing"]]


def test_ac9_positif_ktk_001_sprint_1_bisa_disimpan_ulang_tanpa_ditulis_ulang(as_role, db_session):
    # Arrange
    client = as_role(Role.AUTHOR)
    case_id = _kasus_dengan_isi_lama(client, db_session, LAMA_KTK_001)
    sebelum = _detail(client, case_id)["legal_refs"]

    # Act
    respons = _simpan_ulang(client, case_id)

    # Assert
    assert respons.status_code == 200, respons.text
    assert _detail(client, case_id)["legal_refs"] == sebelum
    assert sebelum[0]["display_text"] == "Pasal 156 UU 13/2003"
    tersimpan = db_session.get(Case, uuid.UUID(case_id)).current_version.content["legal_refs"]
    assert tersimpan[0]["kind"] == "structured"


@pytest.mark.parametrize(
    ("lama", "kind", "teks"),
    [
        (
            {**LAMA_KTK_001, "ayat": "1", "huruf": "a"},
            "structured",
            "Pasal 156 ayat (1) huruf a UU 13/2003",
        ),
        (
            {
                "regulation_type": "Jenis Uji Tidak Dikenal",
                "regulation_number": "1",
                "year": 2020,
                "pasal": "1",
            },
            "free_text",
            None,
        ),
    ],
    ids=["ayat-huruf-lama", "jenis-lama-tidak-dikenal"],
)
def test_ac9_edge_varian_sprint_1_bisa_disimpan_ulang_dan_isinya_tetap(
    as_role, db_session, lama, kind, teks
):
    # Arrange
    client = as_role(Role.AUTHOR)
    case_id = _kasus_dengan_isi_lama(client, db_session, LAMA_KTK_001, lama)
    sebelum = _detail(client, case_id)["legal_refs"]

    # Act
    respons = _simpan_ulang(client, case_id)

    # Assert
    assert respons.status_code == 200, respons.text
    assert _detail(client, case_id)["legal_refs"] == sebelum
    assert sebelum[1]["kind"] == kind
    if teks is not None:
        assert sebelum[1]["display_text"] == teks


def test_ac9_negatif_rujukan_lama_tanpa_tahun_harus_dilengkapi_sebelum_disimpan(
    as_role, db_session
):
    """Keputusan PO 17 Okt (Q2): tahun lama yang kosong wajib diisi saat disimpan ulang."""
    # Arrange
    client = as_role(Role.AUTHOR)
    case_id = _kasus_dengan_isi_lama(client, db_session, {**LAMA_KTK_001, "year": None})

    # Act
    respons = _simpan_ulang(client, case_id)

    # Assert
    assert respons.status_code == 422
    assert respons.json()["code"] == "FIELD_REQUIRED"
    assert respons.json()["field"] == "legal_refs[0].links[0].year"


def test_ac9_positif_rujukan_lama_tersimpan_setelah_tahun_dilengkapi(as_role, db_session):
    """Jawaban PO 18 Okt: Author hanya melengkapi tahun, field lain tidak diketik ulang."""
    # Arrange
    client = as_role(Role.AUTHOR)
    case_id = _kasus_dengan_isi_lama(client, db_session, {**LAMA_KTK_001, "year": None})
    rujukan = _untuk_ditulis(_detail(client, case_id)["legal_refs"])
    rujukan[0]["links"][0]["year"] = 2003

    # Act
    respons = client.put(f"/cases/{case_id}", json=_badan(legal_refs=rujukan))

    # Assert
    assert respons.status_code == 200, respons.text
    assert _detail(client, case_id)["legal_refs"][0]["display_text"] == "Pasal 156 UU 13/2003"
