"""Model ORM bukti jawaban manual di SQLite. PBI-22 (SCRUM-176) AC7; SCRUM-233.

D9 (ERD Jawaban Manual), tabel attempt_evidence: satu baris per bukti, berkas atau tautan.
CHECK (kind = 'file' => file_key, mime_type NOT NULL) AND (kind = 'link' => url NOT NULL).
mime_type hanya image/png, image/jpeg, application/pdf.

Isi berkas disimpan di tabel evidence_files (keputusan SA 7 November 2026: API tidak punya
volume, dan backup hanya PostgreSQL), dirujuk lewat file_key. Batas 3 per jawaban dan
larangan UPDATE dijaga trigger PostgreSQL, diuji di test_manual_evidence_migration_pg.py.

Label: Positif = perilaku yang diharapkan, Negatif = ditolak, Edge = batas perilaku.
"""

import importlib
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.modules.runs.models import Attempt
from tests.modules.runs.test_manual_session_detail import _sesi_manual
from tests.modules.runs.test_manual_session_endpoint import ADMIN_LAIN


@pytest.fixture
def m():
    # Lewat fixture: model yang belum ada membuat tes merah, bukan galat koleksi.
    return importlib.import_module("app.modules.runs.models")


@pytest.fixture
def percobaan(siap_manual) -> Attempt:
    sesi_id = _sesi_manual(siap_manual, repeat_count=1)
    return siap_manual.db.scalars(select(Attempt).where(Attempt.session_id == sesi_id)).first()


def _berkas(m, percobaan, **ubah):
    isi = {
        "attempt_id": percobaan.id,
        "kind": m.EvidenceKind.FILE,
        "file_key": str(uuid.uuid4()),
        "file_name": "tangkapan.png",
        "mime_type": "image/png",
        "size_bytes": 2048,
        "uploaded_by": ADMIN_LAIN,
    }
    isi.update(ubah)
    return m.AttemptEvidence(**isi)


def _tautan(m, percobaan, **ubah):
    isi = {
        "attempt_id": percobaan.id,
        "kind": m.EvidenceKind.LINK,
        "url": "https://chat.example.com/share/abc",
        "uploaded_by": ADMIN_LAIN,
    }
    isi.update(ubah)
    return m.AttemptEvidence(**isi)


def test_bukti_berkas_dan_tautan_tersimpan(siap_manual, m, percobaan):
    """Positif: AC7, D9 dua jenis bukti; waktu unggah diisi database."""
    # Arrange
    db = siap_manual.db

    # Act
    db.add_all([_berkas(m, percobaan), _tautan(m, percobaan)])
    db.commit()
    baris = db.scalars(select(m.AttemptEvidence).order_by(m.AttemptEvidence.kind)).all()

    # Assert
    assert [b.kind for b in baris] == ["file", "link"]
    assert all(b.uploaded_at is not None for b in baris)


@pytest.mark.parametrize(
    ("pembuat", "ubah"),
    [
        # Negatif: berkas tanpa lokasi penyimpanan atau tanpa tipe.
        ("berkas", {"file_key": None}),
        ("berkas", {"mime_type": None}),
        ("berkas", {"size_bytes": None}),
        # Negatif: berkas yang sekaligus membawa tautan tidak jelas jenisnya.
        ("berkas", {"url": "https://example.com"}),
        # Negatif: tautan tanpa url, atau membawa kolom berkas.
        ("tautan", {"url": None}),
        ("tautan", {"file_key": "kunci"}),
        # Negatif: tipe di luar PNG/JPG/PDF tidak pernah tersimpan.
        ("berkas", {"mime_type": "image/gif"}),
        # Edge: ukuran nol tidak mungkin dari berkas sah.
        ("berkas", {"size_bytes": 0}),
    ],
    ids=[
        "berkas-tanpa-key",
        "berkas-tanpa-mime",
        "berkas-tanpa-ukuran",
        "berkas-dengan-url",
        "tautan-tanpa-url",
        "tautan-dengan-key",
        "mime-gif",
        "ukuran-nol",
    ],
)
def test_bukti_yang_kolomnya_tidak_cocok_jenis_ditolak(siap_manual, m, percobaan, pembuat, ubah):
    """Negatif: AC7, CHECK D9 menjaga kolom berkas dan tautan sesuai jenisnya."""
    # Arrange
    buat = _berkas if pembuat == "berkas" else _tautan
    siap_manual.db.add(buat(m, percobaan, **ubah))

    # Act / Assert
    with pytest.raises(IntegrityError):
        siap_manual.db.commit()


def test_isi_berkas_tersimpan_utuh(siap_manual, m):
    """Positif: AC7, isi berkas bukti dibaca kembali byte per byte."""
    # Arrange
    isi = b"\x89PNG\r\n\x1a\n" + bytes(range(256))
    db = siap_manual.db

    # Act
    db.add(m.EvidenceFile(key="kunci-1", content=isi))
    db.commit()
    db.expire_all()

    # Assert
    assert db.get(m.EvidenceFile, "kunci-1").content == isi
