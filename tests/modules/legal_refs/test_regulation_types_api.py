"""GET /regulation-types (PBI-21 AC2, SCRUM-160).

Daftar jenis peraturan untuk dropdown editor. Keputusan PO 16-17 Okt: code,
label, singkatan, is_active; hanya yang aktif kecuali include_inactive=true;
urut menurut urutan; boleh dibaca semua peran yang sudah login.

Data dari fixture jenis_peraturan (seed SCRUM-159, 266 baris).
"""

import pytest
from sqlalchemy import update

from app.modules.legal_refs.models import RegulationType
from app.shared.security import Role

SEMUA_PERAN = [Role.CONTRIBUTOR, Role.CONTRIBUTOR, Role.ADMIN, Role.VIEWER]


def _nonaktifkan(db_session, *codes: str) -> None:
    db_session.execute(
        update(RegulationType).where(RegulationType.code.in_(codes)).values(is_active=False)
    )
    db_session.commit()


@pytest.mark.parametrize("peran", SEMUA_PERAN, ids=lambda peran: peran.value)
def test_semua_peran_yang_login_boleh_membaca(as_role, peran):
    # Act
    respons = as_role(peran).get("/regulation-types")

    # Assert
    assert respons.status_code == 200
    assert len(respons.json()) == 266


def test_tanpa_login_401(client):
    # Act
    respons = client.get("/regulation-types")

    # Assert
    assert respons.status_code == 401
    assert respons.json()["code"] == "UNAUTHENTICATED"


def test_bentuk_item_dan_urutan_mengikuti_seed(as_role):
    # Act
    daftar = as_role(Role.CONTRIBUTOR).get("/regulation-types").json()

    # Assert
    assert daftar[:3] == [
        {
            "code": "uud",
            "label": "Undang-undang Dasar Tahun 1945 dan Amandemen",
            "singkatan": "UUD",
            "is_active": True,
        },
        {"code": "ketetapan_mpr", "label": "Ketetapan MPR", "singkatan": None, "is_active": True},
        {"code": "uu", "label": "Undang-undang", "singkatan": "UU", "is_active": True},
    ]
    assert daftar[-1]["code"] == "perwali"


def test_jenis_nonaktif_tidak_muncul_secara_bawaan(as_role, db_session):
    # Arrange
    client = as_role(Role.CONTRIBUTOR)
    _nonaktifkan(db_session, "permen", "kepmen")

    # Act
    codes = [item["code"] for item in client.get("/regulation-types").json()]

    # Assert
    assert len(codes) == 264
    assert "permen" not in codes
    assert "kepmen" not in codes


def test_include_inactive_menampilkan_semua_dengan_penanda(as_role, db_session):
    """Rujukan lama yang menunjuk jenis nonaktif tetap punya label di editor."""
    # Arrange
    client = as_role(Role.CONTRIBUTOR)
    _nonaktifkan(db_session, "permen")

    # Act
    daftar = client.get("/regulation-types", params={"include_inactive": "true"}).json()

    # Assert
    assert len(daftar) == 266
    permen = next(item for item in daftar if item["code"] == "permen")
    assert permen == {
        "code": "permen",
        "label": "Peraturan Kementerian",
        "singkatan": "Permen",
        "is_active": False,
    }


def test_include_inactive_false_eksplisit_sama_dengan_bawaan(as_role, db_session):
    # Arrange
    client = as_role(Role.CONTRIBUTOR)
    _nonaktifkan(db_session, "permen")

    # Act
    bawaan = client.get("/regulation-types").json()
    eksplisit = client.get("/regulation-types", params={"include_inactive": "false"}).json()

    # Assert
    assert eksplisit == bawaan


def test_semua_nonaktif_mengembalikan_daftar_kosong(as_role, db_session):
    # Arrange
    client = as_role(Role.CONTRIBUTOR)
    db_session.execute(update(RegulationType).values(is_active=False))
    # Act
    db_session.commit()

    # Assert
    assert client.get("/regulation-types").json() == []


def test_include_inactive_bukan_boolean_ditolak(as_role):
    # Act
    respons = as_role(Role.CONTRIBUTOR).get(
        "/regulation-types", params={"include_inactive": "kadang"}
    )

    # Assert
    assert respons.status_code == 422


def test_kontrak_openapi(client):
    # Arrange
    spec = client.get("/openapi.json").json()
    operasi = spec["paths"]["/regulation-types"]["get"]
    # Act
    item = spec["components"]["schemas"]["RegulationTypeRead"]

    # Assert
    assert operasi["parameters"][0]["name"] == "include_inactive"
    assert set(item["required"]) == {"code", "label", "singkatan", "is_active"}
    assert {"401", "403"} <= set(operasi["responses"])
