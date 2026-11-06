"""Unit test PBI-14 SCRUM-226: Pemicu penilaian, penanganan gangguan teknis, dan visibilitas hasil.

AC4: Gangguan teknis (jawaban kosong, AI menolak, error judge) tanpa skor, dilewati.
AC6, AC7: Pemicu manual oleh Admin via POST /measurements/{id}/grade, versi rubrik, dan riwayat nilai ulang.
AC8, D10: Visibilitas hasil:
- Sesi berjalan: Admin saja. Viewer & Developer AI ditolak (403).
- Sesi selesai: Admin & Viewer melihat lengkap. Developer AI melihat kasus dev,
  sedangkan kasus test disamarkan per-kasus (isi & skor kasus disembunyikan),
  namun skor agregat test tetap terlihat di ThreeScores.
AC9: Penilaian seragam untuk sesi otomatis maupun manual.
PBI-11 AC9: Tiga skor terpisah (dev, test, gabungan).
"""

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.modules.audit.models import AuditLog
from app.modules.auth.models import User
from app.modules.cases.models import SplitTag
from app.modules.cases.sections import snapshot_body
from app.modules.cases.service import MASKED_CASE_CODE, MASKED_TITLE
from app.modules.runs.models import (
    Attempt,
    AttemptSource,
    AttemptStatus,
    CallLog,
    CaseSnapshot,
    CaseSnapshotItem,
    SessionStatus,
)
from app.modules.runs.schemas import (
    CaseGradeResult,
    CategoryScore,
    ScorePair,
    SessionGradingResponse,
    ThreeScores,
)
from app.modules.runs.session_grade import (
    AttemptGradeInput,
    CaseGradeInput,
    DbGradingStore,
    MemoryGradingStore,
    aggregate_case_scores,
    apply_visibility_mask,
    check_technical_disruption,
    compute_category_scores,
    ensure_session_gradable,
    grade_session_data,
    is_ai_refusal,
    is_empty_answer,
)
from app.shared.exceptions import ConflictError, ForbiddenError
from app.shared.security import Role
from tests.modules.providers.test_product_lists import _otomatis
from tests.modules.runs.test_session_list_endpoint import _sesi

# ---------------------------------------------------------------------------
# 1. Penanganan Gangguan Teknis (AC4)
# ---------------------------------------------------------------------------


def test_jawaban_kosong_terdeteksi_sebagai_gangguan_teknis():
    """AC4: Jawaban None, string kosong, atau spasi saja dihitung gangguan teknis."""
    assert is_empty_answer(None) is True
    assert is_empty_answer("") is True
    assert is_empty_answer("   \n\t  ") is True
    assert is_empty_answer("Jawaban sah") is False

    assert (
        check_technical_disruption(
            status=AttemptStatus.SUCCEEDED,
            answer_text="",
        )
        is True
    )
    assert (
        check_technical_disruption(
            status=AttemptStatus.SUCCEEDED,
            answer_text="   ",
        )
        is True
    )


def test_ai_menolak_menjawab_terdeteksi_sebagai_gangguan_teknis():
    """AC4: AI menolak menjawab dengan kalimatnya sendiri atau flag failure_kind."""
    assert is_ai_refusal(None, failure_kind="ai_refused") is True
    assert (
        is_ai_refusal(
            "Maaf, saya tidak dapat menjawab pertanyaan ini karena kebijakan internal.",
        )
        is True
    )
    assert (
        is_ai_refusal(
            "Maaf, sebagai model AI saya tidak berwenang memberikan nasihat hukum.",
        )
        is True
    )
    assert is_ai_refusal("Menurut Pasal 156 UU Ketenagakerjaan...") is False

    # Masuk ke check_technical_disruption
    assert (
        check_technical_disruption(
            status=AttemptStatus.SUCCEEDED,
            answer_text="Maaf, saya tidak dapat menjawab pertanyaan hukum ini.",
        )
        is True
    )
    assert (
        check_technical_disruption(
            status=AttemptStatus.SUCCEEDED,
            answer_text="Jawaban normal",
            failure_kind="ai_refused",
        )
        is True
    )


def test_kegagalan_teknis_penyedia_dan_manual_refusal():
    """AC4: technical_failure, timeout, provider_error, platform_refused."""
    assert (
        check_technical_disruption(
            status=AttemptStatus.TECHNICAL_FAILURE,
            answer_text="some text",
        )
        is True
    )
    assert (
        check_technical_disruption(
            status=AttemptStatus.SUCCEEDED,
            answer_text="text",
            failure_kind="no_response",
        )
        is True
    )
    assert (
        check_technical_disruption(
            status=AttemptStatus.SUCCEEDED,
            answer_text="text",
            platform_refused=True,
        )
        is True
    )


def test_penilai_llm_error_dicatat_sebagai_gangguan_teknis():
    """Subtask: Penilai LLM yang error dicatat sebagai gangguan teknis."""
    assert (
        check_technical_disruption(
            status=AttemptStatus.SUCCEEDED,
            answer_text="Jawaban bagus",
            judge_error="LLM judge timeout after 60s",
        )
        is True
    )


def test_gangguan_teknis_tidak_dihitung_skor_0_dan_dilewati():
    """AC4: Percobaan gangguan teknis tanpa skor (None) dan dilewati dalam rata-rata kasus."""
    scores = [
        None,  # Gangguan teknis (jawaban kosong / AI menolak)
        ScorePair(deterministic=Decimal("4.00"), judge=Decimal("5.00")),
        ScorePair(deterministic=Decimal("2.00"), judge=Decimal("3.00")),
    ]
    avg = aggregate_case_scores(scores)
    assert avg is not None
    # Rata-rata dari 4.00 dan 2.00 = 3.00 (bukan (0+4+2)/3 = 2.00!)
    assert avg.deterministic == Decimal("3.00")
    # Rata-rata dari 5.00 dan 3.00 = 4.00 (bukan (0+5+3)/3 = 2.67!)
    assert avg.judge == Decimal("4.00")


def test_kasus_semua_gangguan_teknis_menghasilkan_skor_none():
    """Bila semua percobaan satu kasus mengalami gangguan teknis, skor kasus None."""
    scores = [None, None, None]
    avg = aggregate_case_scores(scores)
    assert avg is None


# ---------------------------------------------------------------------------
# 2. Pemicu Penilaian & Versi Rubrik & Nilai Ulang (AC6, AC7, AC9)
# ---------------------------------------------------------------------------


def _buat_kasus_sample():
    case_1_id = uuid.uuid4()
    item_1_id = uuid.uuid4()
    case_2_id = uuid.uuid4()
    item_2_id = uuid.uuid4()

    c1 = CaseGradeInput(
        snapshot_item_id=item_1_id,
        case_id=case_1_id,
        case_code="PHK-001",
        title="Uang Pesangon Pekerja",
        split_tag=SplitTag.DEV,
        category="Ketenagakerjaan",
        question="Bagaimana ketentuan pesangon?",
        attempts=[
            AttemptGradeInput(
                attempt_id=uuid.uuid4(),
                repetition_no=1,
                status=AttemptStatus.SUCCEEDED,
                source=AttemptSource.AUTOMATIC,
                answer_text="Jawaban valid 1",
                deterministic_score=Decimal("4.00"),
                judge_score=Decimal("4.00"),
            ),
            AttemptGradeInput(
                attempt_id=uuid.uuid4(),
                repetition_no=2,
                status=AttemptStatus.TECHNICAL_FAILURE,  # Gangguan teknis
                source=AttemptSource.AUTOMATIC,
                answer_text="",
            ),
        ],
    )

    c2 = CaseGradeInput(
        snapshot_item_id=item_2_id,
        case_id=case_2_id,
        case_code="CORP-002",
        title="Tanggung Jawab Direksi",
        split_tag=SplitTag.TEST,
        category="Perusahaan",
        question="Kapan direksi bertanggung jawab pribadi?",
        attempts=[
            AttemptGradeInput(
                attempt_id=uuid.uuid4(),
                repetition_no=1,
                status=AttemptStatus.SUCCEEDED,
                source=AttemptSource.AUTOMATIC,
                answer_text="Jawaban valid test",
                deterministic_score=Decimal("5.00"),
                judge_score=Decimal("5.00"),
            )
        ],
    )
    return [c1, c2]


def test_grade_session_data_initial():
    """AC6: Pemicu penilaian pertama menghasilkan kind='initial' dan is_latest=True."""
    session_id = uuid.uuid4()
    admin_id = uuid.uuid4()
    cases = _buat_kasus_sample()

    res = grade_session_data(
        session_id=session_id,
        cases_input=cases,
        triggered_by=admin_id,
        rubric_version="v1",
        existing_runs=(),
    )

    assert res.session_id == session_id
    assert res.triggered_by == admin_id
    assert res.kind == "initial"
    assert res.is_latest is True
    assert res.rubric_version == "v1"
    assert res.status == "completed"

    # Periksa kasus dev
    c1 = next(c for c in res.cases if c.case_code == "PHK-001")
    assert c1.attempts[0].is_technical_disruption is False
    assert c1.attempts[0].score == ScorePair(deterministic=Decimal("4.00"), judge=Decimal("4.00"))
    assert c1.attempts[1].is_technical_disruption is True
    assert c1.attempts[1].score is None
    # Skor kasus dev hanya dari attempt 1
    assert c1.scores == ScorePair(deterministic=Decimal("4.00"), judge=Decimal("4.00"))

    # ThreeScores
    assert res.scores.dev.deterministic == Decimal("4.00")
    assert res.scores.test.deterministic == Decimal("5.00")
    # Combined mean = (4 + 5) / 2 = 4.50
    assert res.scores.combined.deterministic == Decimal("4.50")
    assert res.scores.combined.judge == Decimal("4.50")


def test_regrade_menandai_kind_regrade_dan_memperbarui_is_latest():
    """AC7: Nilai ulang dengan versi rubrik baru menandai hasil lama bukan terbaru."""
    session_id = uuid.uuid4()
    admin_id = uuid.uuid4()
    store = MemoryGradingStore()
    cases = _buat_kasus_sample()

    run1 = grade_session_data(
        session_id=session_id,
        cases_input=cases,
        triggered_by=admin_id,
        rubric_version="v1",
        existing_runs=store.get_grading_runs(session_id),
    )
    store.save_grading_run(run1)

    assert run1.kind == "initial"
    assert run1.is_latest is True

    # Nilai ulang dengan v2
    run2 = grade_session_data(
        session_id=session_id,
        cases_input=cases,
        triggered_by=admin_id,
        rubric_version="v2",
        existing_runs=store.get_grading_runs(session_id),
    )
    store.save_grading_run(run2)

    assert run2.kind == "regrade"
    assert run2.rubric_version == "v2"
    assert run2.is_latest is True

    # Cek riwayat di store
    all_runs = store.get_grading_runs(session_id)
    assert len(all_runs) == 2
    assert all_runs[0].is_latest is False
    assert all_runs[1].is_latest is True


def test_penilaian_seragam_otomatis_dan_manual():
    """AC9: Percobaan manual dinilai dengan alur yang sama persis."""
    session_id = uuid.uuid4()
    admin_id = uuid.uuid4()

    manual_case = CaseGradeInput(
        snapshot_item_id=uuid.uuid4(),
        case_id=uuid.uuid4(),
        case_code="MAN-001",
        title="Kasus Manual",
        split_tag=SplitTag.DEV,
        attempts=[
            AttemptGradeInput(
                attempt_id=uuid.uuid4(),
                repetition_no=1,
                status=AttemptStatus.SUCCEEDED,
                source=AttemptSource.MANUAL,
                answer_text="Jawaban dari catatan tim legal",
                deterministic_score=Decimal("4.50"),
                judge_score=Decimal("4.50"),
            ),
            AttemptGradeInput(
                attempt_id=uuid.uuid4(),
                repetition_no=2,
                status=AttemptStatus.SUCCEEDED,
                source=AttemptSource.MANUAL,
                answer_text="Penolakan platform",
                platform_refused=True,  # Opsi 'Platform menolak menjawab' (D9)
            ),
        ],
    )

    res = grade_session_data(
        session_id=session_id,
        cases_input=[manual_case],
        triggered_by=admin_id,
        rubric_version="v1",
    )

    c = res.cases[0]
    assert c.attempts[0].is_technical_disruption is False
    assert c.attempts[1].is_technical_disruption is True
    # Attempt 2 tidak merusak nilai attempt 1
    assert c.scores == ScorePair(deterministic=Decimal("4.50"), judge=Decimal("4.50"))


# ---------------------------------------------------------------------------
# 3. Visibilitas Hasil & Penyamaran Server (AC8, PBI-11 AC9, D10)
# ---------------------------------------------------------------------------


def test_visibilitas_sesi_berjalan_hanya_admin():
    """AC8: Hasil sesi yang masih berjalan hanya dapat dilihat Admin."""
    grading = grade_session_data(
        session_id=uuid.uuid4(),
        cases_input=_buat_kasus_sample(),
        triggered_by=uuid.uuid4(),
    )

    # Admin boleh melihat sesi berjalan
    res_admin = apply_visibility_mask(grading, viewer_role=Role.ADMIN, session_status="running")
    assert res_admin is not None

    # Viewer ditolak 403
    with pytest.raises(ForbiddenError) as exc_viewer:
        apply_visibility_mask(grading, viewer_role=Role.VIEWER, session_status="running")
    assert "hanya dapat dilihat Admin" in exc_viewer.value.message

    # Developer AI ditolak 403
    with pytest.raises(ForbiddenError) as exc_dev_ai:
        apply_visibility_mask(grading, viewer_role=Role.DEVELOPER_AI, session_status="running")
    assert "hanya dapat dilihat Admin" in exc_dev_ai.value.message

    # Kontributor ditolak 403
    with pytest.raises(ForbiddenError):
        apply_visibility_mask(grading, viewer_role=Role.CONTRIBUTOR, session_status="running")


def test_visibilitas_sesi_selesai_admin_dan_viewer_melihat_lengkap():
    """AC8: Hasil sesi selesai dapat dilihat Admin dan Viewer tanpa penyamaran."""
    grading = grade_session_data(
        session_id=uuid.uuid4(),
        cases_input=_buat_kasus_sample(),
        triggered_by=uuid.uuid4(),
    )

    for role in (Role.ADMIN, Role.VIEWER):
        res = apply_visibility_mask(grading, viewer_role=role, session_status="finished")
        test_case = next(c for c in res.cases if c.split_tag == SplitTag.TEST)
        assert test_case.masked is False
        assert test_case.case_code == "CORP-002"
        assert test_case.title == "Tanggung Jawab Direksi"
        assert test_case.question == "Kapan direksi bertanggung jawab pribadi?"
        assert test_case.scores == ScorePair(deterministic=Decimal("5.00"), judge=Decimal("5.00"))


def test_visibilitas_developer_ai_kasus_test_tersamar_dan_skor_agregat_terbuka():
    """PBI-11 AC9 & AC8: Developer AI melihat dev utuh, test tersamar, tapi skor agregat test terlihat."""
    grading = grade_session_data(
        session_id=uuid.uuid4(),
        cases_input=_buat_kasus_sample(),
        triggered_by=uuid.uuid4(),
    )

    res = apply_visibility_mask(grading, viewer_role=Role.DEVELOPER_AI, session_status="finished")

    # Kasus dev utuh
    dev_case = next(c for c in res.cases if c.split_tag == SplitTag.DEV)
    assert dev_case.masked is False
    assert dev_case.case_code == "PHK-001"
    assert dev_case.title == "Uang Pesangon Pekerja"
    assert dev_case.question == "Bagaimana ketentuan pesangon?"
    assert dev_case.scores == ScorePair(deterministic=Decimal("4.00"), judge=Decimal("4.00"))
    assert dev_case.attempts[0].answer_text == "Jawaban valid 1"

    # Kasus test tersamar di level kasus
    test_case = next(c for c in res.cases if c.split_tag == SplitTag.TEST)
    assert test_case.masked is True
    assert test_case.case_code == MASKED_CASE_CODE
    assert test_case.title == MASKED_TITLE
    assert test_case.question is None
    assert test_case.scores is None  # Skor per kasus disembunyikan!
    assert test_case.attempts[0].answer_text is None  # Jawaban disembunyikan!
    assert test_case.attempts[0].score is None

    # NAMUN skor agregat total test tetap terlihat di ThreeScores!
    assert res.scores.test.deterministic == Decimal("5.00")
    assert res.scores.test.judge == Decimal("5.00")
    assert res.scores.dev.deterministic == Decimal("4.00")
    assert res.scores.combined.deterministic == Decimal("4.50")


def test_kontributor_ditolak_melihat_hasil():
    """D10: Kontributor tidak berwenang melihat hasil penilaian."""
    grading = grade_session_data(
        session_id=uuid.uuid4(),
        cases_input=_buat_kasus_sample(),
        triggered_by=uuid.uuid4(),
    )
    with pytest.raises(ForbiddenError):
        apply_visibility_mask(grading, viewer_role=Role.CONTRIBUTOR, session_status="finished")


# ---------------------------------------------------------------------------
# 4. Kategori & Skor Pecahan (AC10)
# ---------------------------------------------------------------------------


def test_kategori_tanpa_kategori_masuk_lain_lain():
    """AC10: Kasus tanpa kategori masuk 'Lain-lain'."""
    c1 = CaseGradeResult(
        snapshot_item_id=uuid.uuid4(),
        case_id=uuid.uuid4(),
        case_code="C1",
        title="T1",
        category="",
        scores=ScorePair(deterministic=Decimal("3.00"), judge=Decimal("4.00")),
    )
    c2 = CaseGradeResult(
        snapshot_item_id=uuid.uuid4(),
        case_id=uuid.uuid4(),
        case_code="C2",
        title="T2",
        category="Pidana",
        scores=ScorePair(deterministic=Decimal("5.00"), judge=Decimal("5.00")),
    )

    cat_scores = compute_category_scores([c1, c2])
    cat_names = {cs.category: cs for cs in cat_scores}
    assert "Lain-lain" in cat_names
    assert cat_names["Lain-lain"].case_count == 1
    assert cat_names["Lain-lain"].scores.deterministic == Decimal("3.00")
    assert "Pidana" in cat_names
    assert cat_names["Pidana"].case_count == 1
    assert cat_names["Pidana"].scores.deterministic == Decimal("5.00")


# ---------------------------------------------------------------------------
# 5. Kelayakan Sesi Dinilai
# ---------------------------------------------------------------------------


class _MockSession:
    def __init__(self, status: SessionStatus, archived_at=None):
        self.status = status
        self.archived_at = archived_at


def test_sesi_antre_dan_arsip_tidak_bisa_dinilai():
    """Sesi queued, paused, cancelled, failed, atau archived ditolak."""
    for invalid in (
        SessionStatus.QUEUED,
        SessionStatus.PAUSED,
        SessionStatus.CANCELLED,
        SessionStatus.FAILED,
    ):
        with pytest.raises(ConflictError) as exc:
            ensure_session_gradable(_MockSession(status=invalid))
        assert exc.value.code == "SESSION_NOT_GRADABLE"

    archived = _MockSession(status=SessionStatus.FINISHED, archived_at=datetime.now(UTC))
    with pytest.raises(ConflictError) as exc_a:
        ensure_session_gradable(archived)
    assert exc_a.value.code == "SESSION_ARCHIVED"

    # Running dan finished lolos
    ensure_session_gradable(_MockSession(status=SessionStatus.RUNNING))
    ensure_session_gradable(_MockSession(status=SessionStatus.FINISHED))
    ensure_session_gradable(_MockSession(status=SessionStatus.FINISHED_WITH_FAILURES))


# ---------------------------------------------------------------------------
# 6. HTTP API Endpoint Tests (POST /grade & GET /results)
# ---------------------------------------------------------------------------


@pytest.fixture
def pembuat(db_session) -> User:
    user = User(email="pembuat-grade@veritask.test", name="Admin Sesi", role=Role.ADMIN)
    db_session.add(user)
    db_session.commit()
    return user


@pytest.fixture
def produk(db_session):
    item = _otomatis("Produk Grade Uji")
    db_session.add(item)
    db_session.commit()
    return item


def _buat_body_kasus(code: str, tag: str, question: str) -> dict:
    return snapshot_body(
        case_code=code,
        version_no=1,
        status="approved",
        split_tag=tag,
        content={
            "title": f"Judul {code}",
            "question": question,
            "category": "kategori-uji",
            "answer_criteria": {"catatan": "kriteria sintetis"},
            "traps": [],
        },
        legal_refs=[
            {"regulation_type": "UU", "number": "40", "year": 2007, "article": "Pasal 156"}
        ],
    )


def _setup_sesi_lengkap(db_session, produk, pembuat, status=SessionStatus.FINISHED):
    sesi = _sesi(db_session, produk, pembuat, label="Sesi Grade Uji", status=status)
    snapshot = CaseSnapshot(session_id=sesi.id)
    db_session.add(snapshot)
    db_session.flush()

    # Kasus 1: dev
    item_dev = CaseSnapshotItem(
        snapshot_id=snapshot.id,
        case_id=uuid.uuid4(),
        case_version_id=uuid.uuid4(),
        body=_buat_body_kasus("DEV-01", "dev", "Pertanyaan Dev Uji"),
    )
    db_session.add(item_dev)
    db_session.flush()
    db_session.add(
        Attempt(
            session_id=sesi.id,
            snapshot_item_id=item_dev.id,
            repetition_no=1,
            source=AttemptSource.AUTOMATIC,
            status=AttemptStatus.SUCCEEDED,
            answer_text="Berdasarkan UU 40/2007 Pasal 156 ayat (1) mengenai dev",
            model_version="model-v1",
        )
    )

    # Kasus 2: test
    item_test = CaseSnapshotItem(
        snapshot_id=snapshot.id,
        case_id=uuid.uuid4(),
        case_version_id=uuid.uuid4(),
        body=_buat_body_kasus("TEST-02", "test", "Pertanyaan Rahasia Test"),
    )
    db_session.add(item_test)
    db_session.flush()
    db_session.add(
        Attempt(
            session_id=sesi.id,
            snapshot_item_id=item_test.id,
            repetition_no=1,
            source=AttemptSource.AUTOMATIC,
            status=AttemptStatus.SUCCEEDED,
            answer_text="Berdasarkan UU 40/2007 Pasal 156 ayat (1) mengenai test",
            model_version="model-v1",
        )
    )
    db_session.commit()
    return sesi


def test_post_grade_admin_sukses(as_role, db_session, produk, pembuat):
    """Positif: Admin memicu penilaian via POST /measurements/{id}/grade (AC6)."""
    sesi = _setup_sesi_lengkap(db_session, produk, pembuat)
    client = as_role(Role.ADMIN)

    res = client.post(f"/measurements/{sesi.id}/grade", json={"rubric_version": "v1"})
    assert res.status_code == 200
    body = res.json()

    assert body["session_id"] == str(sesi.id)
    assert body["kind"] == "initial"
    assert body["rubric_version"] == "v1"
    assert body["is_latest"] is True
    assert "scores" in body
    assert "cases" in body
    assert len(body["cases"]) == 2


def test_post_grade_bukan_admin_ditolak_403(as_role, db_session, produk, pembuat):
    """Negatif: Peran selain Admin (Viewer, Dev AI, Contributor) ditolak 403."""
    sesi = _setup_sesi_lengkap(db_session, produk, pembuat)

    for role in (Role.VIEWER, Role.DEVELOPER_AI, Role.CONTRIBUTOR):
        res = as_role(role).post(f"/measurements/{sesi.id}/grade", json={})
        assert res.status_code == 403


def test_post_grade_tanpa_login_ditolak_401(client, db_session, produk, pembuat):
    """Negatif: Request tanpa sesi login ditolak 401."""
    sesi = _setup_sesi_lengkap(db_session, produk, pembuat)
    res = client.post(f"/measurements/{sesi.id}/grade", json={})
    assert res.status_code == 401


def test_post_grade_sesi_antre_ditolak_409(as_role, db_session, produk, pembuat):
    """Negatif: Sesi status queued belum boleh dinilai (409)."""
    sesi = _setup_sesi_lengkap(db_session, produk, pembuat, status=SessionStatus.QUEUED)
    res = as_role(Role.ADMIN).post(f"/measurements/{sesi.id}/grade", json={})
    assert res.status_code == 409
    assert res.json()["code"] == "SESSION_NOT_GRADABLE"


def test_post_grade_sesi_arsip_ditolak_409(as_role, db_session, produk, pembuat):
    """Negatif: Sesi yang diarsipkan ditolak 409."""
    sesi = _setup_sesi_lengkap(db_session, produk, pembuat)
    sesi.archived_at = datetime.now(UTC)
    sesi.archived_by = pembuat.id
    db_session.commit()

    res = as_role(Role.ADMIN).post(f"/measurements/{sesi.id}/grade", json={})
    assert res.status_code == 409
    assert res.json()["code"] == "SESSION_ARCHIVED"


def test_post_grade_sesi_tidak_ada_ditolak_404(as_role):
    """Negatif: Sesi yang tidak ada menghasilkan 404."""
    dummy_id = uuid.uuid4()
    res = as_role(Role.ADMIN).post(f"/measurements/{dummy_id}/grade", json={})
    assert res.status_code == 404
    assert res.json()["code"] == "MEASUREMENT_NOT_FOUND"


def test_get_results_sesi_berjalan_hanya_admin(as_role, db_session, produk, pembuat):
    """AC8: Hasil sesi berjalan hanya dapat dilihat Admin; Viewer & Dev AI 403."""
    sesi = _setup_sesi_lengkap(db_session, produk, pembuat, status=SessionStatus.RUNNING)

    # Pemicu nilai oleh Admin
    post_res = as_role(Role.ADMIN).post(f"/measurements/{sesi.id}/grade", json={})
    assert post_res.status_code == 200

    # Admin GET hasil -> 200 OK
    res_admin = as_role(Role.ADMIN).get(f"/measurements/{sesi.id}/results")
    assert res_admin.status_code == 200

    # Viewer GET hasil -> 403 FORBIDDEN
    res_viewer = as_role(Role.VIEWER).get(f"/measurements/{sesi.id}/results")
    assert res_viewer.status_code == 403

    # Developer AI GET hasil -> 403 FORBIDDEN
    res_dev = as_role(Role.DEVELOPER_AI).get(f"/measurements/{sesi.id}/results")
    assert res_dev.status_code == 403


def test_get_results_sesi_selesai_developer_ai_masking(as_role, db_session, produk, pembuat):
    """AC8, PBI-11 AC9: Sesi selesai untuk Dev AI: kasus test tersamar, agregat test terlihat."""
    sesi = _setup_sesi_lengkap(db_session, produk, pembuat, status=SessionStatus.FINISHED)

    # Nilai dulu
    assert as_role(Role.ADMIN).post(f"/measurements/{sesi.id}/grade", json={}).status_code == 200

    # Admin & Viewer dapat melihat semua unmasked
    for role in (Role.ADMIN, Role.VIEWER):
        res = as_role(role).get(f"/measurements/{sesi.id}/results")
        assert res.status_code == 200
        cases = res.json()["cases"]
        test_case = next(c for c in cases if c["case_code"] == "TEST-02")
        assert test_case["masked"] is False
        assert test_case["question"] == "Pertanyaan Rahasia Test"
        assert test_case["scores"] is not None

    # Developer AI
    res_dev = as_role(Role.DEVELOPER_AI).get(f"/measurements/{sesi.id}/results")
    assert res_dev.status_code == 200
    dev_body = res_dev.json()

    # Skor agregat test tetap terlihat di ThreeScores!
    assert dev_body["scores"]["test"]["deterministic"] is not None
    assert dev_body["scores"]["test"]["judge"] is not None

    # Tapi kasus test tersamar per-kasus
    dev_cases = dev_body["cases"]
    test_c = next(c for c in dev_cases if c["masked"] is True)
    assert test_c["case_code"] == MASKED_CASE_CODE
    assert test_c["title"] == MASKED_TITLE
    assert test_c["question"] is None
    assert test_c["scores"] is None
    assert test_c["attempts"][0]["answer_text"] is None
    assert test_c["attempts"][0]["score"] is None

    # Kasus dev tidak tersamar
    dev_c = next(c for c in dev_cases if c["masked"] is False)
    assert dev_c["case_code"] == "DEV-01"
    assert dev_c["question"] == "Pertanyaan Dev Uji"
    assert dev_c["scores"] is not None


def test_regrade_via_api(as_role, db_session, produk, pembuat):
    """AC7: Nilai ulang via API menandai kind='regrade'."""
    sesi = _setup_sesi_lengkap(db_session, produk, pembuat, status=SessionStatus.FINISHED)
    admin = as_role(Role.ADMIN)

    res1 = admin.post(f"/measurements/{sesi.id}/grade", json={"rubric_version": "v1"})
    assert res1.status_code == 200
    assert res1.json()["kind"] == "initial"

    res2 = admin.post(f"/measurements/{sesi.id}/grade", json={"rubric_version": "v2"})
    assert res2.status_code == 200
    assert res2.json()["kind"] == "regrade"
    assert res2.json()["rubric_version"] == "v2"
    assert res2.json()["is_latest"] is True


def test_get_grade_alias(as_role, db_session, produk, pembuat):
    """GET /measurements/{id}/grade bekerja sebagai alias ke GET /results."""
    sesi = _setup_sesi_lengkap(db_session, produk, pembuat, status=SessionStatus.FINISHED)
    admin = as_role(Role.ADMIN)
    admin.post(f"/measurements/{sesi.id}/grade", json={})

    res_results = admin.get(f"/measurements/{sesi.id}/results")
    res_grade = admin.get(f"/measurements/{sesi.id}/grade")
    assert res_results.status_code == 200
    assert res_grade.status_code == 200
    assert res_results.json() == res_grade.json()


def test_attempt_tanpa_skor_dan_tanpa_rujukan_tetap_none():
    """Nilai tidak diam-diam menjadi 1.00 atau 4.00 bila tidak ada rujukan atau kriteria yang dinilai."""
    case_in = CaseGradeInput(
        snapshot_item_id=uuid.uuid4(),
        case_id=uuid.uuid4(),
        case_code="C-UNSCORED",
        title="Kasus Tanpa Skor",
        legal_refs=[],
        answer_criteria=None,
        attempts=[
            AttemptGradeInput(
                attempt_id=uuid.uuid4(),
                repetition_no=1,
                status=AttemptStatus.SUCCEEDED,
                answer_text="Jawaban tanpa sitasi hukum dan tanpa kriteria",
                deterministic_score=None,
                judge_score=None,
            )
        ],
    )
    res = grade_session_data(
        session_id=uuid.uuid4(),
        cases_input=[case_in],
        triggered_by=uuid.uuid4(),
    )
    assert res.cases[0].scores is None
    assert res.cases[0].attempts[0].score is None


def test_developer_ai_category_scores_mengabaikan_kasus_test():
    """Developer AI hanya melihat category_scores untuk kasus dev, tidak membocorkan performa test per kategori."""
    dev_case = CaseGradeResult(
        snapshot_item_id=uuid.uuid4(),
        case_id=uuid.uuid4(),
        case_code="DEV-01",
        title="Kasus Dev",
        split_tag=SplitTag.DEV,
        category="Hukum Perdata",
        question="Pertanyaan Dev",
        scores=ScorePair(deterministic=Decimal("1.00"), judge=Decimal("5.00")),
    )
    test_case = CaseGradeResult(
        snapshot_item_id=uuid.uuid4(),
        case_id=uuid.uuid4(),
        case_code="TEST-01",
        title="Kasus Test",
        split_tag=SplitTag.TEST,
        category="Hukum Pidana",
        question="Pertanyaan Test Rahasia",
        scores=ScorePair(deterministic=Decimal("0.50"), judge=Decimal("2.00")),
    )
    grading = SessionGradingResponse(
        grading_run_id=uuid.uuid4(),
        session_id=uuid.uuid4(),
        rubric_version="v1",
        kind="initial",
        status="completed",
        triggered_by=uuid.uuid4(),
        triggered_at=datetime.now(UTC),
        is_latest=True,
        scores=ThreeScores(
            dev=ScorePair(deterministic=Decimal("1.00"), judge=Decimal("5.00")),
            test=ScorePair(deterministic=Decimal("0.50"), judge=Decimal("2.00")),
            combined=ScorePair(deterministic=Decimal("0.75"), judge=Decimal("3.50")),
        ),
        category_scores=[
            CategoryScore(
                category="Hukum Perdata",
                case_count=1,
                scores=ScorePair(deterministic=Decimal("1.00"), judge=Decimal("5.00")),
            ),
            CategoryScore(
                category="Hukum Pidana",
                case_count=1,
                scores=ScorePair(deterministic=Decimal("0.50"), judge=Decimal("2.00")),
            ),
        ],
        cases=[dev_case, test_case],
    )

    res = apply_visibility_mask(
        grading, viewer_role=Role.DEVELOPER_AI, session_status=SessionStatus.FINISHED
    )
    categories = [cat.category for cat in res.category_scores]
    assert "Hukum Perdata" in categories
    assert "Hukum Pidana" not in categories


def test_db_grading_store_persists_across_instances(db_session, produk, pembuat):
    """Grading run tersimpan di database via AuditLog dan terbaca oleh instance DbGradingStore baru tanpa commit manual."""
    sesi = _setup_sesi_lengkap(db_session, produk, pembuat, status=SessionStatus.FINISHED)
    store1 = DbGradingStore(db_session)

    res = grade_session_data(
        session_id=sesi.id,
        cases_input=[],
        triggered_by=pembuat.id,
    )
    store1.save_grading_run(res)

    store2 = DbGradingStore(db_session)
    runs = store2.get_grading_runs(sesi.id)
    assert len(runs) >= 1
    assert runs[-1].session_id == sesi.id
    assert runs[-1].is_latest is True


def test_judge_error_dari_call_logs_terdeteksi_gangguan_teknis(
    as_role, db_session, produk, pembuat
):
    """Error penilai LLM pada CallLog database terdeteksi sebagai gangguan teknis (AC4)."""
    sesi = _setup_sesi_lengkap(db_session, produk, pembuat, status=SessionStatus.FINISHED)
    attempts = db_session.scalars(select(Attempt).where(Attempt.session_id == sesi.id)).all()
    target_attempt = attempts[0]

    now = datetime.now(UTC)
    log = CallLog(
        attempt_id=target_attempt.id,
        product_id=produk.id,
        started_at=now,
        ended_at=now,
        duration_ms=100,
        usd_idr_rate=Decimal("16000.00"),
        model_version="judge-model-v1",
        failure_kind="judge_error",
        result="failure",
        raw="{}",
        try_no=1,
    )
    db_session.add(log)
    db_session.commit()

    admin = as_role(Role.ADMIN)
    res = admin.post(f"/measurements/{sesi.id}/grade", json={})
    assert res.status_code == 200
    cases = res.json()["cases"]
    c1 = next(c for c in cases if c["snapshot_item_id"] == str(target_attempt.snapshot_item_id))
    att1 = c1["attempts"][0]
    assert att1["is_technical_disruption"] is True
    assert att1["score"] is None


def test_grade_session_commits_trigger_and_timestamp_audit_record(
    as_role, db_session, produk, pembuat
):
    """Pemicu dan waktu penilaian tersimpan dan ter-commit otomatis di database (AC6, AC7)."""
    sesi = _setup_sesi_lengkap(db_session, produk, pembuat, status=SessionStatus.FINISHED)
    admin = as_role(Role.ADMIN)

    res = admin.post(f"/measurements/{sesi.id}/grade", json={"rubric_version": "v1"})
    assert res.status_code == 200
    data = res.json()
    assert data["triggered_by"] is not None
    assert data["triggered_at"] is not None

    # Verifikasi langsung ke database audit log
    audit_entry = db_session.scalars(
        select(AuditLog)
        .where(
            AuditLog.entity_id == sesi.id,
            AuditLog.action.in_(("measurement_session.graded", "measurement_session.regraded")),
        )
        .order_by(AuditLog.occurred_at.desc())
    ).first()

    assert audit_entry is not None
    assert str(audit_entry.actor_user_id) == data["triggered_by"]
    assert audit_entry.occurred_at.replace(tzinfo=UTC).isoformat() == data["triggered_at"].replace(
        "Z", "+00:00"
    )
    assert audit_entry.after is not None
    assert audit_entry.after["session_id"] == str(sesi.id)
