"""Migrasi akun Author dan Reviewer menjadi Kontributor di PostgreSQL. PBI-24 AC7, AC9.

SCRUM-195. Migrasi b19c5e7d2a41 mengubah users.role lewat SQL, jadi hanya
PostgreSQL yang bisa membuktikan bahwa:
- kasus, versi, assignment, dan verdict tidak berubah jumlah maupun pemiliknya;
- setiap akun yang berubah punya satu baris audit user.role_changed;
- downgrade mengembalikan peran lama dari baris audit itu, dengan baris audit balik.

Test ini menurunkan database ke revisi sebelumnya lalu menaikkannya lagi, dan
selalu mengembalikannya ke head di akhir. Data di-commit dan tidak dihapus
(audit_logs append-only); setiap jalan memakai id baru.
"""

import os
import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.modules.cases.models import Case, CaseStatus, CaseVersion, SplitTag
from app.modules.reviews.models import (
    ReviewAssignment,
    ReviewRound,
    ReviewVerdict,
    RoundStatus,
    VerdictDecision,
)
from app.modules.suites.models import Suite
from app.shared.config import get_settings

POSTGRES_URL = os.environ.get("TEST_POSTGRES_URL", "")
SEBELUM = "4a10dea5a620"  # pragma: allowlist secret (revision id)
ALASAN = "SCRUM-195: Author dan Reviewer digabung menjadi Kontributor"
ALASAN_BALIK = "SCRUM-195: rollback migrasi akun Kontributor"
ROOT = Path(__file__).resolve().parents[2]

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(not POSTGRES_URL, reason="TEST_POSTGRES_URL tidak diisi"),
]


@pytest.fixture
def alembic_cfg(monkeypatch):
    """Alembic membaca URL dari settings (migrations/env.py), jadi arahkan ke database tes."""
    monkeypatch.setenv("DATABASE_URL", POSTGRES_URL)
    get_settings.cache_clear()
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    yield cfg
    command.upgrade(cfg, "head")
    get_settings.cache_clear()


@pytest.fixture
def engine():
    mesin = create_engine(POSTGRES_URL)
    yield mesin
    mesin.dispose()


def _pengguna(koneksi, role: str) -> uuid.UUID:
    """Ditulis lewat SQL: model Role di kode sudah tidak mengenal author dan reviewer."""
    user_id = uuid.uuid4()
    koneksi.execute(
        text(
            "INSERT INTO users (id, email, name, role, is_active, created_at, updated_at) "
            "VALUES (:id, :email, :name, :role, true, now(), now())"
        ),
        {"id": user_id, "email": f"m195-{user_id.hex}@veritask.test", "name": role, "role": role},
    )
    return user_id


def _kasus_dengan_review(engine, penulis, reviewer_a, reviewer_b) -> uuid.UUID:
    """Satu kasus milik penulis, dua assignment dengan verdict masing-masing. Mengembalikan case_id."""
    with sessionmaker(bind=engine)() as db:
        suite = Suite(name=f"M195 {uuid.uuid4().hex}", created_by=penulis)
        db.add(suite)
        db.flush()
        case_id, version_id = uuid.uuid4(), uuid.uuid4()
        kode = f"M195-{uuid.uuid4().hex[:8]}"
        db.add(
            Case(
                id=case_id,
                case_code=kode,
                suite_id=suite.id,
                current_version_id=version_id,
                created_by=penulis,
                updated_by=penulis,
            )
        )
        db.add(
            CaseVersion(
                id=version_id,
                case_id=case_id,
                version_no=1,
                status=CaseStatus.IN_REVIEW,
                case_code=kode,
                split_tag=SplitTag.DEV,
                content={"title": "PHK sepihak", "question": "Sah?"},
                created_by=penulis,
            )
        )
        db.flush()
        ronde = ReviewRound(
            case_id=case_id, case_version_id=version_id, round_no=1, status=RoundStatus.OPEN
        )
        db.add(ronde)
        db.flush()
        for reviewer in (reviewer_a, reviewer_b):
            tugas = ReviewAssignment(review_round_id=ronde.id, reviewer_id=reviewer)
            db.add(tugas)
            db.flush()
            db.add(ReviewVerdict(assignment_id=tugas.id, decision=VerdictDecision.APPROVE))
        db.commit()
    return case_id


def _jejak(koneksi, case_id) -> dict:
    """Jumlah dan pemilik baris kasus, versi, assignment, dan verdict untuk satu kasus."""
    return {
        "kasus": koneksi.execute(
            text("SELECT created_by FROM cases WHERE id = :id"), {"id": case_id}
        ).all(),
        "versi": koneksi.execute(
            text("SELECT id, created_by, status::text FROM case_versions WHERE case_id = :id"),
            {"id": case_id},
        ).all(),
        "assignment": sorted(
            koneksi.execute(
                text(
                    "SELECT a.id, a.reviewer_id, a.status::text FROM review_assignments a "
                    "JOIN review_rounds r ON r.id = a.review_round_id WHERE r.case_id = :id"
                ),
                {"id": case_id},
            ).all()
        ),
        "verdict": sorted(
            koneksi.execute(
                text(
                    "SELECT v.id, v.assignment_id, v.decision::text FROM review_verdicts v "
                    "JOIN review_assignments a ON a.id = v.assignment_id "
                    "JOIN review_rounds r ON r.id = a.review_round_id WHERE r.case_id = :id"
                ),
                {"id": case_id},
            ).all()
        ),
    }


def _peran(koneksi, *user_ids) -> list[str]:
    return [
        koneksi.execute(text("SELECT role::text FROM users WHERE id = :id"), {"id": u}).scalar_one()
        for u in user_ids
    ]


def _audit(koneksi, user_id) -> list[tuple]:
    return koneksi.execute(
        text(
            "SELECT actor_user_id, before, after, reason FROM audit_logs "
            "WHERE action = 'user.role_changed' AND entity_id = :id ORDER BY id"
        ),
        {"id": user_id},
    ).all()


def test_migrasi_akun_menjadi_kontributor_tanpa_kehilangan_data(alembic_cfg, engine):
    """AC7: peran berubah, kasus dan review tetap atas nama pemiliknya. AC9: tercatat."""
    # Arrange
    command.downgrade(alembic_cfg, SEBELUM)
    with engine.begin() as koneksi:
        penulis, reviewer_a = _pengguna(koneksi, "author"), _pengguna(koneksi, "reviewer")
        reviewer_b, admin = _pengguna(koneksi, "reviewer"), _pengguna(koneksi, "admin")
    case_id = _kasus_dengan_review(engine, penulis, reviewer_a, reviewer_b)
    with engine.connect() as koneksi:
        sebelum = _jejak(koneksi, case_id)

    # Act
    command.upgrade(alembic_cfg, "head")

    # Assert
    with engine.connect() as koneksi:
        assert _jejak(koneksi, case_id) == sebelum
        assert sum(len(baris) for baris in sebelum.values()) == 6
        assert _peran(koneksi, penulis, reviewer_a, reviewer_b, admin) == [
            "contributor",
            "contributor",
            "contributor",
            "admin",
        ]
        assert _audit(koneksi, penulis) == [
            (None, {"role": "author"}, {"role": "contributor"}, ALASAN)
        ]
        assert _audit(koneksi, reviewer_a)[0][1:3] == (
            {"role": "reviewer"},
            {"role": "contributor"},
        )
        assert _audit(koneksi, admin) == []
        nilai_enum = koneksi.execute(
            text("SELECT unnest(enum_range(NULL::user_role_enum))::text")
        ).scalars()
        assert set(nilai_enum) == {"admin", "contributor", "viewer", "developer_ai"}


def test_rollback_mengembalikan_peran_lama_dengan_jejak_audit(alembic_cfg, engine):
    """AC7: skrip bisa di-rollback. Baris audit lama tidak dihapus, ada baris balik."""
    # Arrange
    command.downgrade(alembic_cfg, SEBELUM)
    with engine.begin() as koneksi:
        penulis, reviewer = _pengguna(koneksi, "author"), _pengguna(koneksi, "reviewer")
    command.upgrade(alembic_cfg, "head")
    with engine.begin() as koneksi:
        # Akun yang baru dibuat sebagai Kontributor tidak punya peran lama.
        baru = _pengguna(koneksi, "contributor")

    # Act
    command.downgrade(alembic_cfg, SEBELUM)

    # Assert
    with engine.connect() as koneksi:
        assert _peran(koneksi, penulis, reviewer, baru) == ["author", "reviewer", "author"]
        jejak = _audit(koneksi, reviewer)
        assert [(b, a, r) for _, b, a, r in jejak] == [
            ({"role": "reviewer"}, {"role": "contributor"}, ALASAN),
            ({"role": "contributor"}, {"role": "reviewer"}, ALASAN_BALIK),
        ]
        assert _audit(koneksi, baru)[-1][1:] == (
            {"role": "contributor"},
            {"role": "author"},
            ALASAN_BALIK,
        )
