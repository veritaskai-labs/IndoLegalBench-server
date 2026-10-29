"""Test penugasan reviewer otomatis, reviews.service.assign_on_submit().

PBI-6 AC2 dan AC8, sub task SCRUM-144. Ranking dan antrean diuji lewat
FakeStore. Penyimpanan nyata (ReviewTableStore) dan audit D6a
(review.reviewer_assigned per assignment) diuji terpisah. Notifikasi
masih port (SCRUM-153).
"""

import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import pytest

from app.modules.audit.models import AuditLog
from app.modules.auth.models import User
from app.modules.reviews import service
from app.modules.reviews.ports import DeferredNotifier, NoopNotifier, ReviewerLoad
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
    id: uuid.UUID = field(default_factory=uuid.uuid4)


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
        baru = [
            _Penugasan(reviewer_id=reviewer_id, round_id=round_id, assigned_at=self.jam)
            for reviewer_id in reviewer_ids
        ]
        self.penugasan.extend(baru)
        return {tugas.reviewer_id: tugas.id for tugas in baru}

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
    def buat(nomor: int, role: Role = Role.CONTRIBUTOR, *, is_active: bool = True) -> uuid.UUID:
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
        author_id=author_id,
        store=store,
        notifier=notifier,
    )


def _baris_audit_lama(db) -> list[AuditLog]:
    """Bentuk lama review.assigned + reviewer_ids; harus hilang setelah D6a."""
    return db.query(AuditLog).filter(AuditLog.action == "review.assigned").all()


def _baris_reviewer_assigned(db) -> list[AuditLog]:
    return db.query(AuditLog).filter(AuditLog.action == "review.reviewer_assigned").all()


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
    penulis = buat_user(5, Role.CONTRIBUTOR)
    aktif = [buat_user(6), buat_user(7)]

    # Act
    terpilih = _tugaskan(db_session, store, notifier, author_id=penulis)

    # Assert
    assert sorted(terpilih) == sorted(aktif)
    assert not {nonaktif, admin, viewer} & set(terpilih)


def test_dua_antrean_terkecil_menang(db_session, buat_user, store, notifier):
    # Arrange
    penulis = buat_user(1, Role.CONTRIBUTOR)
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
    penulis = buat_user(1, Role.CONTRIBUTOR)
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
    penulis = buat_user(1, Role.CONTRIBUTOR)
    pernah = buat_user(2)
    store.tambah(pernah, sudah_dinilai=True)
    belum = [buat_user(3), buat_user(4)]

    # Act
    terpilih = _tugaskan(db_session, store, notifier, author_id=penulis)

    # Assert
    assert sorted(terpilih) == sorted(belum)


def test_seri_penugasan_terakhir_paling_lama_lebih_dulu(db_session, buat_user, store, notifier):
    # Arrange
    penulis = buat_user(1, Role.CONTRIBUTOR)
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
    penulis = buat_user(1, Role.CONTRIBUTOR)
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
    penulis = buat_user(1, Role.CONTRIBUTOR)
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
    penulis = buat_user(1, Role.CONTRIBUTOR)
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
    penulis = buat_user(1, Role.CONTRIBUTOR)
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
    assert _baris_audit_lama(db_session) == []
    assert _baris_reviewer_assigned(db_session) == []


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


def test_dua_terpilih_disimpan_dan_dinotifikasi_tanpa_audit_manual(
    db_session, buat_user, store, notifier
):
    """FakeStore tidak menulis baris ORM; audit D6a datang dari tracker saat store nyata."""
    # Arrange
    penulis = buat_user(1, Role.CONTRIBUTOR)
    reviewer = [buat_user(2), buat_user(3)]
    round_id = uuid.uuid4()
    request_context.bind(db_session, user_id=penulis, role=Role.CONTRIBUTOR)

    # Act
    terpilih = _tugaskan(db_session, store, notifier, author_id=penulis, round_id=round_id)
    db_session.commit()

    # Assert
    assert terpilih == reviewer
    assert store.round(round_id) == reviewer
    assert store.perlu_penugasan == []

    tugas = {p.reviewer_id: p.id for p in store.penugasan if p.round_id == round_id}
    assert [user_ids for user_ids, _, _ in notifier.panggilan] == [[r] for r in reviewer]
    for user_ids, jenis, payload in notifier.panggilan:
        assert jenis == "review_assigned"
        assert set(payload) == {"title", "body", "link"}
        assert payload["link"] == f"/reviews/{tugas[user_ids[0]]}"

    assert _baris_audit_lama(db_session) == []
    assert _baris_reviewer_assigned(db_session) == []


def test_store_nyata_membuka_round_dan_mencatat_satu_audit_per_assignment(
    db_session, buat_user, notifier
):
    """ReviewTableStore + D6a: round open, dua baris review.reviewer_assigned."""
    from app.modules.reviews.models import (
        AssignmentSource,
        ReviewAssignment,
        ReviewRound,
        RoundStatus,
    )

    # Arrange
    penulis = buat_user(1, Role.CONTRIBUTOR)
    reviewer = [buat_user(2), buat_user(3)]
    request_context.bind(db_session, user_id=penulis, role=Role.CONTRIBUTOR)
    ronde = ReviewRound(
        case_id=CASE_ID,
        case_version_id=uuid.uuid4(),
        round_no=1,
        status=RoundStatus.AWAITING_ASSIGNMENT,
    )
    db_session.add(ronde)
    db_session.flush()

    # Act
    terpilih = service.assign_on_submit(
        db_session,
        round_id=ronde.id,
        author_id=penulis,
        notifier=notifier,
    )
    db_session.commit()

    # Assert
    assert sorted(terpilih) == sorted(reviewer)
    db_session.refresh(ronde)
    assert ronde.status == RoundStatus.OPEN
    tugas = (
        db_session.query(ReviewAssignment)
        .filter(ReviewAssignment.review_round_id == ronde.id)
        .all()
    )
    assert {t.reviewer_id for t in tugas} == set(reviewer)
    assert all(t.source == AssignmentSource.SYSTEM for t in tugas)

    assert _baris_audit_lama(db_session) == []
    baris = _baris_reviewer_assigned(db_session)
    assert len(baris) == 2
    assert {b.entity_id for b in baris} == {t.id for t in tugas}
    assert all(b.entity_type == "review" for b in baris)
    assert all(b.case_id == CASE_ID for b in baris)
    assert {frozenset(b.after.items()) for b in baris} == {
        frozenset({"reviewer_id": str(r), "source": "system"}.items()) for r in reviewer
    }
    assert all(b.actor_user_id == penulis for b in baris)


def test_store_nyata_kurang_dari_dua_tetap_awaiting(db_session, buat_user, notifier):
    from app.modules.reviews.models import ReviewRound, RoundStatus

    # Arrange
    penulis = buat_user(1, Role.CONTRIBUTOR)
    buat_user(2)
    ronde = ReviewRound(
        case_id=CASE_ID,
        case_version_id=uuid.uuid4(),
        round_no=1,
        status=RoundStatus.AWAITING_ASSIGNMENT,
    )
    db_session.add(ronde)
    db_session.flush()

    # Act
    terpilih = service.assign_on_submit(
        db_session,
        round_id=ronde.id,
        author_id=penulis,
        notifier=notifier,
    )
    db_session.commit()

    # Assert
    assert terpilih == []
    db_session.refresh(ronde)
    assert ronde.status == RoundStatus.AWAITING_ASSIGNMENT
    assert _baris_reviewer_assigned(db_session) == []


def test_tidak_commit_sendiri(db_session, buat_user, notifier):
    """Submit SCRUM-143 memegang transaksi: gagal di sana, audit penugasan ikut batal."""
    from app.modules.reviews.models import ReviewRound, RoundStatus

    # Arrange
    penulis = buat_user(1, Role.CONTRIBUTOR)
    buat_user(2)
    buat_user(3)
    request_context.bind(db_session, user_id=penulis, role=Role.CONTRIBUTOR)
    ronde = ReviewRound(
        case_id=CASE_ID,
        case_version_id=uuid.uuid4(),
        round_no=1,
        status=RoundStatus.AWAITING_ASSIGNMENT,
    )
    db_session.add(ronde)
    db_session.flush()

    # Act
    service.assign_on_submit(
        db_session,
        round_id=ronde.id,
        author_id=penulis,
        notifier=notifier,
    )
    db_session.rollback()

    # Assert
    assert _baris_audit_lama(db_session) == []
    assert _baris_reviewer_assigned(db_session) == []


def test_store_nyata_open_queue_menghitung_beban_dengan_benar(db_session, buat_user):
    """ReviewTableStore.open_queue: active tanpa verdict = open; judged/replaced/void tidak.

    Fairness tests memakai FakeStore. Aturan hitungan store nyata harus
    terpasang sendiri (catatan review Astha di #38).
    """
    from app.modules.reviews.models import (
        AssignmentSource,
        AssignmentStatus,
        ReviewAssignment,
        ReviewRound,
        ReviewVerdict,
        RoundStatus,
        VerdictDecision,
    )

    # Arrange
    penulis = buat_user(1, Role.CONTRIBUTOR)
    ringan = buat_user(2)
    berat = buat_user(3)
    jam = AWAL

    def _ronde(nomor: int) -> ReviewRound:
        ronde = ReviewRound(
            case_id=CASE_ID,
            case_version_id=uuid.uuid4(),
            round_no=nomor,
            status=RoundStatus.OPEN,
        )
        db_session.add(ronde)
        db_session.flush()
        return ronde

    def _tugas(
        ronde: ReviewRound,
        reviewer_id: uuid.UUID,
        *,
        status: AssignmentStatus = AssignmentStatus.ACTIVE,
        assigned_at: datetime | None = None,
        verdict: VerdictDecision | None = None,
    ) -> None:
        nonlocal jam
        jam = assigned_at or (jam + timedelta(minutes=1))
        tugas = ReviewAssignment(
            review_round_id=ronde.id,
            reviewer_id=reviewer_id,
            source=AssignmentSource.SYSTEM,
            status=status,
            assigned_at=jam,
        )
        tugas.review_round = ronde
        db_session.add(tugas)
        db_session.flush()
        if verdict is not None:
            db_session.add(ReviewVerdict(assignment_id=tugas.id, decision=verdict, comment=None))
            db_session.flush()

    # ringan: one judged (not open) + one replaced (not open); last = later row
    r1, r2 = _ronde(1), _ronde(2)
    _tugas(r1, ringan, verdict=VerdictDecision.APPROVE, assigned_at=AWAL + timedelta(hours=1))
    _tugas(
        r2,
        ringan,
        status=AssignmentStatus.REPLACED,
        assigned_at=AWAL + timedelta(hours=2),
    )
    # berat: one active unjudged (open=1) + one void (still in total)
    r3, r4 = _ronde(3), _ronde(4)
    _tugas(r3, berat, assigned_at=AWAL + timedelta(hours=3))
    _tugas(
        r4,
        berat,
        status=AssignmentStatus.VOID,
        assigned_at=AWAL + timedelta(hours=4),
    )
    db_session.commit()

    # Act
    beban = service.ReviewTableStore(db_session).open_queue([ringan, berat, penulis])

    # Assert
    # SQLite may drop tzinfo on DateTime(timezone=True); compare as naive UTC.
    def _utc(nilai: datetime) -> datetime:
        return nilai.replace(tzinfo=None) if nilai.tzinfo else nilai

    assert beban[ringan].open_count == 0
    assert beban[ringan].total_count == 2
    assert _utc(beban[ringan].last_assigned_at) == _utc(AWAL + timedelta(hours=2))
    assert beban[berat].open_count == 1
    assert beban[berat].total_count == 2
    assert _utc(beban[berat].last_assigned_at) == _utc(AWAL + timedelta(hours=4))
    assert penulis not in beban


def test_noop_notifier_menerima_panggilan_tanpa_efek():
    # Act + Assert
    assert NoopNotifier().notify([uuid.uuid4()], "review_assigned", {"title": "t"}) is None


# --- SCRUM-196 AC5: Admin diberi tahu bila kandidat kurang dari dua ---


@pytest.mark.parametrize("jumlah_reviewer", [0, 1])
def test_kurang_dari_dua_memberitahu_semua_admin_sekali(
    db_session, buat_user, store, notifier, jumlah_reviewer
):
    """AC5: 0 atau 1 kandidat → needs-assignment + semua Admin aktif diberitahu sekali."""
    # Arrange
    penulis = buat_user(1, Role.CONTRIBUTOR)
    for nomor in range(2, 2 + jumlah_reviewer):
        buat_user(nomor)
    admin_a = buat_user(10, Role.ADMIN)
    admin_b = buat_user(11, Role.ADMIN)
    buat_user(12, Role.ADMIN, is_active=False)
    round_id = uuid.uuid4()

    # Act
    terpilih = _tugaskan(db_session, store, notifier, author_id=penulis, round_id=round_id)

    # Assert
    assert terpilih == []
    assert store.perlu_penugasan == [round_id]
    assert len(notifier.panggilan) == 1
    user_ids, jenis, payload = notifier.panggilan[0]
    assert sorted(user_ids) == sorted([admin_a, admin_b])
    assert jenis == "review_needs_assignment"
    assert set(payload) == {"title", "body", "link"}
    assert payload["link"] == "/admin/reviews"


def test_cukup_kandidat_tidak_memberitahu_admin(db_session, buat_user, store, notifier):
    # Arrange
    penulis = buat_user(1, Role.CONTRIBUTOR)
    buat_user(2)
    buat_user(3)
    buat_user(10, Role.ADMIN)
    round_id = uuid.uuid4()

    # Act
    terpilih = _tugaskan(db_session, store, notifier, author_id=penulis, round_id=round_id)

    # Assert
    assert len(terpilih) == 2
    assert all(jenis == "review_assigned" for _, jenis, _ in notifier.panggilan)
    assert not any(jenis == "review_needs_assignment" for _, jenis, _ in notifier.panggilan)


def test_notifikasi_admin_ditahan_oleh_deferred_notifier(db_session, buat_user, store):
    """AC5: notifikasi Admin hanya keluar setelah send (setelah commit pemanggil)."""
    # Arrange
    penulis = buat_user(1, Role.CONTRIBUTOR)
    admin = buat_user(10, Role.ADMIN)
    tertunda = DeferredNotifier()
    tujuan = FakeNotifier()
    round_id = uuid.uuid4()

    # Act
    terpilih = service.assign_on_submit(
        db_session,
        round_id=round_id,
        author_id=penulis,
        store=store,
        notifier=tertunda,
    )

    # Assert: belum terkirim sebelum send
    assert terpilih == []
    assert store.perlu_penugasan == [round_id]
    assert tujuan.panggilan == []

    tertunda.send(tujuan)

    assert len(tujuan.panggilan) == 1
    user_ids, jenis, _payload = tujuan.panggilan[0]
    assert user_ids == [admin]
    assert jenis == "review_needs_assignment"


def test_is_case_author_satu_aturan_untuk_tiga_jalur():
    """SCRUM-196: helper bersama dipakai auto-assign, replace, dan verdict."""
    penulis = uuid.uuid4()
    lain = uuid.uuid4()
    assert service._is_case_author(user_id=penulis, author_id=penulis) is True
    assert service._is_case_author(user_id=lain, author_id=penulis) is False
