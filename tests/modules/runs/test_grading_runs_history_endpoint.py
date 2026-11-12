"""GET /measurements/{id}/grading-runs: riwayat penilaian sesi. PBI-14 AC7, SCRUM-225.

Hanya Admin yang boleh membaca riwayat, karena nilai ulang adalah fitur Admin dan setiap baris
membawa skor dev, test, dan gabungan. Baris riwayat tidak membawa rincian kasus.

Setiap tes diberi label di docstring-nya:
- Positif: perilaku yang memang diharapkan terjadi.
- Negatif: permintaan yang harus ditolak.
- Edge: batas atau kasus pinggir.
"""

import uuid
from datetime import UTC, date, datetime

import pytest

from app.modules.auth.models import User
from app.modules.grading.models import RubricVersion
from app.modules.runs.models import SessionStatus
from app.shared.security import Role
from tests.modules.providers.test_product_lists import _otomatis
from tests.modules.runs.test_session_grade import _setup_sesi_lengkap, pasang_penilai_tiruan


@pytest.fixture
def pembuat(db_session) -> User:
    user = User(email="pembuat-riwayat-http@veritask.test", name="Admin Riwayat", role=Role.ADMIN)
    db_session.add(user)
    db_session.commit()
    return user


@pytest.fixture
def produk(db_session):
    item = _otomatis("Produk Riwayat HTTP")
    db_session.add(item)
    db_session.commit()
    return item


def _url(session_id: uuid.UUID) -> str:
    return f"/measurements/{session_id}/grading-runs"


def _tambah_versi_dua(db_session, pembuat) -> None:
    """Versi 2 di tabel rubrik, supaya nilai ulang dengan v2 diterima."""
    db_session.add(
        RubricVersion(
            id=uuid.uuid4(),
            version_no=2,
            effective_date=date.today(),
            is_active=False,
            locked_at=datetime.now(UTC),
            judge_prompt_quality="Quality prompt v2",
            judge_prompt_format="Format prompt v2",
            created_by=pembuat.id,
        )
    )
    db_session.commit()


def test_admin_melihat_penilaian_pertama_dan_nilai_ulang_terbaru_di_atas(
    as_role, db_session, produk, pembuat, monkeypatch
):
    """Positif: nilai lalu nilai ulang menghasilkan dua baris, yang terbaru bertanda."""
    # Arrange
    sesi = _setup_sesi_lengkap(db_session, produk, pembuat, status=SessionStatus.FINISHED)
    pasang_penilai_tiruan(db_session, sesi, monkeypatch)
    _tambah_versi_dua(db_session, pembuat)
    admin = as_role(Role.ADMIN)
    assert admin.post(_url(sesi.id).replace("grading-runs", "grade"), json={}).status_code == 200
    nilai_ulang = admin.post(
        _url(sesi.id).replace("grading-runs", "grade"), json={"rubric_version": "v2"}
    )
    assert nilai_ulang.status_code == 200

    # Act
    res = admin.get(_url(sesi.id))

    # Assert
    assert res.status_code == 200
    baris = res.json()
    assert [b["kind"] for b in baris] == ["regrade", "initial"]
    assert [b["rubric_version"] for b in baris] == ["v2", "v1"]
    assert [b["is_latest"] for b in baris] == [True, False]
    assert baris[0]["grading_run_id"] == nilai_ulang.json()["grading_run_id"]


def test_baris_riwayat_tidak_membawa_rincian_kasus(as_role, db_session, produk, pembuat):
    """Positif: hanya ringkasan; kasus dan jawaban mentah tidak ikut terkirim."""
    # Arrange
    sesi = _setup_sesi_lengkap(db_session, produk, pembuat, status=SessionStatus.FINISHED)
    admin = as_role(Role.ADMIN)
    admin.post(_url(sesi.id).replace("grading-runs", "grade"), json={})

    # Act
    res = admin.get(_url(sesi.id))

    # Assert
    assert set(res.json()[0]) == {
        "grading_run_id",
        "rubric_version",
        "kind",
        "status",
        "triggered_by",
        "triggered_at",
        "is_latest",
        "scores",
        "judge",
    }
    assert set(res.json()[0]["scores"]) == {"dev", "test", "combined"}


def test_sesi_yang_belum_dinilai_mengembalikan_daftar_kosong(as_role, db_session, produk, pembuat):
    """Edge: belum dinilai bukan error, riwayatnya memang kosong."""
    # Arrange
    sesi = _setup_sesi_lengkap(db_session, produk, pembuat, status=SessionStatus.FINISHED)

    # Act
    res = as_role(Role.ADMIN).get(_url(sesi.id))

    # Assert
    assert res.status_code == 200
    assert res.json() == []


def test_sesi_tidak_ada_ditolak_404(as_role):
    """Negatif: sesi yang tidak ada dijawab MEASUREMENT_NOT_FOUND."""
    # Arrange
    sesi_id = uuid.uuid4()

    # Act
    res = as_role(Role.ADMIN).get(_url(sesi_id))

    # Assert
    assert res.status_code == 404
    assert res.json()["code"] == "MEASUREMENT_NOT_FOUND"


@pytest.mark.parametrize("peran", [Role.VIEWER, Role.DEVELOPER_AI, Role.CONTRIBUTOR])
def test_selain_admin_ditolak_403_walau_sesi_sudah_dinilai(
    peran, as_role, db_session, produk, pembuat
):
    """Negatif: riwayat hanya untuk Admin; sesi selesai pun tidak membuka akses peran lain."""
    # Arrange
    sesi = _setup_sesi_lengkap(db_session, produk, pembuat, status=SessionStatus.FINISHED)
    as_role(Role.ADMIN).post(_url(sesi.id).replace("grading-runs", "grade"), json={})

    # Act
    res = as_role(peran).get(_url(sesi.id))

    # Assert
    assert res.status_code == 403
    assert res.json()["code"] == "FORBIDDEN"


def test_id_sesi_bukan_uuid_ditolak_422(as_role):
    """Negatif: id yang bukan UUID ditolak validasi path sebelum menyentuh data."""
    # Arrange
    url = "/measurements/bukan-uuid/grading-runs"

    # Act
    res = as_role(Role.ADMIN).get(url)

    # Assert
    assert res.status_code == 422
