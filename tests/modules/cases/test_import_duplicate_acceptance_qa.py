"""Uji penerimaan impor CSV, duplikasi, dan pencarian kasus PBI-4. Sub task QA SCRUM-342.

Tes SCRUM-337/338/339 sudah menguji aturannya satu per satu. Berkas ini hanya menambah skenario
SCRUM-342 yang belum tercakup, semuanya lewat API seperti dipakai halaman impor:

- Negatif: ringkasan impor tertutup untuk Viewer dan Developer AI; tanpa login 401 di ketiga
  endpoint impor
- Batas: tepat 500 baris diterima dan 501 ditolak lewat endpoint, bukan hanya di parser
- Edge: berkas ekspor Google Sheets apa adanya (tanpa BOM, CRLF, sel berkutip dengan baris baru,
  koma, dan huruf non-ASCII) tersimpan dan terbaca kembali sama persis
- Edge: kasus hasil impor tanpa tag tidak terlihat oleh Developer AI
- AC2: duplikat dari kasus hasil impor adalah draft baru tanpa tag, kasus asal tidak berubah
- AC3: kasus hasil impor ditemukan lewat kode IMPOR-, judul, dan filter suite sementara
- OWASP A03: isi sel berbentuk formula disimpan sebagai teks biasa

Skenario lain di SCRUM-342 sudah dijawab tes yang ada; tabel jejaknya ada di deskripsi PR.
Data uji sintetis saja (L1, L3), bukan kasus Veritask.
"""

import csv
import io
import json
import uuid

import pytest

from app.modules.cases.csv_import import TEMPLATE_COLUMNS
from app.modules.cases.models import Case
from app.shared.security import Role
from tests.legal_refs import rujukan_ktk_001

# AC4 PBI-4: sekitar 500 kasus per berkas. Ditulis sebagai angka, bukan MAX_ROWS dari kode,
# supaya tes gagal bila batas di kode bergeser dari yang disepakati klien.
BATAS_BARIS = 500


@pytest.fixture
def pengimpor(as_role):
    return as_role(Role.CONTRIBUTOR)


def _baris(**isi: str) -> list[str]:
    return [isi.get(kolom, "") for kolom in TEMPLATE_COLUMNS]


def _berkas(*baris: list[str]) -> bytes:
    keluaran = io.StringIO()
    penulis = csv.writer(keluaran, lineterminator="\r\n")
    penulis.writerow(TEMPLATE_COLUMNS)
    penulis.writerows(baris)
    return keluaran.getvalue().encode("utf-8")


def _impor(client, data: bytes, nama: str = "uji-qa.csv"):
    return client.post("/case-imports", files={"file": (nama, data, "text/csv")})


def _lengkap(judul: str, pertanyaan: str, tag: str = "test") -> list[str]:
    return _baris(
        title=judul,
        question=pertanyaan,
        category="uji",
        split_tag=tag,
        legal_refs=json.dumps(["Rujukan uji", rujukan_ktk_001()]),
        must_contain='["kata uji"]',
        expected_conclusion="Kesimpulan uji",
    )


# --- Akses (OWASP A01) ------------------------------------------------------------------------


@pytest.mark.parametrize("peran", [Role.VIEWER, Role.DEVELOPER_AI])
def test_ringkasan_impor_tertutup_untuk_viewer_dan_developer_ai(as_role, peran):
    """Negatif: hanya Kontributor dan Admin yang mengimpor, jadi peran lain tidak boleh membuka
    ringkasan batch siapa pun, walau tahu id-nya."""
    # Arrange
    batch_id = _impor(as_role(Role.CONTRIBUTOR), _berkas(_lengkap("Judul uji 1", "Tanya 1")))
    batch_id = batch_id.json()["batch_id"]
    lain = as_role(peran, user_id=uuid.uuid4(), email="lain@veritask.test")

    # Act
    respons = lain.get(f"/case-imports/{batch_id}")

    # Assert
    assert respons.status_code == 403


def test_tanpa_login_ketiga_endpoint_impor_ditolak(client):
    """Negatif: tanpa sesi, template, unggahan, dan ringkasan semuanya 401."""
    # Act
    respons = [
        client.get("/case-imports/template"),
        _impor(client, _berkas(_lengkap("Judul uji 1", "Tanya 1"))),
        client.get(f"/case-imports/{uuid.uuid4()}"),
    ]

    # Assert
    assert [r.status_code for r in respons] == [401, 401, 401]


# --- Batas jumlah baris -----------------------------------------------------------------------


def test_tepat_500_baris_diterima_lewat_endpoint(pengimpor):
    """Batas: AC4, berkas dengan tepat 500 kasus diterima utuh dan semuanya tersimpan."""
    # Arrange
    data = _berkas(*[_lengkap(f"Judul uji {i}", f"Tanya {i}") for i in range(BATAS_BARIS)])

    # Act
    respons = _impor(pengimpor, data)

    # Assert
    assert respons.status_code == 201, respons.text
    ringkasan = respons.json()
    assert (ringkasan["total_rows"], ringkasan["success_count"]) == (BATAS_BARIS, BATAS_BARIS)
    assert (ringkasan["failed_count"], ringkasan["skipped_count"]) == (0, 0)


def test_501_baris_ditolak_lewat_endpoint_tanpa_menyimpan(pengimpor, db_session):
    """Batas: satu baris lewat dari batas menolak seluruh berkas, tidak ada kasus yang dibuat."""
    # Arrange
    data = _berkas(*[_lengkap(f"Judul uji {i}", f"Tanya {i}") for i in range(BATAS_BARIS + 1)])

    # Act
    respons = _impor(pengimpor, data)

    # Assert
    assert (respons.status_code, respons.json()["code"]) == (422, "IMPORT_TOO_MANY_ROWS")
    assert db_session.query(Case).count() == 0


# --- Berkas dari Google Sheets ----------------------------------------------------------------


def test_ekspor_google_sheets_apa_adanya_terbaca_sama_persis(pengimpor):
    """Edge: Google Sheets mengekspor UTF-8 tanpa BOM dengan CRLF, dan mengutip sel yang berisi
    koma, tanda kutip, atau baris baru. Isi sel harus kembali persis seperti diketik."""
    # Arrange
    pertanyaan = 'Baris pertama, dengan koma.\nBaris kedua "berkutip" – é ü'
    data = (
        ",".join(TEMPLATE_COLUMNS)
        + "\r\n"
        + 'Judul uji Sheets,"'
        + pertanyaan.replace('"', '""')
        + '",uji,dev,"[""Rujukan uji""]","[""kata uji""]",[],Kesimpulan uji\r\n'
    ).encode("utf-8")

    # Act
    ringkasan = _impor(pengimpor, data).json()
    (baris,) = ringkasan["rows"]
    kasus = pengimpor.get(f"/cases/{baris['case_id']}").json()

    # Assert
    assert baris["status"] == "created", baris
    assert kasus["identity"]["question"] == pertanyaan
    assert kasus["split_tag"] == "dev"
    assert kasus["answer_criteria"]["must_contain"] == ["kata uji"]


# --- Developer AI dan kasus tanpa tag ---------------------------------------------------------


def test_kasus_impor_tanpa_tag_tidak_terlihat_developer_ai(as_role):
    """Negatif: D9 aturan 4 berlaku juga untuk kasus hasil impor yang tagnya belum dipilih."""
    # Arrange
    ringkasan = _impor(
        as_role(Role.CONTRIBUTOR), _berkas(_lengkap("Judul uji", "Tanya", ""))
    ).json()
    case_id = ringkasan["rows"][0]["case_id"]
    dev_ai = as_role(Role.DEVELOPER_AI, user_id=uuid.uuid4(), email="devai@veritask.test")

    # Act
    detail = dev_ai.get(f"/cases/{case_id}")
    daftar = dev_ai.get(f"/suites/{ringkasan['suite_id']}/cases")

    # Assert
    assert detail.status_code == 404
    assert daftar.json() == []


# --- AC2 duplikat dari kasus hasil impor ------------------------------------------------------


def test_duplikat_kasus_impor_adalah_draft_baru_tanpa_tag(pengimpor):
    """Positif: AC2, kasus hasil impor bisa diduplikasi; salinan draft tanpa tag, asal tidak
    berubah dan tetap tercatat sebagai hasil impor."""
    # Arrange
    case_id = _impor(pengimpor, _berkas(_lengkap("Judul uji", "Tanya"))).json()["rows"][0][
        "case_id"
    ]

    # Act
    respons = pengimpor.post(f"/cases/{case_id}/duplicate")
    asal = pengimpor.get(f"/cases/{case_id}").json()

    # Assert
    assert respons.status_code == 201, respons.text
    salinan = respons.json()
    assert (salinan["origin"], salinan["source_case_id"]) == ("duplicate", case_id)
    assert (salinan["status"], salinan["split_tag"]) == ("draft", None)
    assert salinan["identity"] == asal["identity"]
    assert (asal["origin"], asal["split_tag"]) == ("import", "test")


# --- AC3 pencarian kasus hasil impor ----------------------------------------------------------


def test_kasus_impor_ditemukan_lewat_kode_judul_dan_suite_sementara(pengimpor):
    """Positif: AC3, kasus hasil impor bisa dicari dengan awalan kode IMPOR-, judulnya, dan
    disaring ke suite sementara batch-nya."""
    # Arrange
    ringkasan = _impor(
        pengimpor, _berkas(_lengkap("Judul uji alfa", "Tanya 1"), _lengkap("Judul uji beta", "2"))
    ).json()
    pengimpor.post("/suites", json={"name": "Suite manual uji"})

    # Act
    per_kode = pengimpor.get("/cases", params={"q": "impor-"}).json()
    per_judul = pengimpor.get("/cases", params={"q": "uji beta"}).json()
    per_suite = pengimpor.get("/cases", params={"suite_id": ringkasan["suite_id"]}).json()

    # Assert
    kode = {b["case_code"] for b in ringkasan["rows"]}
    assert all(k.startswith("IMPOR-") for k in kode)
    assert {i["case_code"] for i in per_kode["items"]} == kode
    assert [i["title"] for i in per_judul["items"]] == ["Judul uji beta"]
    assert per_suite["total"] == 2


# --- OWASP A03 isi formula --------------------------------------------------------------------


def test_isi_berbentuk_formula_disimpan_sebagai_teks_biasa(pengimpor):
    """Edge: OWASP A03, sel seperti =HYPERLINK(...) tidak dijalankan atau diubah saat impor; ia
    disimpan dan dikembalikan sebagai teks. Ekspor CSV yang ada hanya mencetak kode kasus buatan
    sistem (score_csv) atau menetralkan sel (audit export), jadi teks ini tidak pernah sampai ke
    spreadsheet sebagai formula."""
    # Arrange
    formula = '=HYPERLINK("http://contoh.invalid","klik")'

    # Act
    baris = _impor(pengimpor, _berkas(_lengkap(formula, "+1+1"))).json()["rows"][0]
    kasus = pengimpor.get(f"/cases/{baris['case_id']}").json()

    # Assert
    assert baris["status"] == "created"
    assert baris["case_code"].startswith("IMPOR-")
    assert (kasus["identity"]["title"], kasus["identity"]["question"]) == (formula, "+1+1")
