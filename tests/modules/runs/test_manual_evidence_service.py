"""ManualEvidenceService dengan penyimpanan palsu. PBI-22 (SCRUM-176) AC7, AC9; SCRUM-233.

D9 (ERD Jawaban Manual), attempt_evidence: bukti opsional satu jawaban manual, berkas
PNG/JPG/PDF atau tautan, maksimal 3. Isi berkas lewat port EvidenceStorage (tabel
evidence_files), baris bukti lewat port EvidenceStore. Setiap tambah dan hapus bukti
tercatat di audit_logs dengan label manual (AC9, PBI-18), tanpa isi berkas maupun tautan.

Bukti bisa ditambah atau dihapus sampai sesi Selesai (AC8). Siapa yang boleh melihat
bukti mengikuti flag MANUAL_EVIDENCE_VISIBILITY (AC7, TBA).

Jalur ke tabel dan HTTP diuji di test_manual_evidence_endpoint.py.

Label: Positif = perilaku yang diharapkan, Negatif = ditolak, Edge = batas perilaku.
"""

import uuid
from datetime import UTC, datetime

import pytest

from app.modules.runs import session_ports
from app.modules.runs.models import SessionStatus
from app.shared.exceptions import ConflictError, NotFoundError, ValidationError
from app.shared.security import Role

SESI = uuid.UUID("00000000-0000-0000-0000-0000000000e5")
ITEM = uuid.UUID("00000000-0000-0000-0000-00000000000a")
PERCOBAAN = uuid.UUID("00000000-0000-0000-0000-0000000000a1")
ADMIN = uuid.UUID("00000000-0000-0000-0000-0000000000d4")
KONTRIBUTOR = uuid.UUID("00000000-0000-0000-0000-0000000000c7")
JAM = datetime(2026, 11, 7, 10, 0, tzinfo=UTC)
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


class _Penyimpan:
    """EvidenceStore palsu: status sesi, satu percobaan, baris bukti, catatan audit."""

    def __init__(self, status="partially_filled", *, ada_sesi=True, ada_percobaan=True):
        self.status = SessionStatus(status) if ada_sesi else None
        self.ada_percobaan = ada_percobaan
        self.bukti: list[session_ports.EvidenceRow] = []
        self.audit: list[tuple[str, dict]] = []
        self.dikunci: list[bool] = []
        self.percobaan_dikunci: list[bool] = []
        self.commit_ke = 0

    def session_status(self, session_id, *, lock):
        self.dikunci.append(lock)
        return self.status

    def find_attempt(self, session_id, snapshot_item_id, attempt_no, *, lock=False):
        self.percobaan_dikunci.append(lock)
        if not self.ada_percobaan:
            return None
        return session_ports.EvidenceTarget(
            attempt_id=PERCOBAAN, snapshot_item_id=snapshot_item_id, repetition_no=attempt_no
        )

    def evidence_for(self, attempt_id):
        return [b for b in self.bukti if b.attempt_id == attempt_id]

    def add(self, new):
        baris = session_ports.EvidenceRow(
            id=uuid.uuid4(),
            attempt_id=new.attempt_id,
            kind=new.kind,
            file_key=new.file_key,
            file_name=new.file_name,
            mime_type=new.mime_type,
            size_bytes=new.size_bytes,
            url=new.url,
            uploaded_by=new.uploaded_by,
            uploaded_at=JAM,
        )
        self.bukti.append(baris)
        return baris

    def find_evidence(self, session_id, evidence_id):
        for b in self.bukti:
            if b.id == evidence_id:
                return b, session_ports.EvidenceTarget(
                    attempt_id=b.attempt_id, snapshot_item_id=ITEM, repetition_no=1
                )
        return None

    def remove(self, evidence_id):
        self.bukti = [b for b in self.bukti if b.id != evidence_id]

    def record(self, session_id, action, *, before=None, after=None):
        # Tambah tercatat di after, hapus di before (pola audit PBI-18).
        self.audit.append((action, after if after is not None else before))
        self.sisi = "after" if after is not None else "before"

    def commit(self):
        self.commit_ke += 1


class _Berkas:
    """EvidenceStorage palsu di memori."""

    def __init__(self):
        self.isi: dict[str, bytes] = {}

    def put(self, content):
        key = uuid.uuid4().hex
        self.isi[key] = content
        return key

    def read(self, key):
        return self.isi.get(key)

    def delete(self, key):
        self.isi.pop(key, None)


@pytest.fixture
def buat():
    # Diimpor lewat fixture: kelas yang belum ada membuat tes merah, bukan galat koleksi.
    from app.modules.runs.manual_evidence import ManualEvidenceService

    def _buat(penyimpan=None, berkas=None, *, visibility="session_viewers", max_bytes=1024):
        penyimpan = penyimpan or _Penyimpan()
        berkas = berkas or _Berkas()
        layanan = ManualEvidenceService(
            penyimpan, berkas, max_bytes=max_bytes, visibility=visibility
        )
        return layanan, penyimpan, berkas

    return _buat


def _unggah(layanan, isi=PNG, tipe="image/png", nama="bukti.png"):
    return layanan.add_file(
        SESI, ITEM, 1, content=isi, declared_type=tipe, file_name=nama, actor=ADMIN
    )


def test_unggah_berkas_menyimpan_isi_baris_dan_audit(buat):
    """Positif: AC7, AC9, berkas tersimpan, baris bukti dibuat, audit berlabel manual."""
    # Arrange
    layanan, penyimpan, berkas = buat()

    # Act
    hasil = _unggah(layanan)

    # Assert
    assert (hasil.kind, hasil.mime_type, hasil.size_bytes) == ("file", "image/png", len(PNG))
    assert list(berkas.isi.values()) == [PNG]
    assert penyimpan.bukti[0].file_key in berkas.isi
    aksi, isi = penyimpan.audit[0]
    assert aksi == "measurement_session.evidence_added"
    assert isi["source"] == "manual"
    assert (isi["attempt_id"], isi["snapshot_item_id"], isi["repetition_no"]) == (
        str(PERCOBAAN),
        str(ITEM),
        1,
    )
    assert penyimpan.commit_ke == 1
    # Baris sesi dikunci: tidak bisa lolos bersamaan dengan Tandai selesai.
    assert penyimpan.dikunci == [True]


def test_audit_tidak_memuat_isi_berkas_maupun_tautan(buat):
    """Negatif: PBI-18, isi bukti tetap di tabelnya; audit hanya jejak siapa dan apa."""
    # Arrange
    layanan, penyimpan, _ = buat()

    # Act
    _unggah(layanan)
    layanan.add_link(SESI, ITEM, 1, url="https://chat.example.com/share/rahasia", actor=ADMIN)

    # Assert
    teks = repr(penyimpan.audit)
    assert "rahasia" not in teks
    assert "PNG" not in teks


def test_tautan_disimpan_rapi(buat):
    """Positif: AC7, tautan percakapan disimpan tanpa spasi tepi."""
    # Arrange
    layanan, penyimpan, _ = buat()

    # Act
    hasil = layanan.add_link(SESI, ITEM, 1, url="  https://x.test/a  ", actor=ADMIN)

    # Assert
    assert (hasil.kind, hasil.url, hasil.file_name) == ("link", "https://x.test/a", None)
    assert penyimpan.audit[0][1]["kind"] == "link"


@pytest.mark.parametrize(
    ("isi", "tipe", "kode"),
    [
        (b"GIF89a" + b"\x00" * 8, "image/gif", "EVIDENCE_TYPE_NOT_ALLOWED"),
        (b"%PDF-1.7", "image/png", "EVIDENCE_TYPE_MISMATCH"),
        (b"", "image/png", "EVIDENCE_EMPTY"),
        (PNG + b"\x00" * 2000, "image/png", "EVIDENCE_TOO_LARGE"),
    ],
    ids=["gif", "pdf-berlabel-png", "kosong", "lewat-batas"],
)
def test_berkas_tidak_sah_ditolak_tanpa_menyimpan_apa_pun(buat, isi, tipe, kode):
    """Negatif: AC7, validasi di server sebelum sesi dikunci atau berkas disimpan."""
    # Arrange
    layanan, penyimpan, berkas = buat()

    # Act
    with pytest.raises(ValidationError) as galat:
        _unggah(layanan, isi=isi, tipe=tipe)

    # Assert
    assert galat.value.code == kode
    assert (berkas.isi, penyimpan.bukti, penyimpan.audit, penyimpan.dikunci) == ({}, [], [], [])


def test_bukti_keempat_ditolak_dan_berkas_tidak_tersimpan(buat):
    """Negatif: AC7, D9 maksimal 3 bukti per jawaban."""
    # Arrange
    layanan, penyimpan, berkas = buat()
    for _ in range(3):
        layanan.add_link(SESI, ITEM, 1, url="https://x.test", actor=ADMIN)

    # Act
    with pytest.raises(ConflictError) as galat:
        _unggah(layanan)

    # Assert
    assert galat.value.code == "EVIDENCE_LIMIT_REACHED"
    assert berkas.isi == {}


@pytest.mark.parametrize(
    ("penyimpan", "galat", "kode"),
    [
        (_Penyimpan(ada_sesi=False), NotFoundError, "MANUAL_SESSION_NOT_FOUND"),
        (_Penyimpan(ada_percobaan=False), NotFoundError, "MANUAL_ATTEMPT_NOT_FOUND"),
        (_Penyimpan(status="finalized"), ConflictError, "MANUAL_SESSION_FINALIZED"),
        (_Penyimpan(status="running"), ConflictError, "MANUAL_SESSION_STATUS_INVALID"),
    ],
    ids=["sesi-tidak-ada", "percobaan-tidak-ada", "sudah-selesai", "status-d4"],
)
def test_tambah_bukti_ditolak_di_luar_jawaban_yang_masih_terbuka(buat, penyimpan, galat, kode):
    """Negatif: AC8, bukti seperti jawaban: tidak bisa diubah setelah sesi Selesai."""
    # Arrange
    layanan, _, berkas = buat(penyimpan)

    # Act
    with pytest.raises(galat) as tertangkap:
        _unggah(layanan)

    # Assert
    assert tertangkap.value.code == kode
    assert (berkas.isi, penyimpan.bukti) == ({}, [])


def test_daftar_bukti_satu_jawaban(buat):
    """Positif: AC7, bukti tampil di kartu jawaban, urut waktu unggah."""
    # Arrange
    layanan, penyimpan, _ = buat()
    _unggah(layanan)
    layanan.add_link(SESI, ITEM, 1, url="https://x.test", actor=ADMIN)

    # Act
    hasil = layanan.list(SESI, ITEM, 1, role=Role.CONTRIBUTOR)

    # Assert
    assert [b.kind for b in hasil] == ["file", "link"]
    # Membaca tidak perlu mengunci sesi.
    assert penyimpan.dikunci[-1] is False


def test_daftar_bukti_sesi_selesai_tetap_terbaca(buat):
    """Edge: AC8, Selesai mengunci perubahan, bukan membaca."""
    # Arrange
    penyimpan = _Penyimpan()
    layanan, _, _ = buat(penyimpan)
    _unggah(layanan)
    penyimpan.status = SessionStatus.FINALIZED

    # Act / Assert
    assert len(layanan.list(SESI, ITEM, 1, role=Role.ADMIN)) == 1


@pytest.mark.parametrize(
    ("visibility", "peran", "boleh"),
    [
        ("session_viewers", Role.CONTRIBUTOR, True),
        ("admin_only", Role.CONTRIBUTOR, False),
        ("admin_only", Role.ADMIN, True),
    ],
)
def test_melihat_bukti_mengikuti_flag(buat, visibility, peran, boleh):
    """Positif/Negatif: AC7, siapa yang boleh melihat bukti masih TBA, jadi dibuat flag."""
    # Arrange
    layanan, _, _ = buat(visibility=visibility)
    bukti = _unggah(layanan)

    # Act / Assert
    if boleh:
        assert layanan.list(SESI, ITEM, 1, role=peran)
        assert layanan.download(SESI, bukti.id, role=peran).content == PNG
    else:
        for aksi in (
            lambda: layanan.list(SESI, ITEM, 1, role=peran),
            lambda: layanan.download(SESI, bukti.id, role=peran),
            lambda: layanan.delete(SESI, bukti.id, role=peran, actor=KONTRIBUTOR),
        ):
            with pytest.raises(NotFoundError) as galat:
                aksi()
            # 404, bukan 403: keberadaan bukti tidak dibocorkan ke yang tidak berhak.
            assert galat.value.code == "EVIDENCE_NOT_FOUND"


def test_unduh_berkas_membawa_tipe_dan_nama(buat):
    """Positif: AC7, unduhan memakai tipe dari isi berkas dan nama yang sudah dirapikan."""
    # Arrange
    layanan, _, _ = buat()
    bukti = _unggah(layanan, nama="../../etc/passwd\r\n.png")

    # Act
    hasil = layanan.download(SESI, bukti.id, role=Role.ADMIN)

    # Assert
    assert (hasil.content, hasil.mime_type) == (PNG, "image/png")
    assert "/" not in hasil.file_name and "\n" not in hasil.file_name
    assert hasil.file_name.endswith(".png")


@pytest.mark.parametrize("jenis", ["tautan", "tidak-ada", "isi-hilang"])
def test_unduh_tanpa_berkas_404(buat, jenis):
    """Negatif: AC7, tautan tidak punya berkas; id asing atau isi yang hilang juga 404."""
    # Arrange
    layanan, penyimpan, berkas = buat()
    if jenis == "tautan":
        sasaran = layanan.add_link(SESI, ITEM, 1, url="https://x.test", actor=ADMIN).id
    elif jenis == "tidak-ada":
        sasaran = uuid.uuid4()
    else:
        sasaran = _unggah(layanan).id
        berkas.isi.clear()

    # Act
    with pytest.raises(NotFoundError) as galat:
        layanan.download(SESI, sasaran, role=Role.ADMIN)

    # Assert
    assert galat.value.code == "EVIDENCE_NOT_FOUND"


def test_hapus_bukti_menghapus_berkas_dan_tercatat(buat):
    """Positif: AC7, AC9, bukti yang salah dihapus; berkasnya ikut hilang, audit mencatat."""
    # Arrange
    layanan, penyimpan, berkas = buat()
    bukti = _unggah(layanan)

    # Act
    layanan.delete(SESI, bukti.id, role=Role.CONTRIBUTOR, actor=KONTRIBUTOR)

    # Assert
    assert (penyimpan.bukti, berkas.isi) == ([], {})
    assert penyimpan.audit[-1][0] == "measurement_session.evidence_removed"
    assert penyimpan.audit[-1][1]["evidence_id"] == str(bukti.id)
    assert penyimpan.sisi == "before"
    assert penyimpan.commit_ke == 2


def test_hapus_bukti_sesi_selesai_ditolak(buat):
    """Negatif: AC8, bukti sesi Selesai tidak bisa dihapus."""
    # Arrange
    penyimpan = _Penyimpan()
    layanan, _, berkas = buat(penyimpan)
    bukti = _unggah(layanan)
    penyimpan.status = SessionStatus.FINALIZED

    # Act
    with pytest.raises(ConflictError) as galat:
        layanan.delete(SESI, bukti.id, role=Role.ADMIN, actor=ADMIN)

    # Assert
    assert galat.value.code == "MANUAL_SESSION_FINALIZED"
    assert len(berkas.isi) == 1


def test_daftar_bukti_sesi_yang_tidak_ada_404(buat):
    """Negatif: id yang bukan sesi manual."""
    # Arrange
    layanan, _, _ = buat(_Penyimpan(ada_sesi=False))

    # Act
    with pytest.raises(NotFoundError) as galat:
        layanan.list(SESI, ITEM, 1, role=Role.ADMIN)

    # Assert
    assert galat.value.code == "MANUAL_SESSION_NOT_FOUND"


def test_hapus_bukti_yang_tidak_ada_404(buat):
    """Negatif: id bukti asing (atau milik sesi lain) tidak menghapus apa pun."""
    # Arrange
    layanan, penyimpan, _ = buat()
    _unggah(layanan)

    # Act
    with pytest.raises(NotFoundError) as galat:
        layanan.delete(SESI, uuid.uuid4(), role=Role.ADMIN, actor=ADMIN)

    # Assert
    assert galat.value.code == "EVIDENCE_NOT_FOUND"
    assert len(penyimpan.bukti) == 1


# Review #121.


def test_jalur_tulis_mengunci_percobaan_jalur_baca_tidak(buat):
    """Positif: review #121 blocker 1, hitungan 3 bukti diserialkan lewat kunci baris attempt.

    Kunci sesi FOR SHARE tidak saling menahan, jadi tanpa kunci attempt dua unggahan
    bersamaan sama-sama melihat 2 bukti dan yang kalah mendapat 500 dari trigger.
    """
    # Arrange
    layanan, penyimpan, _ = buat()

    # Act
    _unggah(layanan)
    layanan.add_link(SESI, ITEM, 1, url="https://x.test", actor=ADMIN)
    layanan.list(SESI, ITEM, 1, role=Role.ADMIN)

    # Assert
    assert penyimpan.percobaan_dikunci == [True, True, False]


def test_pengunggah_boleh_menghapus_buktinya_sendiri_walau_flag_admin_saja(buat):
    """Positif: review #121, bukti salah unggah tidak terjebak saat Kontributor tidak boleh melihat."""
    # Arrange
    layanan, penyimpan, berkas = buat(visibility="admin_only")
    bukti = layanan.add_file(
        SESI,
        ITEM,
        1,
        content=PNG,
        declared_type="image/png",
        file_name="salah.png",
        actor=KONTRIBUTOR,
    )

    # Act
    layanan.delete(SESI, bukti.id, role=Role.CONTRIBUTOR, actor=KONTRIBUTOR)

    # Assert
    assert (penyimpan.bukti, berkas.isi) == ([], {})


def test_bukti_orang_lain_tetap_tersembunyi_saat_flag_admin_saja(buat):
    """Negatif: review #121, pengecualian hanya untuk pengunggahnya sendiri."""
    # Arrange
    layanan, penyimpan, _ = buat(visibility="admin_only")
    bukti = _unggah(layanan)

    # Act
    with pytest.raises(NotFoundError) as galat:
        layanan.delete(SESI, bukti.id, role=Role.CONTRIBUTOR, actor=KONTRIBUTOR)

    # Assert
    assert galat.value.code == "EVIDENCE_NOT_FOUND"
    assert len(penyimpan.bukti) == 1
