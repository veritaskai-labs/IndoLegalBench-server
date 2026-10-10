"""Test penugasan reviewer otomatis, reviews.service.assign_on_submit().

PBI-6 AC2 dan AC8, sub task SCRUM-144. Tabel review (SCRUM-143) dan
notifikasi (SCRUM-153) belum ada, jadi keduanya diganti fake lewat port.
Pengguna dan audit log memakai database uji yang asli.
"""

import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import pytest

from app.modules.audit.models import AuditLog
from app.modules.auth.models import User
from app.modules.reviews import service
from app.modules.reviews.ports import NoopNotifier, ReviewerLoad
from app.shared import request_context
from app.shared.security import Role

CASE_ID = uuid.UUID("00000000-0000-0000-0000-0000000144c1")
AWAL = datetime(2026, 10, 1, tzinfo=UTC)


@dataclass
class _Penugasan:
    reviewer_id: uuid.UUID
    round_id: uuid.UUID
    assigned_at: datetime
    status: str = "active"
    sudah_dinilai: bool = False


@dataclass
class FakeStore:
    """Meniru kontrak AssignmentStore di atas tabel SCRUM-143."""

    penugasan: list[_Penugasan] = field(default_factory=list)
    perlu_penugasan: list[uuid.UUID] = field(default_factory=list)
    jam: datetime = AWAL

    def tambah(self, reviewer_id: uuid.UUID, **kolom) -> None:
        self.jam += timedelta(minutes=1)
        self.penugasan.append(
            _Penugasan(
                reviewer_id=reviewer_id, round_id=uuid.uuid4(), assigned_at=self.jam, **kolom
            )
        )

    def open_queue(self, reviewer_ids):
        hasil = {}
        for reviewer_id in reviewer_ids:
            milik = [p for p in self.penugasan if p.reviewer_id == reviewer_id]
            terbuka = [p for p in milik if p.status == "active" and not p.sudah_dinilai]
            terakhir = max((p.assigned_at for p in milik), default=None)
            hasil[reviewer_id] = ReviewerLoad(
                open_count=len(terbuka), last_assigned_at=terakhir, total_count=len(milik)
            )
        return hasil

    def save_assignments(self, round_id, reviewer_ids):
        self.jam += timedelta(minutes=1)
        for reviewer_id in reviewer_ids:
            self.penugasan.append(
                _Penugasan(reviewer_id=reviewer_id, round_id=round_id, assigned_at=self.jam)
            )

    def mark_needs_assignment(self, round_id):
        self.perlu_penugasan.append(round_id)

    def round(self, round_id: uuid.UUID) -> list[uuid.UUID]:
        return [p.reviewer_id for p in self.penugasan if p.round_id == round_id]


@dataclass
class FakeNotifier:
    panggilan: list[tuple[list[uuid.UUID], str, dict]] = field(default_factory=list)

    def notify(self, user_ids, type, payload):
        self.panggilan.append((list(user_ids), type, dict(payload)))


def _user_id(nomor: int) -> uuid.UUID:
    return uuid.UUID(f"00000000-0000-0000-0000-{nomor:012d}")


@pytest.fixture
def buat_user(db_session):
    def buat(nomor: int, role: Role = Role.REVIEWER, *, is_active: bool = True) -> uuid.UUID:
        user = User(
            id=_user_id(nomor),
            email=f"user{nomor}@veritask.test",
            name=f"User {nomor}",
            role=role,
            is_active=is_active,
        )
        db_session.add(user)
        db_session.commit()
        return user.id

    return buat


@pytest.fixture
def store() -> FakeStore:
    return FakeStore()


@pytest.fixture
def notifier() -> FakeNotifier:
    return FakeNotifier()


def _tugaskan(db, store, notifier, *, author_id: uuid.UUID, round_id: uuid.UUID | None = None):
    return service.assign_on_submit(
        db,
        round_id=round_id or uuid.uuid4(),
        case_id=CASE_ID,
        author_id=author_id,
        store=store,
        notifier=notifier,
    )


def _baris_audit(db) -> list[AuditLog]:
    return db.query(AuditLog).filter(AuditLog.action == "review.assigned").all()


def test_penulis_tidak_pernah_terpilih_walau_reviewer_aktif(db_session, buat_user, store, notifier):
    # Arrange
    penulis = buat_user(1)
    reviewer = [buat_user(2), buat_user(3)]

    # Act
    terpilih = _tugaskan(db_session, store, notifier, author_id=penulis)

    # Assert
    assert penulis not in terpilih
    assert sorted(terpilih) == sorted(reviewer)


def test_penulis_tidak_terpilih_di_banyak_pengajuan(db_session, buat_user, store, notifier):
    # Arrange
    penulis = buat_user(1)
    for nomor in range(2, 6):
        buat_user(nomor)

    # Act
    semua = [_tugaskan(db_session, store, notifier, author_id=penulis) for _ in range(10)]

    # Assert
    assert all(penulis not in terpilih for terpilih in semua)


def test_nonaktif_dan_bukan_reviewer_tidak_terpilih(db_session, buat_user, store, notifier):
    # Arrange: yang tidak memenuhi syarat sengaja memakai id terkecil
    nonaktif = buat_user(2, is_active=False)
    admin = buat_user(3, Role.ADMIN)
    viewer = buat_user(4, Role.VIEWER)
    penulis = buat_user(5, Role.AUTHOR)
    aktif = [buat_user(6), buat_user(7)]

    # Act
    terpilih = _tugaskan(db_session, store, notifier, author_id=penulis)

    # Assert
    assert sorted(terpilih) == sorted(aktif)
    assert not {nonaktif, admin, viewer} & set(terpilih)


def test_dua_antrean_terkecil_menang(db_session, buat_user, store, notifier):
    # Arrange
    penulis = buat_user(1, Role.AUTHOR)
    sibuk, sedang, luang_a, luang_b = (buat_user(n) for n in range(2, 6))
    for _ in range(3):
        store.tambah(sibuk)
    store.tambah(sedang)
    store.tambah(sedang)
    store.tambah(luang_a)

    # Act
    terpilih = _tugaskan(db_session, store, notifier, author_id=penulis)

    # Assert
    assert sorted(terpilih) == sorted([luang_a, luang_b])


def test_penugasan_yang_sudah_dinilai_tidak_masuk_antrean(db_session, buat_user, store, notifier):
    # Arrange
    penulis = buat_user(1, Role.AUTHOR)
    sudah_menilai, menunggu = buat_user(2), buat_user(3)
    store.tambah(sudah_menilai, sudah_dinilai=True)
    store.tambah(sudah_menilai, status="replaced")
    store.tambah(sudah_menilai, status="void")
    store.tambah(menunggu)
    belum_pernah = buat_user(4)

    # Act
    terpilih = _tugaskan(db_session, store, notifier, author_id=penulis)

    # Assert
    assert sorted(terpilih) == sorted([sudah_menilai, belum_pernah])


def test_seri_belum_pernah_ditugaskan_lebih_dulu(db_session, buat_user, store, notifier):
    # Arrange
    penulis = buat_user(1, Role.AUTHOR)
    pernah = buat_user(2)
    store.tambah(pernah, sudah_dinilai=True)
    belum = [buat_user(3), buat_user(4)]

    # Act
    terpilih = _tugaskan(db_session, store, notifier, author_id=penulis)

    # Assert
    assert sorted(terpilih) == sorted(belum)


def test_seri_penugasan_terakhir_paling_lama_lebih_dulu(db_session, buat_user, store, notifier):
    # Arrange
    penulis = buat_user(1, Role.AUTHOR)
    paling_lama, menengah, terbaru = buat_user(2), buat_user(3), buat_user(4)
    store.tambah(paling_lama, sudah_dinilai=True)
    store.tambah(menengah, sudah_dinilai=True)
    store.tambah(terbaru, sudah_dinilai=True)

    # Act
    terpilih = _tugaskan(db_session, store, notifier, author_id=penulis)

    # Assert
    assert terpilih == [paling_lama, menengah]


def test_seri_penuh_memakai_id_terkecil(db_session, buat_user, store, notifier):
    # Arrange
    penulis = buat_user(1, Role.AUTHOR)
    buat_user(9)
    buat_user(7)
    buat_user(8)

    # Act
    terpilih = _tugaskan(db_session, store, notifier, author_id=penulis)

    # Assert
    assert terpilih == [_user_id(7), _user_id(8)]


def test_seri_waktu_memakai_total_tersedikit_sebelum_id(db_session, buat_user, store, notifier):
    """Pasangan satu round punya waktu yang sama. Id kecil tidak boleh terus menang."""
    # Arrange: satu kursi tersisa untuk sepasang reviewer dengan waktu yang sama
    penulis = buat_user(1, Role.AUTHOR)
    sering, jarang, baru = buat_user(2), buat_user(3), buat_user(4)
    store.tambah(sering, sudah_dinilai=True)
    store.tambah(sering, sudah_dinilai=True)
    store.save_assignments(uuid.uuid4(), [sering, jarang])
    for penugasan in store.penugasan:
        penugasan.sudah_dinilai = True

    # Act
    terpilih = _tugaskan(db_session, store, notifier, author_id=penulis)

    # Assert
    assert terpilih == [baru, jarang]


@pytest.mark.parametrize("jumlah_reviewer", [3, 5, 7])
def test_pemilihan_merata_di_kelompok_yang_setara(
    db_session, buat_user, store, notifier, jumlah_reviewer
):
    """Penugasan dinilai sebelum pengajuan berikutnya, jadi hanya urutan seri yang bekerja."""
    # Arrange
    penulis = buat_user(1, Role.AUTHOR)
    reviewer = [buat_user(n) for n in range(2, 2 + jumlah_reviewer)]

    # Act
    for _ in range(30):
        _tugaskan(db_session, store, notifier, author_id=penulis)
        for penugasan in store.penugasan:
            penugasan.sudah_dinilai = True

    # Assert
    jumlah = Counter(p.reviewer_id for p in store.penugasan)
    assert set(jumlah) == set(reviewer)
    assert max(jumlah.values()) - min(jumlah.values()) <= 1


@pytest.mark.parametrize("jumlah_reviewer", [0, 1])
def test_kurang_dari_dua_masuk_antrean_penugasan(
    db_session, buat_user, store, notifier, jumlah_reviewer
):
    # Arrange
    penulis = buat_user(1, Role.AUTHOR)
    for nomor in range(2, 2 + jumlah_reviewer):
        buat_user(nomor)
    round_id = uuid.uuid4()

    # Act
    terpilih = _tugaskan(db_session, store, notifier, author_id=penulis, round_id=round_id)
    db_session.commit()

    # Assert
    assert terpilih == []
    assert store.perlu_penugasan == [round_id]
    assert store.penugasan == []
    assert notifier.panggilan == []
    assert _baris_audit(db_session) == []


def test_penulis_reviewer_dan_satu_reviewer_lain_masuk_antrean(
    db_session, buat_user, store, notifier
):
    """Edge SCRUM-151: hanya 2 reviewer aktif dan salah satunya penulis."""
    # Arrange
    penulis = buat_user(1)
    buat_user(2)
    round_id = uuid.uuid4()

    # Act
    terpilih = _tugaskan(db_session, store, notifier, author_id=penulis, round_id=round_id)

    # Assert
    assert terpilih == []
    assert store.perlu_penugasan == [round_id]


def test_dua_terpilih_disimpan_dinotifikasi_dan_diaudit(db_session, buat_user, store, notifier):
    # Arrange
    penulis = buat_user(1, Role.AUTHOR)
    reviewer = [buat_user(2), buat_user(3)]
    round_id = uuid.uuid4()
    request_context.bind(db_session, user_id=penulis, role=Role.AUTHOR)

    # Act
    terpilih = _tugaskan(db_session, store, notifier, author_id=penulis, round_id=round_id)
    db_session.commit()

    # Assert
    assert terpilih == reviewer
    assert store.round(round_id) == reviewer
    assert store.perlu_penugasan == []

    assert len(notifier.panggilan) == 1
    user_ids, jenis, payload = notifier.panggilan[0]
    assert user_ids == reviewer
    assert jenis == "review_assigned"
    assert set(payload) == {"title", "body", "link"}
    assert payload["link"] == f"/cases/{CASE_ID}"

    [baris] = _baris_audit(db_session)
    assert baris.entity_type == "review"
    assert baris.entity_id == round_id
    assert baris.case_id == CASE_ID
    assert baris.after == {"reviewer_ids": [str(r) for r in reviewer]}
    assert baris.actor_user_id == penulis
    assert baris.actor_role == "author"


def test_tidak_commit_sendiri(db_session, buat_user, store, notifier):
    """Submit SCRUM-143 memegang transaksi: gagal di sana, audit penugasan ikut batal."""
    # Arrange
    penulis = buat_user(1, Role.AUTHOR)
    buat_user(2)
    buat_user(3)

    # Act
    _tugaskan(db_session, store, notifier, author_id=penulis)
    db_session.rollback()

    # Assert
    assert _baris_audit(db_session) == []


def test_noop_notifier_menerima_panggilan_tanpa_efek():
    # Act + Assert
    assert NoopNotifier().notify([uuid.uuid4()], "review_assigned", {"title": "t"}) is None
