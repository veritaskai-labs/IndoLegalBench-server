"""Peran Developer AI dan penyamaran kasus test. SCRUM-183, PBI-9 AC4.

Developer AI hanya boleh membaca isi kasus dev yang sudah disetujui. Kasus
test tetap muncul di daftar dan hitungan, tetapi kode dan judulnya diganti
placeholder oleh server, dan detailnya ditolak. Kasus yang belum disetujui
tidak terlihat sama sekali.
"""

import uuid

import pytest

from app.modules.cases.service import MASKED_CASE_CODE, MASKED_TITLE
from app.shared.security import Role
from tests.modules.cases.test_cases import ADMIN_LAIN, _badan, _buat, _setujui, _suite
from tests.modules.cases.test_split_tag_api import _jadi

JUDUL_TEST = "Pesangon pekerja kontrak yang dirahasiakan"
KODE_TEST = "RAHASIA-001"


@pytest.fixture
def suite_campuran(as_role, buat_pengguna, db_session):
    """Satu suite berisi dev dan test, masing-masing approved dan draf.

    Yang login di akhir fixture adalah Developer AI.
    """
    client = as_role(Role.AUTHOR)
    suite_id = _suite(client)
    ids = {
        "dev_ok": _buat(client, suite_id, case_code="DEV-001").json()["id"],
        "test_ok": _buat(
            client,
            suite_id,
            case_code=KODE_TEST,
            split_tag="test",
            identity={"title": JUDUL_TEST, "question": "Rahasia?", "category": None},
        ).json()["id"],
        "dev_draf": _buat(client, suite_id, case_code="DEV-002").json()["id"],
        "test_draf": _buat(client, suite_id, case_code="TEST-002", split_tag="test").json()["id"],
    }
    _setujui(db_session, ids["dev_ok"])
    _setujui(db_session, ids["test_ok"])
    _jadi(buat_pengguna(Role.DEVELOPER_AI))
    return client, suite_id, ids


def test_daftar_hanya_kasus_disetujui_dan_test_disamarkan(suite_campuran):
    # Arrange
    client, suite_id, ids = suite_campuran

    # Act
    response = client.get(f"/suites/{suite_id}/cases")

    # Assert
    assert response.status_code == 200
    baris = {item["id"]: item for item in response.json()}
    assert set(baris) == {ids["dev_ok"], ids["test_ok"]}
    assert baris[ids["dev_ok"]]["case_code"] == "DEV-001"
    assert baris[ids["dev_ok"]]["masked"] is False
    samar = baris[ids["test_ok"]]
    assert samar["case_code"] == MASKED_CASE_CODE
    assert samar["title"] == MASKED_TITLE
    assert samar["masked"] is True
    assert samar["split_tag"] == "test"
    assert samar["status"] == "approved"


def test_isi_kasus_test_tidak_ada_di_response_daftar(suite_campuran):
    # Arrange
    client, suite_id, _ = suite_campuran

    # Act
    teks = client.get(f"/suites/{suite_id}/cases").text

    # Assert
    assert JUDUL_TEST not in teks
    assert KODE_TEST not in teks


@pytest.mark.parametrize(
    ("params", "kunci"),
    [
        ({"split_tag": "test"}, {"test_ok"}),
        ({"split_tag": "dev"}, {"dev_ok"}),
        ({"status": "approved"}, {"dev_ok", "test_ok"}),
        ({"status": "draft"}, set()),
        ({"status": "in_review"}, set()),
    ],
)
def test_filter_daftar_tetap_dalam_kasus_disetujui(suite_campuran, params, kunci):
    # Arrange
    client, suite_id, ids = suite_campuran

    # Act
    response = client.get(f"/suites/{suite_id}/cases", params=params)

    # Assert
    assert response.status_code == 200
    assert {item["id"] for item in response.json()} == {ids[k] for k in kunci}


def test_daftar_suite_yang_tidak_ada_tetap_404(suite_campuran):
    # Arrange
    client, _, _ = suite_campuran

    # Act
    response = client.get(f"/suites/{uuid.uuid4()}/cases", params={"status": "draft"})

    # Assert
    assert response.status_code == 404


def test_detail_kasus_dev_disetujui_terbuka_penuh(suite_campuran):
    # Arrange
    client, _, ids = suite_campuran

    # Act
    response = client.get(f"/cases/{ids['dev_ok']}")

    # Assert
    assert response.status_code == 200
    body = response.json()
    assert body["case_code"] == "DEV-001"
    assert body["identity"]["title"] == "PHK sepihak"
    assert body["answer_criteria"]["expected_conclusion"] == "tidak sah"
    assert body["traps"]
    assert body["status"] == "approved"


def test_detail_kasus_test_ditolak_tanpa_membocorkan_isi(suite_campuran):
    # Arrange
    client, _, ids = suite_campuran

    # Act
    response = client.get(f"/cases/{ids['test_ok']}")

    # Assert
    assert response.status_code == 403
    assert response.json()["code"] == "TEST_CASE_HIDDEN"
    assert JUDUL_TEST not in response.text
    assert KODE_TEST not in response.text


@pytest.mark.parametrize("kunci", ["dev_draf", "test_draf"])
def test_detail_kasus_belum_disetujui_seperti_tidak_ada(suite_campuran, kunci):
    # Arrange
    client, _, ids = suite_campuran

    # Act
    response = client.get(f"/cases/{ids[kunci]}")

    # Assert
    assert response.status_code == 404


def test_detail_memakai_versi_disetujui_bukan_draf_versi_baru(as_role, buat_pengguna, db_session):
    # Arrange
    client = as_role(Role.AUTHOR)
    suite_id = _suite(client)
    case_id = _buat(client, suite_id, case_code="DEV-010").json()["id"]
    _setujui(db_session, case_id)
    assert client.post(f"/cases/{case_id}/versions").status_code == 201
    draf_baru = _buat_badan_judul("Judul draf versi 2")
    assert client.put(f"/cases/{case_id}", json=draf_baru).status_code == 200
    _jadi(buat_pengguna(Role.DEVELOPER_AI))

    # Act
    response = client.get(f"/cases/{case_id}")

    # Assert
    assert response.status_code == 200
    assert response.json()["identity"]["title"] == "PHK sepihak"
    assert response.json()["version"] == 1


def test_dev_dipindah_ke_test_langsung_tertutup(suite_campuran, buat_pengguna):
    # Arrange
    client, suite_id, ids = suite_campuran
    _jadi(buat_pengguna(Role.ADMIN, user_id=ADMIN_LAIN))
    pindah = client.patch(
        f"/cases/{ids['dev_ok']}/split-tag", json={"split_tag": "test", "confirm": True}
    )
    assert pindah.status_code == 200
    _jadi(buat_pengguna(Role.DEVELOPER_AI))

    # Act
    detail = client.get(f"/cases/{ids['dev_ok']}")
    daftar = client.get(f"/suites/{suite_id}/cases").json()

    # Assert
    assert detail.status_code == 403
    assert all(item["masked"] for item in daftar)


def test_jumlah_suite_menghitung_semua_kasus_disetujui(suite_campuran, buat_pengguna):
    # Arrange
    client, suite_id, _ = suite_campuran

    # Act
    detail = client.get(f"/suites/{suite_id}")
    daftar = client.get("/suites")

    # Assert
    assert detail.status_code == 200
    assert detail.json()["case_count"] == 2
    assert detail.json()["is_empty"] is False
    [item] = [s for s in daftar.json()["items"] if s["id"] == suite_id]
    assert item["case_count"] == 2


def test_jumlah_suite_untuk_author_tidak_berubah(suite_campuran, buat_pengguna):
    # Arrange
    client, suite_id, _ = suite_campuran
    _jadi(buat_pengguna(Role.AUTHOR))

    # Act
    response = client.get(f"/suites/{suite_id}")

    # Assert
    assert response.json()["case_count"] == 4


def test_suite_tanpa_kasus_disetujui_terlihat_kosong(as_role, buat_pengguna):
    """exportable tetap dari jumlah sebenarnya: itu soal pengukuran, bukan tampilan."""
    # Arrange
    client = as_role(Role.AUTHOR)
    suite_id = _suite(client)
    _buat(client, suite_id)
    _jadi(buat_pengguna(Role.DEVELOPER_AI))

    # Act
    body = client.get(f"/suites/{suite_id}").json()

    # Assert
    assert body["case_count"] == 0
    assert body["is_empty"] is True
    assert body["exportable"] is True


def _buat_badan_judul(judul: str) -> dict:
    return _badan(
        case_code="DEV-010",
        identity={"title": judul, "question": "Apakah PHK tanpa surat sah?", "category": None},
    )


# --- Semua jalur lain tertutup ---------------------------------------------

# Jalur yang memang dibuka untuk Developer AI di SCRUM-183.
_DIBUKA = {
    ("GET", "/suites"),
    ("GET", "/suites/{suite_id}"),
    ("GET", "/suites/{suite_id}/cases"),
    ("GET", "/cases/{case_id}"),
    ("GET", "/regulation-types"),
}
# Jalur tanpa penjaga peran: login, profil sendiri, dan lonceng notifikasi.
_TANPA_PERAN = ("/health", "/auth/", "/me", "/notifications")


def _jalur_tertutup() -> list[tuple[str, str, dict]]:
    from app.main import app

    hasil = []
    for path, metode in app.openapi()["paths"].items():
        if path.startswith(_TANPA_PERAN):
            continue
        for nama, operasi in metode.items():
            if (nama.upper(), path) in _DIBUKA:
                continue
            hasil.append((nama.upper(), path, operasi))
    return hasil


def _isi_path(path: str, operasi: dict) -> str:
    for param in operasi.get("parameters", []):
        if param["in"] != "path":
            continue
        nilai = "1" if param["schema"].get("type") == "integer" else str(uuid.uuid4())
        path = path.replace("{" + param["name"] + "}", nilai)
    return path


@pytest.mark.parametrize(
    ("metode", "path", "operasi"),
    _jalur_tertutup(),
    ids=lambda nilai: nilai if isinstance(nilai, str) else "",
)
def test_developer_ai_ditolak_di_semua_jalur_lain(as_role, metode, path, operasi):
    """Peran baru jatuh ke akses paling terbatas, bukan terbuka."""
    # Arrange
    client = as_role(Role.DEVELOPER_AI)

    # Act
    kirim = {"json": {}} if metode in {"POST", "PUT", "PATCH"} else {}
    response = client.request(metode, _isi_path(path, operasi), **kirim)

    # Assert
    assert response.status_code == 403, response.text
