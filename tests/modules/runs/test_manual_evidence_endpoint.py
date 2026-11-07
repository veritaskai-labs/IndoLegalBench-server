"""Endpoint bukti jawaban manual lewat HTTP. PBI-22 (SCRUM-176) AC7, AC8, AC9; SCRUM-233.

D9 attempt_evidence, maks 3 per jawaban, berkas PNG/JPG/PDF atau tautan:
- POST   /manual-measurements/{id}/answers/{snapshot_item_id}/{attempt_no}/evidence
         multipart: field file (berkas) atau url (tautan), tepat satu.
- GET    /manual-measurements/{id}/answers/{snapshot_item_id}/{attempt_no}/evidence
- GET    /manual-measurements/{id}/evidence/{evidence_id}/file   unduh berkas
- DELETE /manual-measurements/{id}/evidence/{evidence_id}
D10: Admin dan Kontributor mengisi dan membuka sesi manual; Viewer dan Dev AI tidak.
Siapa yang boleh melihat bukti: flag MANUAL_EVIDENCE_VISIBILITY (TBA, default semua yang
bisa membuka sesi).

Repository dan penyimpanan berkas (evidence_files) diuji lewat jalur HTTP ini di SQLite;
trigger PostgreSQL diuji di test_manual_evidence_migration_pg.py.

Label: Positif = perilaku yang diharapkan, Negatif = ditolak, Edge = batas perilaku.
"""

import uuid

import pytest
from sqlalchemy import select, update

from app.main import app
from app.modules.audit.models import AuditLog
from app.modules.runs.models import Attempt, AttemptEvidence, EvidenceFile, MeasurementSession
from app.shared.config import get_settings
from app.shared.security import Role
from tests.modules.runs.test_manual_session_detail import _sesi_manual

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
PDF = b"%PDF-1.7\n" + b"\x00" * 64


@pytest.fixture
def manual(siap_manual):
    sesi_id = _sesi_manual(siap_manual, repeat_count=1)
    percobaan = siap_manual.db.scalars(
        select(Attempt).where(Attempt.session_id == sesi_id).order_by(Attempt.id)
    ).all()
    return siap_manual, sesi_id, percobaan


@pytest.fixture
def setelan():
    def _ubah(**isi):
        app.dependency_overrides[get_settings] = lambda: get_settings().model_copy(update=isi)

    yield _ubah
    app.dependency_overrides.pop(get_settings, None)


def _url(sesi_id, attempt) -> str:
    return (
        f"/manual-measurements/{sesi_id}/answers/{attempt.snapshot_item_id}"
        f"/{attempt.repetition_no}/evidence"
    )


def _unggah(client, sesi_id, attempt, isi=PNG, tipe="image/png", nama="bukti.png"):
    return client.post(_url(sesi_id, attempt), files={"file": (nama, isi, tipe)})


def _tautan(client, sesi_id, attempt, url="https://chat.example.com/share/1"):
    return client.post(_url(sesi_id, attempt), data={"url": url})


def _audit(db, sesi_id, action):
    return db.scalars(
        select(AuditLog).where(AuditLog.entity_id == sesi_id, AuditLog.action == action)
    ).all()


def test_kontributor_mengunggah_tangkapan_layar(manual):
    """Positif: AC7, AC9, berkas tersimpan utuh, tipe dari isi, audit berlabel manual."""
    # Arrange
    ctx, sesi_id, percobaan = manual

    # Act
    respons = _unggah(ctx.sebagai(Role.CONTRIBUTOR), sesi_id, percobaan[0])

    # Assert
    assert respons.status_code == 201, respons.text
    badan = respons.json()
    assert (badan["kind"], badan["mime_type"], badan["file_name"], badan["size_bytes"]) == (
        "file",
        "image/png",
        "bukti.png",
        len(PNG),
    )
    baris = ctx.db.get(AttemptEvidence, uuid.UUID(badan["id"]))
    assert ctx.db.get(EvidenceFile, baris.file_key).content == PNG
    (catatan,) = _audit(ctx.db, sesi_id, "measurement_session.evidence_added")
    assert catatan.after["source"] == "manual"
    assert catatan.after["attempt_id"] == str(percobaan[0].id)
    assert catatan.actor_user_id is not None


def test_admin_menambah_tautan(manual):
    """Positif: AC7, bukti berupa tautan percakapan di platform."""
    # Arrange
    ctx, sesi_id, percobaan = manual

    # Act
    respons = _tautan(ctx.sebagai(Role.ADMIN), sesi_id, percobaan[0])

    # Assert
    assert respons.status_code == 201, respons.text
    assert (respons.json()["kind"], respons.json()["url"]) == (
        "link",
        "https://chat.example.com/share/1",
    )


@pytest.mark.parametrize(
    ("isi", "tipe", "nama", "kode"),
    [
        (PDF, "image/png", "bukti.png", "EVIDENCE_TYPE_MISMATCH"),
        (b"GIF89a" + b"\x00" * 16, "image/gif", "bukti.gif", "EVIDENCE_TYPE_NOT_ALLOWED"),
        (b"", "image/png", "kosong.png", "EVIDENCE_EMPTY"),
    ],
    ids=["pdf-berlabel-png", "gif", "kosong"],
)
def test_berkas_tidak_sah_ditolak_422_tanpa_tersimpan(manual, isi, tipe, nama, kode):
    """Negatif: AC7, validasi tipe di server dari isi berkas, bukan hanya ekstensi."""
    # Arrange
    ctx, sesi_id, percobaan = manual

    # Act
    respons = _unggah(ctx.sebagai(Role.ADMIN), sesi_id, percobaan[0], isi=isi, tipe=tipe, nama=nama)

    # Assert
    assert respons.status_code == 422, respons.text
    assert respons.json()["code"] == kode
    assert ctx.db.scalars(select(AttemptEvidence)).all() == []
    assert ctx.db.scalars(select(EvidenceFile)).all() == []


def test_berkas_lewat_batas_teknis_ditolak(manual, setelan):
    """Negatif: AC7, ukuran bebas dengan batas teknis tim (MANUAL_EVIDENCE_MAX_BYTES)."""
    # Arrange
    ctx, sesi_id, percobaan = manual
    setelan(manual_evidence_max_bytes=32)

    # Act
    respons = _unggah(ctx.sebagai(Role.ADMIN), sesi_id, percobaan[0])

    # Assert
    assert respons.status_code == 422
    assert respons.json()["code"] == "EVIDENCE_TOO_LARGE"


@pytest.mark.parametrize("bentuk", ["keduanya", "kosong"])
def test_harus_tepat_satu_berkas_atau_tautan(manual, bentuk):
    """Negatif: AC7, satu bukti = satu berkas atau satu tautan."""
    # Arrange
    ctx, sesi_id, percobaan = manual
    client = ctx.sebagai(Role.ADMIN)
    url = _url(sesi_id, percobaan[0])

    # Act
    if bentuk == "keduanya":
        respons = client.post(
            url, data={"url": "https://x.test"}, files={"file": ("b.png", PNG, "image/png")}
        )
    else:
        respons = client.post(url, data={})

    # Assert
    assert respons.status_code == 422
    assert respons.json()["code"] == "EVIDENCE_KIND_REQUIRED"


def test_bukti_keempat_ditolak_409(manual):
    """Negatif: AC7, D9 maksimal 3 bukti per jawaban."""
    # Arrange
    ctx, sesi_id, percobaan = manual
    client = ctx.sebagai(Role.ADMIN)
    for _ in range(3):
        assert _tautan(client, sesi_id, percobaan[0]).status_code == 201

    # Act
    respons = _unggah(client, sesi_id, percobaan[0])

    # Assert
    assert respons.status_code == 409
    assert respons.json()["code"] == "EVIDENCE_LIMIT_REACHED"


def test_daftar_dan_unduh_bukti(manual):
    """Positif: AC7, bukti satu jawaban terbaca; berkas diunduh sebagai lampiran."""
    # Arrange
    ctx, sesi_id, percobaan = manual
    client = ctx.sebagai(Role.CONTRIBUTOR)
    berkas = _unggah(client, sesi_id, percobaan[0], nama="layar 1.png").json()
    _tautan(client, sesi_id, percobaan[0])
    _tautan(client, sesi_id, percobaan[1])

    # Act
    daftar = client.get(_url(sesi_id, percobaan[0]))
    unduh = client.get(f"/manual-measurements/{sesi_id}/evidence/{berkas['id']}/file")

    # Assert
    assert daftar.status_code == 200
    assert [b["kind"] for b in daftar.json()] == ["file", "link"]
    assert unduh.status_code == 200
    assert unduh.content == PNG
    assert unduh.headers["content-type"] == "image/png"
    assert unduh.headers["content-disposition"].startswith("attachment;")
    assert "layar%201.png" in unduh.headers["content-disposition"]
    # Browser tidak menebak tipe lain dari isi berkas.
    assert unduh.headers["x-content-type-options"] == "nosniff"


def test_kontributor_tidak_melihat_bukti_bila_flag_admin_saja(manual, setelan):
    """Negatif: AC7, flag MANUAL_EVIDENCE_VISIBILITY=admin_only; 404 tanpa bocor keberadaan."""
    # Arrange
    ctx, sesi_id, percobaan = manual
    setelan(manual_evidence_visibility="admin_only")
    bukti = _unggah(ctx.sebagai(Role.ADMIN), sesi_id, percobaan[0]).json()

    # Act
    client = ctx.sebagai(Role.CONTRIBUTOR)
    daftar = client.get(_url(sesi_id, percobaan[0]))
    unduh = client.get(f"/manual-measurements/{sesi_id}/evidence/{bukti['id']}/file")

    # Assert
    assert (daftar.status_code, unduh.status_code) == (404, 404)
    assert daftar.json()["code"] == "EVIDENCE_NOT_FOUND"


@pytest.mark.parametrize("peran", [Role.VIEWER, Role.DEVELOPER_AI])
def test_peran_di_luar_sesi_manual_ditolak_403(manual, peran):
    """Negatif: D10, Viewer dan Dev AI tidak membuka sesi manual."""
    # Arrange
    ctx, sesi_id, percobaan = manual

    # Act
    respons = _tautan(ctx.sebagai(peran), sesi_id, percobaan[0])

    # Assert
    assert respons.status_code == 403


def test_hapus_bukti_dan_berkasnya(manual):
    """Positif: AC7, AC9, bukti yang salah dihapus, berkasnya ikut hilang, tercatat di audit."""
    # Arrange
    ctx, sesi_id, percobaan = manual
    client = ctx.sebagai(Role.CONTRIBUTOR)
    bukti = _unggah(client, sesi_id, percobaan[0]).json()

    # Act
    respons = client.delete(f"/manual-measurements/{sesi_id}/evidence/{bukti['id']}")

    # Assert
    assert respons.status_code == 204
    ctx.db.expire_all()
    assert ctx.db.scalars(select(AttemptEvidence)).all() == []
    assert ctx.db.scalars(select(EvidenceFile)).all() == []
    assert len(_audit(ctx.db, sesi_id, "measurement_session.evidence_removed")) == 1


def test_bukti_sesi_selesai_tidak_bisa_ditambah_atau_dihapus(manual):
    """Negatif: AC8, sesi Selesai final; bukti tetap bisa dilihat."""
    # Arrange
    ctx, sesi_id, percobaan = manual
    client = ctx.sebagai(Role.ADMIN)
    bukti = _tautan(client, sesi_id, percobaan[0]).json()
    ctx.db.execute(
        update(MeasurementSession)
        .where(MeasurementSession.id == sesi_id)
        .values(status="finalized")
    )
    ctx.db.commit()

    # Act
    tambah = _unggah(client, sesi_id, percobaan[0])
    hapus = client.delete(f"/manual-measurements/{sesi_id}/evidence/{bukti['id']}")
    daftar = client.get(_url(sesi_id, percobaan[0]))

    # Assert
    assert (tambah.status_code, hapus.status_code, daftar.status_code) == (409, 409, 200)
    assert tambah.json()["code"] == "MANUAL_SESSION_FINALIZED"
    assert len(daftar.json()) == 1


def test_bukti_sesi_lain_dan_percobaan_asing_404(manual):
    """Negatif: id bukti hanya berlaku di sesinya; nomor percobaan di luar sesi 404."""
    # Arrange
    ctx, sesi_id, percobaan = manual
    client = ctx.sebagai(Role.ADMIN)
    bukti = _tautan(client, sesi_id, percobaan[0]).json()
    sesi_lain = _sesi_manual(ctx, repeat_count=1)

    # Act
    unduh = client.get(f"/manual-measurements/{sesi_lain}/evidence/{bukti['id']}/file")
    hapus = client.delete(f"/manual-measurements/{sesi_lain}/evidence/{bukti['id']}")
    asing = client.post(
        f"/manual-measurements/{sesi_id}/answers/{percobaan[0].snapshot_item_id}/5/evidence",
        data={"url": "https://x.test"},
    )

    # Assert
    assert (unduh.status_code, hapus.status_code, asing.status_code) == (404, 404, 404)
    assert hapus.json()["code"] == "EVIDENCE_NOT_FOUND"
    assert asing.json()["code"] == "MANUAL_ATTEMPT_NOT_FOUND"


def test_bukti_untuk_sesi_yang_tidak_ada_404(manual):
    """Negatif: id sesi asing (atau sesi otomatis) bukan sesi manual."""
    # Arrange
    ctx, _, percobaan = manual

    # Act
    respons = _tautan(ctx.sebagai(Role.ADMIN), uuid.uuid4(), percobaan[0])

    # Assert
    assert respons.status_code == 404
    assert respons.json()["code"] == "MANUAL_SESSION_NOT_FOUND"
