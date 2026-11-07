"""SCRUM-222 PR B: service versi rubrik di SQLite. PBI-14 AC3, AC7, D7.

POST membuat versi berikutnya dan langsung menjadikannya aktif di transaksi yang
sama (D7 dan SCRUM-228 selalu memakai "versi rubrik aktif terbaru"). Versi lama tetap
tersimpan sebagai riwayat, hanya tidak aktif lagi. Trigger PostgreSQL diuji di
tests/integration/test_rubric_service_pg.py; di sini logika service-nya.

Isolasi: db_session SQLite in-memory dari tests/conftest.py; monkeypatch hanya untuk
memaksa bentrok nomor versi, karena dua request bersamaan tidak bisa dibuat di SQLite.

Setiap tes diberi label di docstring-nya:
- Positif: perilaku yang memang diharapkan terjadi.
- Negatif: permintaan yang harus ditolak.
- Edge: batas atau kasus pinggir.
"""

import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy.exc import IntegrityError

from app.modules.auth.models import User
from app.modules.grading import repository, rubric_rules, service
from app.modules.grading.models import RubricVersion
from app.modules.grading.schemas import RubricVersionCreate
from app.shared.exceptions import ConflictError, NotFoundError, ValidationError
from tests.modules.grading.rubric_payload import DIMENSI, LULUS_GAGAL, body_rubrik

ADMIN_ID = uuid.UUID("00000000-0000-0000-0000-000000000222")


@pytest.fixture
def admin(db_session) -> uuid.UUID:
    db_session.add(
        User(id=ADMIN_ID, email="admin-222@veritask.test", name="Admin Rubrik", role="admin")
    )
    db_session.commit()
    return ADMIN_ID


def _buat(db_session, admin, **ubah):
    payload = RubricVersionCreate.model_validate(body_rubrik(**ubah))
    return service.create_version(db_session, payload, created_by=admin)


def _jumlah_versi(db_session) -> int:
    return db_session.query(RubricVersion).count()


# --- Membuat versi --------------------------------------------------------------


def test_versi_pertama_bernomor_1_aktif_dan_terkunci(db_session, admin):
    """Positif: AC7, versi tersimpan lengkap dan langsung dipakai menilai."""
    versi = _buat(db_session, admin)

    assert (versi.version_no, versi.is_active, versi.locked_at is not None) == (1, True, True)
    assert (versi.created_by, versi.created_by_name) == (admin, "Admin Rubrik")
    assert [a.name for a in versi.aspects] == list(DIMENSI)
    assert [a.sort_order for a in versi.aspects] == [1, 2, 3]
    assert [lv.score for lv in versi.levels] == [5, 4, 3, 2, 1, 0]
    assert [d.aspect_name for d in versi.levels[0].descriptors] == list(DIMENSI)
    assert versi.levels[0].descriptors[0].descriptor == "Dimensi A pada level 5."


def test_versi_baru_menjadi_aktif_dan_versi_lama_tetap_tersimpan(db_session, admin):
    """Positif: AC7 dan D7, versi terbaru = versi aktif; riwayat tidak dihapus."""
    pertama = _buat(db_session, admin)

    kedua = _buat(db_session, admin, judge_prompt_quality="Prompt kualitas v2.")

    lama = service.get_version(db_session, pertama.id)
    assert (kedua.version_no, kedua.is_active) == (2, True)
    assert (lama.is_active, lama.locked_at is not None) == (False, True)
    assert lama.judge_prompt_quality == "Prompt kualitas sintetis."


def test_deskriptor_dipetakan_ke_aspek_tanpa_membedakan_huruf(db_session, admin):
    """Edge: nama dimensi di deskriptor ditulis dengan huruf berbeda."""
    body = body_rubrik()
    body["levels"][0]["descriptors"][0]["aspect_name"] = "dimensi a"

    versi = service.create_version(
        db_session, RubricVersionCreate.model_validate(body), created_by=admin
    )

    assert versi.levels[0].descriptors[0].aspect_name == "Dimensi A"


def test_bobot_dan_penyetuju_tersimpan(db_session, admin):
    aspek = [
        {"name": "Dimensi A", "grader_path": "deterministic", "weight": "40", "definition": "D."},
        {"name": "Dimensi B", "grader_path": "llm", "weight": "60", "definition": "D."},
    ]

    versi = _buat(
        db_session,
        admin,
        aspects=aspek,
        levels=LULUS_GAGAL,
        approved_by_name="Penyetuju Sintetis",
        approved_at="2026-10-27",
    )

    assert [(a.grader_path, str(a.weight)) for a in versi.aspects] == [
        ("deterministic", "40.00"),
        ("llm", "60.00"),
    ]
    assert (versi.approved_by_name, str(versi.approved_at)) == ("Penyetuju Sintetis", "2026-10-27")


def test_isi_yang_melanggar_aturan_ditolak_tanpa_menyimpan_apa_pun(db_session, admin):
    """Negatif: rubric_rules menjadi 422 dengan code dan field; tidak ada versi tersimpan."""
    aspek = [
        {"name": "Dimensi A", "grader_path": "llm", "weight": "50", "definition": "D."},
        {"name": "Dimensi B", "grader_path": "llm", "weight": None, "definition": "D."},
    ]

    with pytest.raises(ValidationError) as info:
        _buat(db_session, admin, aspects=aspek)

    assert (info.value.code, info.value.field) == (rubric_rules.WEIGHTS_INCOMPLETE, "aspects")
    assert _jumlah_versi(db_session) == 0


def test_bentrok_nomor_versi_menjadi_409_dan_versi_aktif_tetap(db_session, admin, monkeypatch):
    """Negatif: dua POST bersamaan membaca nomor terakhir yang sama; yang kalah dapat 409."""
    pertama = _buat(db_session, admin)
    monkeypatch.setattr(repository, "max_version_no", lambda db: 0)

    with pytest.raises(ConflictError) as info:
        _buat(db_session, admin)

    assert info.value.code == service.RUBRIC_VERSION_CONFLICT
    assert _jumlah_versi(db_session) == 1
    assert service.get_version(db_session, pertama.id).is_active is True


def _gagal_tulis(pesan: str, constraint: str | None = None):
    """add_and_flush palsu yang gagal seperti driver database dengan pesan tertentu."""
    asal = Exception(pesan)
    asal.diag = SimpleNamespace(constraint_name=constraint)

    def tulis(db, rows):
        raise IntegrityError("INSERT ...", {}, asal)

    return tulis


@pytest.mark.parametrize(
    ("pesan", "constraint"),
    [
        ("duplicate key", "rubric_versions_version_no_key"),
        ("duplicate key", "uq_rubric_versions_one_active"),
        ("UNIQUE constraint failed: rubric_versions.is_active", None),
    ],
    ids=["pg-nomor-versi", "pg-satu-aktif", "sqlite-satu-aktif"],
)
def test_hanya_bentrok_versi_yang_menjadi_409(db_session, admin, monkeypatch, pesan, constraint):
    """Positif: review Kak Aileen #112, 409 hanya untuk constraint bentrok antar-POST."""
    monkeypatch.setattr(repository, "add_and_flush", _gagal_tulis(pesan, constraint))

    with pytest.raises(ConflictError):
        _buat(db_session, admin)


@pytest.mark.parametrize(
    ("pesan", "constraint"),
    [
        ("insert or update violates foreign key", "rubric_versions_created_by_fkey"),
        ("FOREIGN KEY constraint failed", None),
        ("new row violates check constraint", "ck_rubric_versions_prompt_quality"),
    ],
    ids=["pg-fk", "sqlite-fk", "pg-check"],
)
def test_pelanggaran_lain_tidak_disamarkan_sebagai_409(
    db_session, admin, monkeypatch, pesan, constraint
):
    """Negatif: FK atau CHECK yang gagal adalah bug, bukan "muat ulang"; tetap di-rollback."""
    monkeypatch.setattr(repository, "add_and_flush", _gagal_tulis(pesan, constraint))

    with pytest.raises(IntegrityError):
        _buat(db_session, admin)

    assert _jumlah_versi(db_session) == 0


# --- Membaca versi --------------------------------------------------------------


def test_daftar_kosong_sebelum_ada_versi(db_session):
    """Edge: halaman rubrik pertama kali dibuka (state empty, SCRUM-228)."""
    assert service.list_versions(db_session) == []


def test_daftar_versi_terbaru_di_atas_dengan_pembuat_dan_jumlah_isi(db_session, admin):
    """Positif: SCRUM-228, daftar versi read-only: nomor, tanggal, pembuat, penyetuju."""
    _buat(db_session, admin)
    _buat(
        db_session,
        admin,
        aspects=body_rubrik()["aspects"][:1],
        levels=LULUS_GAGAL,
    )

    daftar = service.list_versions(db_session)

    assert [(v.version_no, v.is_active) for v in daftar] == [(2, True), (1, False)]
    assert [(v.aspect_count, v.level_count) for v in daftar] == [(1, 2), (3, 6)]
    assert {v.created_by_name for v in daftar} == {"Admin Rubrik"}


def test_pembuat_yang_tidak_ada_lagi_tampil_kosong(db_session, admin):
    """Edge: nama pembuat diambil dari users; id tanpa baris user menjadi None."""
    versi = _buat(db_session, admin)
    db_session.query(User).filter(User.id == admin).delete()
    db_session.commit()

    assert service.get_version(db_session, versi.id).created_by_name is None


def test_versi_yang_tidak_ada_menjadi_404(db_session):
    with pytest.raises(NotFoundError) as info:
        service.get_version(db_session, uuid.uuid4())

    assert info.value.code == service.RUBRIC_VERSION_NOT_FOUND
