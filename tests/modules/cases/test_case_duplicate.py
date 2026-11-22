"""POST /cases/{id}/duplicate. PBI-4 SCRUM-81 SCRUM-337, AC2, diagram D8 dan D9.

AC2: Kontributor menduplikasi kasus lama sebagai titik awal kasus baru yang serupa. Salinan
adalah kasus baru dengan id dan kode unik, berstatus draft, dan tag dev/test dikosongkan supaya
dipilih ulang. Keputusan SA D8: salinan tidak mewarisi riwayat versi dan review, mulai dari
versi 1, dan penulisnya pengguna yang menyalin. D9: hanya Kontributor dan Admin.

Label: Positif = jalur normal, Negatif = harus ditolak, Edge = data batas.
"""

import uuid

import pytest
from sqlalchemy.exc import IntegrityError

from app.modules.audit.models import AuditLog
from app.modules.cases import service
from app.modules.cases.models import Case, CaseOrigin, CaseStatus
from app.shared.security import Role
from tests.modules.cases.test_cases import AUTHOR_LAIN, _badan, _buat, _ganti, _setujui, _suite


@pytest.fixture
def penulis(as_role):
    return as_role(Role.CONTRIBUTOR)


def _kasus_approved(client, db_session, **ubah) -> dict:
    """Kasus approved di suite baru, isi lengkap dengan rujukan dan kriteria."""
    dibuat = _buat(client, _suite(client), **ubah).json()
    _setujui(db_session, dibuat["id"])
    return client.get(f"/cases/{dibuat['id']}").json()


def _salin(client, case_id: str):
    return client.post(f"/cases/{case_id}/duplicate")


def test_salinan_adalah_kasus_baru_berstatus_draft_tanpa_tag(penulis, db_session):
    """Positif: AC2, id dan kode baru, draft versi 1, tag kosong, isi sama dengan kasus asal,
    dan asalnya tercatat sebagai duplikat dari kasus itu."""
    # Arrange
    asal = _kasus_approved(penulis, db_session)

    # Act
    respons = _salin(penulis, asal["id"])

    # Assert
    assert respons.status_code == 201, respons.text
    salinan = respons.json()
    assert salinan["id"] != asal["id"]
    assert (salinan["case_code"], salinan["status"], salinan["version"]) == (
        "PHK-001-SALIN",
        "draft",
        1,
    )
    assert (salinan["split_tag"], salinan["was_dev"]) == (None, False)
    assert (salinan["origin"], salinan["source_case_id"]) == ("duplicate", asal["id"])
    for bagian in ("suite_id", "identity", "legal_refs", "answer_criteria"):
        assert salinan[bagian] == asal[bagian], bagian


def test_penulis_salinan_adalah_penyalin_dan_kasus_asal_tidak_berubah(
    penulis, db_session, buat_pengguna
):
    """Positif: keputusan SA D8, salinan ditulis penyalin, bukan penulis asal; kasus asal tetap
    approved dengan tag dan kodenya."""
    # Arrange
    asal = _kasus_approved(penulis, db_session)
    penyalin = uuid.uuid4()
    _ganti(penulis, buat_pengguna(Role.CONTRIBUTOR, user_id=penyalin))

    # Act
    salinan = _salin(penulis, asal["id"]).json()

    # Assert
    baris = db_session.get(Case, uuid.UUID(salinan["id"]))
    assert (baris.created_by, baris.current_version.created_by) == (penyalin, penyalin)
    sesudah = penulis.get(f"/cases/{asal['id']}").json()
    assert (sesudah["status"], sesudah["split_tag"], sesudah["case_code"]) == (
        "approved",
        asal["split_tag"],
        "PHK-001",
    )


def test_yang_disalin_adalah_versi_yang_sedang_berlaku(penulis, db_session):
    """Positif: versi approved disalin walaupun sudah ada draft versi 2 yang sedang diubah."""
    # Arrange
    asal = _kasus_approved(penulis, db_session)
    assert penulis.post(f"/cases/{asal['id']}/versions").status_code == 201
    draft_baru = {**asal["identity"], "title": "Judul draft v2"}
    assert penulis.put(f"/cases/{asal['id']}", json=_badan(identity=draft_baru)).status_code == 200

    # Act
    salinan = _salin(penulis, asal["id"]).json()

    # Assert
    assert salinan["identity"]["title"] == asal["identity"]["title"]


def test_kasus_yang_belum_pernah_disetujui_menyalin_draftnya(penulis):
    """Edge: kasus yang masih draft juga bisa disalin; yang disalin isi draft itu."""
    # Arrange
    asal = _buat(penulis, _suite(penulis)).json()

    # Act
    salinan = _salin(penulis, asal["id"]).json()

    # Assert
    assert (salinan["identity"], salinan["case_code"]) == (asal["identity"], "PHK-001-SALIN")


def test_salinan_berikutnya_mendapat_kode_bernomor(penulis, db_session):
    """Edge: kode salinan selalu unik; salinan kedua dan ketiga diberi nomor."""
    # Arrange
    asal = _kasus_approved(penulis, db_session)

    # Act
    kode = [_salin(penulis, asal["id"]).json()["case_code"] for _ in range(3)]

    # Assert
    assert kode == ["PHK-001-SALIN", "PHK-001-SALIN-2", "PHK-001-SALIN-3"]


def test_kode_asal_yang_panjang_dipotong_supaya_muat_64_karakter(penulis, db_session):
    """Edge: kode asal 64 karakter tetap menghasilkan kode salinan yang sah (maksimal 64)."""
    # Arrange
    asal = _kasus_approved(penulis, db_session, case_code="K" * 64)

    # Act
    pertama = _salin(penulis, asal["id"]).json()["case_code"]
    kedua = _salin(penulis, asal["id"]).json()["case_code"]

    # Assert
    assert (len(pertama), pertama.endswith("-SALIN")) == (64, True)
    assert (len(kedua), kedua.endswith("-SALIN-2")) == (64, True)


def test_tag_harus_dipilih_ulang_sebelum_salinan_siap_direview(penulis, db_session):
    """Positif: AC2, salinan dari kasus lengkap hanya kurang tag; setelah tag dipilih lewat
    penyimpanan biasa, salinan siap diajukan review."""
    # Arrange
    asal = _kasus_approved(penulis, db_session)
    salinan = _salin(penulis, asal["id"]).json()
    sebelum = penulis.get(f"/cases/{salinan['id']}/completeness").json()

    # Act
    simpan = penulis.put(
        f"/cases/{salinan['id']}", json=_badan(case_code=salinan["case_code"], split_tag="test")
    )
    sesudah = penulis.get(f"/cases/{salinan['id']}/completeness").json()

    # Assert
    assert [kurang["field"] for kurang in sebelum["missing"]] == ["split_tag"]
    assert sebelum["ready_for_review"] is False
    assert simpan.status_code == 200, simpan.text
    assert (sesudah["missing"], sesudah["ready_for_review"]) == ([], True)


def test_admin_boleh_menduplikasi(as_role, db_session):
    """Positif: D9, Admin juga boleh menduplikasi kasus."""
    # Arrange
    client = as_role(Role.ADMIN)
    asal = _kasus_approved(client, db_session)

    # Act
    respons = _salin(client, asal["id"])

    # Assert
    assert respons.status_code == 201, respons.text


@pytest.mark.parametrize("peran", [Role.VIEWER, Role.DEVELOPER_AI])
def test_viewer_dan_developer_ai_tidak_boleh_menduplikasi(
    penulis, db_session, peran, buat_pengguna
):
    """Negatif: D9, hanya Kontributor dan Admin; tidak ada kasus yang ikut terbuat."""
    # Arrange
    asal = _kasus_approved(penulis, db_session)
    jumlah = db_session.query(Case).count()
    _ganti(penulis, buat_pengguna(peran, user_id=uuid.uuid4()))

    # Act
    respons = _salin(penulis, asal["id"])

    # Assert
    assert respons.status_code == 403
    assert db_session.query(Case).count() == jumlah


def test_kasus_yang_tidak_ada_dijawab_404(penulis):
    """Negatif: id kasus yang tidak dikenal."""
    # Act / Assert
    assert _salin(penulis, str(uuid.uuid4())).status_code == 404


def test_kasus_di_suite_yang_diarsipkan_tidak_bisa_disalin(penulis):
    """Negatif: salinan masuk ke suite asal, jadi suite itu harus masih aktif."""
    # Arrange
    suite_id = _suite(penulis)
    asal = _buat(penulis, suite_id).json()
    assert penulis.post(f"/suites/{suite_id}/archive").status_code == 200

    # Act
    respons = _salin(penulis, asal["id"])

    # Assert
    assert (respons.status_code, respons.json()["code"]) == (422, "SUITE_NOT_ACTIVE")


def test_salinan_punya_sidik_jari_dan_jejak_audit_asalnya(penulis, db_session):
    """Positif: D7 dan D8, sidik jari salinan sama dengan kasus asal (isinya sama), dan audit
    case.created mencatat bahwa kasus ini duplikat dari kasus asal."""
    # Arrange
    asal = _kasus_approved(penulis, db_session)

    # Act
    salinan = _salin(penulis, asal["id"]).json()

    # Assert
    baris = {k.id: k for k in db_session.query(Case).all()}
    asal_id, salinan_id = uuid.UUID(asal["id"]), uuid.UUID(salinan["id"])
    assert baris[salinan_id].content_fingerprint == baris[asal_id].content_fingerprint
    assert baris[salinan_id].origin is CaseOrigin.DUPLICATE
    (dibuat,) = (
        db_session.query(AuditLog)
        .filter(AuditLog.action == "case.created", AuditLog.entity_id == salinan_id)
        .all()
    )
    assert dibuat.after == {
        "case_code": "PHK-001-SALIN",
        "origin": "duplicate",
        "source_case_id": asal["id"],
    }


def test_kasus_draft_milik_orang_lain_bisa_disalin_kontributor(penulis, buat_pengguna):
    """Edge: menduplikasi tidak butuh hak menulis kasus asal; Kontributor lain boleh memakai
    kasus siapa pun sebagai contoh (AC2)."""
    # Arrange
    asal = _buat(penulis, _suite(penulis)).json()
    _ganti(penulis, buat_pengguna(Role.CONTRIBUTOR, user_id=AUTHOR_LAIN))

    # Act
    respons = _salin(penulis, asal["id"])

    # Assert
    assert respons.status_code == 201, respons.text
    assert respons.json()["status"] == CaseStatus.DRAFT.value


class _GalatDriver(Exception):
    """Pengganti galat driver database; IntegrityError membaca pesannya dari .orig."""


def _simpan_gagal(monkeypatch, pesan: str) -> None:
    def gagal(*_args, **_kwargs):
        raise IntegrityError("INSERT INTO cases", {}, _GalatDriver(pesan))

    monkeypatch.setattr(service.repository, "create", gagal)


def test_kode_yang_keburu_dipakai_salinan_lain_dijawab_409(penulis, db_session, monkeypatch):
    """Edge: kode yang bebas saat dicek dipakai salinan lain sebelum tersimpan (dua klik
    bersamaan); jawabannya 409 CASE_CODE_TAKEN, dan pengguna cukup mengulang."""
    # Arrange
    asal = _kasus_approved(penulis, db_session)
    _simpan_gagal(
        monkeypatch, 'duplicate key value violates unique constraint "uq_cases_case_code"'
    )

    # Act
    respons = _salin(penulis, asal["id"])

    # Assert
    assert (respons.status_code, respons.json()["code"]) == (409, "CASE_CODE_TAKEN")


def test_galat_database_lain_tidak_disamarkan_sebagai_bentrok_kode(
    penulis, db_session, monkeypatch
):
    """Negatif: galat integritas selain bentrok case_code diteruskan, bukan dijawab 409."""
    # Arrange
    asal = _kasus_approved(penulis, db_session)
    _simpan_gagal(monkeypatch, 'insert or update violates foreign key constraint "fk_lain"')

    # Act / Assert
    with pytest.raises(IntegrityError, match="fk_lain"):
        _salin(penulis, asal["id"])
