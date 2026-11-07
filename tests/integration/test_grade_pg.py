"""SCRUM-78 SCRUM-226 PostgreSQL grade and regrade integration tests.

Verifies that grade_session and save_grading_run strictly adhere to PostgreSQL triggers:
- grading_runs_guard: a new grading run starts queued and not latest, then moves queued -> running -> finished
- technical disruption attempts do not insert into llm_judge_results
"""

import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

from app.main import app
from app.modules.grading.models import (
    GradingResult,
    GradingRun,
    GradingRunKind,
    GradingRunStatus,
    LlmJudgeResult,
)
from app.modules.runs.session_grade import grade_session
from app.shared.database import get_db
from app.shared.security import Role, get_current_user
from tests.grading_pg import buat_percobaan, buat_versi_aktif
from tests.measurement_pg import buat_admin, buat_produk, buat_sesi, ubah_status

TEST_POSTGRES_URL = os.environ.get("TEST_POSTGRES_URL", "")

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(
        not TEST_POSTGRES_URL,
        reason="TEST_POSTGRES_URL not set",
    ),
]


@pytest.fixture
def pg():
    engine = create_engine(TEST_POSTGRES_URL)

    with engine.connect() as conn:
        transaction = conn.begin()

        try:
            with Session(
                bind=conn,
                join_transaction_mode="create_savepoint",
            ) as db:
                yield db, conn
        finally:
            transaction.rollback()

    engine.dispose()


def _setup_pg_grade_session(conn, extra_disrupted: bool = False):
    admin_id = buat_admin(conn)
    product_id = buat_produk(conn, admin_id)
    session_id = buat_sesi(conn, product_id, admin_id)
    buat_versi_aktif(conn, admin_id)

    body_data = {
        "sections": {
            "case_code": "KASUS-PG-01",
            "identity.title": "Kasus Integrasi PG",
            "split_tag": "dev",
            "answer_criteria": {
                "must_contain": ["badan hukum", "pesangon"],
                "must_not_contain": ["ilegal"],
                "expected_conclusion": "sah",
            },
            "legal_refs": [
                {
                    "jenis": "UU",
                    "nomor": "1",
                    "tahun": 2023,
                    "pasal": "1",
                }
            ],
        }
    }

    attempt_id = buat_percobaan(conn, session_id, admin_id, body=body_data)

    disrupt_att_id = None
    if extra_disrupted:
        disrupt_att_id = buat_percobaan(conn, session_id, admin_id, body=body_data)
        conn.execute(
            text("UPDATE attempts SET status = 'running' WHERE id = :id"),
            {"id": disrupt_att_id},
        )
        conn.execute(
            text(
                "UPDATE attempts SET status = 'technical_failure', "
                "answer_text = 'Maaf, saya tidak dapat menjawab pertanyaan ini.' "
                "WHERE id = :id"
            ),
            {"id": disrupt_att_id},
        )

    conn.execute(
        text("UPDATE attempts SET status = 'running' WHERE id = :id"),
        {"id": attempt_id},
    )
    conn.execute(
        text(
            "UPDATE attempts SET status = 'succeeded', "
            "answer_text = 'Berdasarkan UU 1/2023 Pasal 1, badan hukum sah berlaku tanpa izin.' "
            "WHERE id = :id"
        ),
        {"id": attempt_id},
    )

    ubah_status(conn, session_id, "finished")

    if extra_disrupted:
        return admin_id, session_id, attempt_id, disrupt_att_id
    return admin_id, session_id, attempt_id


def test_grade_session_pg_lifecycle(pg):
    """Positif: Penilaian sesi di PostgreSQL mengikuti siklus status."""
    db, conn = pg
    admin_id, session_id, attempt_id = _setup_pg_grade_session(conn)

    # 1. Pemicuan penilaian awal (kind=initial)
    res_initial = grade_session(db, session_id, actor_id=admin_id, actor_role=Role.ADMIN)

    assert res_initial.status == "completed"
    assert res_initial.kind == "initial"
    assert res_initial.is_latest is True
    assert res_initial.scores.dev.deterministic is not None
    assert res_initial.scores.dev.quality is None

    # Verifikasi GradingRun di Postgres
    runs = db.scalars(
        select(GradingRun)
        .where(GradingRun.session_id == session_id)
        .order_by(GradingRun.triggered_at)
    ).all()
    assert len(runs) == 1
    run_1 = runs[0]
    assert run_1.status == GradingRunStatus.FINISHED
    assert run_1.kind == GradingRunKind.INITIAL
    assert run_1.is_latest is True
    assert run_1.finished_at is not None

    # 2. Pemicuan penilaian ulang (kind=regrade)
    res_regrade = grade_session(db, session_id, actor_id=admin_id, actor_role=Role.ADMIN)

    assert res_regrade.status == "completed"
    assert res_regrade.kind == "regrade"
    assert res_regrade.is_latest is True

    # Verifikasi transisi is_latest pada Postgres
    runs_after = db.scalars(
        select(GradingRun)
        .where(GradingRun.session_id == session_id)
        .order_by(GradingRun.triggered_at)
    ).all()
    assert len(runs_after) == 2
    assert runs_after[0].kind == GradingRunKind.INITIAL
    assert runs_after[0].is_latest is False
    assert runs_after[0].status == GradingRunStatus.FINISHED

    assert runs_after[1].kind == GradingRunKind.REGRADE
    assert runs_after[1].is_latest is True
    assert runs_after[1].status == GradingRunStatus.FINISHED


def test_grade_session_pg_technical_disruption_does_not_call_judge_guard(pg):
    """Positif: Percobaan dengan gangguan teknis tidak memasukkan baris juri ke Postgres."""
    db, conn = pg
    admin_id, session_id, attempt_id, disrupt_att_id = _setup_pg_grade_session(
        conn, extra_disrupted=True
    )

    res = grade_session(db, session_id, actor_id=admin_id, actor_role=Role.ADMIN)
    assert res.status == "completed"

    # Verifikasi GradingResult di PostgreSQL
    results = db.scalars(
        select(GradingResult).where(GradingResult.grading_run_id == res.grading_run_id)
    ).all()
    assert len(results) == 2
    disrupt_res = next(r for r in results if r.attempt_id == disrupt_att_id)
    assert disrupt_res.is_technical_disruption is True
    assert disrupt_res.det_score is None

    # Pastikan trigger llm_judge_guard tidak dilanggar dan tidak ada baris juri untuk attempt gangguan
    judges = db.scalars(
        select(LlmJudgeResult).where(LlmJudgeResult.grading_result_id == disrupt_res.id)
    ).all()
    assert len(judges) == 0


def test_post_grade_endpoint_pg(pg):
    """Positif: Endpoint POST /measurements/{id}/grade berfungsi sukses di PostgreSQL."""
    db, conn = pg
    admin_id, session_id, _ = _setup_pg_grade_session(conn)

    class FakeAdmin:
        id = admin_id
        role = Role.ADMIN
        user_id = admin_id

    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: FakeAdmin()

    try:
        client = TestClient(app)
        res = client.post(f"/measurements/{session_id}/grade", json={"rubric_version": "v1"})
        assert res.status_code == 200
        data = res.json()
        assert data["session_id"] == str(session_id)
        assert data["kind"] == "initial"
        assert data["status"] == "completed"
        assert data["is_latest"] is True
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_current_user, None)
