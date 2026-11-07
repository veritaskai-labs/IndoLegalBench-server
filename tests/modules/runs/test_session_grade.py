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
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.modules.audit.models import AuditLog
from app.modules.auth.models import User
from app.modules.cases.models import SplitTag
from app.modules.cases.sections import snapshot_body
from app.modules.cases.service import MASKED_CASE_CODE, MASKED_TITLE
from app.modules.grading.models import RubricVersion
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
    _extract_criteria_items,
    _get_jenis_catalog,
    _is_judge_failure,
    _load_call_logs_metadata,
    _resolve_rubric_version,
    aggregate_case_scores,
    apply_visibility_mask,
    check_technical_disruption,
    compute_category_scores,
    ensure_session_gradable,
    get_session_results,
    grade_session,
    grade_session_data,
    is_ai_refusal,
    is_empty_answer,
    list_graded_sessions,
    normalize_legal_refs,
    round_two_digits,
    score_deterministic_citations,
    score_judge_criteria,
)
from app.shared.exceptions import ConflictError, ForbiddenError, NotFoundError
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
            "answer_criteria": {
                "must_contain": ["badan hukum", "pesangon"],
                "must_not_contain": [],
                "expected_conclusion": "sah",
            },
            "traps": [],
        },
        legal_refs=[
            {
                "kind": "structured",
                "free_text": None,
                "links": [
                    {
                        "regulation_type": "uu",
                        "number": "40",
                        "year": 2007,
                        "pasal": "156",
                        "angka": "1",
                    }
                ],
            }
        ],
    )


def _setup_sesi_lengkap(db_session, produk, pembuat, status=SessionStatus.FINISHED):
    rubric = db_session.scalars(select(RubricVersion).where(RubricVersion.version_no == 1)).first()
    if rubric is None:
        rubric = RubricVersion(
            id=uuid.uuid4(),
            version_no=1,
            effective_date=date.today(),
            is_active=True,
            locked_at=datetime.now(UTC),
            judge_prompt_quality="Default quality prompt",
            judge_prompt_format="Default format prompt",
            created_by=pembuat.id,
        )
        db_session.add(rubric)
        db_session.flush()

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
            answer_text=(
                "Berdasarkan Pasal 156 angka 1 Undang-Undang Nomor 40 Tahun 2007 tentang Perseroan Terbatas, "
                "badan hukum pesangon adalah sah."
            ),
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
            answer_text=(
                "Berdasarkan Pasal 156 ayat (1) Undang-Undang Nomor 40 Tahun 2007, "
                "badan hukum pesangon adalah sah."
            ),
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
    assert test_c["category"] is None
    assert test_c["attempts"][0]["answer_text"] is None
    assert test_c["attempts"][0]["score"] is None
    assert test_c["attempts"][0]["explanation"] is None
    assert test_c["attempts"][0]["reasons"] == []
    assert test_c["attempts"][0]["criteria"] == []
    assert test_c["attempts"][0]["source"] == "automatic"

    # Kasus dev tidak tersamar
    dev_c = next(c for c in dev_cases if c["masked"] is False)
    assert dev_c["case_code"] == "DEV-01"
    assert dev_c["question"] == "Pertanyaan Dev Uji"
    assert dev_c["scores"] is not None


def test_regrade_via_api(as_role, db_session, produk, pembuat):
    """AC7: Nilai ulang via API menandai kind='regrade' dan validasi versi rubrik."""
    sesi = _setup_sesi_lengkap(db_session, produk, pembuat, status=SessionStatus.FINISHED)
    admin = as_role(Role.ADMIN)

    # Seed versi v2 di tabel rubric_versions (v1 sudah dibuat oleh _setup_sesi_lengkap)
    v2 = db_session.scalars(select(RubricVersion).where(RubricVersion.version_no == 2)).first()
    if v2 is None:
        v2 = RubricVersion(
            id=uuid.uuid4(),
            version_no=2,
            effective_date=date.today(),
            is_active=False,
            locked_at=datetime.now(UTC),
            judge_prompt_quality="Quality prompt v2",
            judge_prompt_format="Format prompt v2",
            created_by=pembuat.id,
        )
        db_session.add(v2)
        db_session.commit()

    res1 = admin.post(f"/measurements/{sesi.id}/grade", json={"rubric_version": "v1"})
    assert res1.status_code == 200
    assert res1.json()["kind"] == "initial"
    assert res1.json()["rubric_version"] == "v1"

    res2 = admin.post(f"/measurements/{sesi.id}/grade", json={"rubric_version": "v2"})
    assert res2.status_code == 200
    assert res2.json()["kind"] == "regrade"
    assert res2.json()["rubric_version"] == "v2"
    assert res2.json()["is_latest"] is True

    # Unknown version ditolak 404 (AC7)
    res_unknown = admin.post(f"/measurements/{sesi.id}/grade", json={"rubric_version": "v99"})
    assert res_unknown.status_code == 404


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


def test_edge_cases_and_error_handling(db_session, produk, pembuat, monkeypatch):
    """Pengujian cabang dan penanganan error untuk mencapai 100% coverage."""
    # 1. round_two_digits(None)
    assert round_two_digits(None) is None

    # 2. score_deterministic_citations edge cases
    assert score_deterministic_citations("", [{"number": "40"}]) is None
    assert score_deterministic_citations(None, [{"number": "40"}]) is None
    assert (
        score_deterministic_citations("Sesuai UU 40 tentang perseroan", [{"number": "40"}]) is None
    )
    assert score_deterministic_citations(
        "Sesuai UU 40 Tahun 2007 tentang perseroan", [{"number": "40", "year": 2007}]
    ) == Decimal("1.00")

    # 3. score_judge_criteria formatting branches and real AnswerCriteria
    real_crit = {
        "must_contain": ["badan hukum", "perseroan"],
        "must_not_contain": ["ilegal"],
        "expected_conclusion": "sah",
    }
    assert score_judge_criteria(
        "Badan hukum perseroan ini sah menurut undang-undang.",
        real_crit,
    ) == Decimal("1.00")
    assert score_judge_criteria(
        "Badan hukum ini sah namun ilegal.",
        real_crit,
    ) == Decimal("0.50")

    crit_a = "kriteria pertama harus dipenuhi sepenuhnya"
    crit_b = "kriteria kedua harus dipenuhi sepenuhnya"
    assert score_judge_criteria(
        "jawaban di sini kriteria pertama harus dipenuhi sepenuhnya",
        {"items": [crit_a, crit_b]},
    ) == Decimal("0.50")
    assert score_judge_criteria(
        "kriteria pertama harus dipenuhi sepenuhnya", {"kriteria": [crit_a]}
    ) == Decimal("1.00")
    assert score_judge_criteria(
        "jawaban di sini kriteria pertama harus dipenuhi sepenuhnya", [crit_a, crit_b]
    ) == Decimal("0.50")
    assert score_judge_criteria("jawaban", {"catatan": ""}) is None
    assert score_judge_criteria(
        "jawaban acak tanpa kata kunci",
        {"items": ["singkat", "dua kata"]},
    ) == Decimal("0.00")
    assert score_judge_criteria(
        "jawaban memuat kata singkat di sini",
        {"items": ["singkat", "dua kata"]},
    ) == Decimal("0.50")

    # Real LegalRefStored deterministic scoring & refusal with disclaimer
    real_ref = {
        "kind": "structured",
        "free_text": None,
        "links": [
            {
                "regulation_type": "UU",
                "number": "40",
                "year": 2007,
                "pasal": "1",
                "angka": "1",
            }
        ],
    }
    assert score_deterministic_citations(
        "Pasal 1 angka 1 Undang-Undang Nomor 40 Tahun 2007 menyatakan...",
        [real_ref],
    ) == Decimal("1.00")
    assert score_deterministic_citations(
        "Pasal 1 angka 1 Peraturan Pemerintah Nomor 40 Tahun 2007 menyatakan...",
        [real_ref],
    ) == Decimal("0.00")

    disclaimer_ans = (
        "Saya tidak dapat memberikan nasihat hukum yang mengikat, namun berdasarkan "
        "Undang-Undang Nomor 40 Tahun 2007 Pasal 156, perseroan terbatas wajib mematuhi aturan ini."
    )
    assert is_ai_refusal(disclaimer_ans) is False
    assert is_ai_refusal("Maaf, saya tidak dapat menjawab pertanyaan ini.") is True

    # 4. MemoryGradingStore get_latest_grading_run
    mem_store = MemoryGradingStore()
    dummy_id = uuid.uuid4()
    assert mem_store.get_latest_grading_run(dummy_id) is None
    run1 = SessionGradingResponse(
        grading_run_id=uuid.uuid4(),
        session_id=dummy_id,
        rubric_version="v1",
        kind="initial",
        status="completed",
        triggered_by=pembuat.id,
        triggered_at=datetime.now(UTC),
        is_latest=True,
        scores=ThreeScores(dev=ScorePair(), test=ScorePair(), combined=ScorePair()),
        category_scores=[],
        cases=[],
    )
    mem_store.save_grading_run(run1)
    assert mem_store.get_latest_grading_run(dummy_id) == run1

    # 5. DbGradingStore invalid audit log and fallback
    log_invalid = AuditLog(
        occurred_at=datetime.now(UTC),
        actor_user_id=pembuat.id,
        actor_role=Role.ADMIN.value,
        action="measurement_session.graded",
        entity_type="measurement_session",
        entity_id=dummy_id,
        after={"rubric_version": 123},  # Invalid model payload to trigger except
        reason="test",
    )
    db_session.add(log_invalid)
    db_session.commit()
    db_store = DbGradingStore(db_session)
    runs = db_store.get_grading_runs(dummy_id)
    assert runs == []
    assert db_store.get_latest_grading_run(dummy_id) is None

    # MemoryGradingStore latest search
    mem_store2 = MemoryGradingStore()
    run_old = run1.model_copy(update={"is_latest": False})
    run_new = run1.model_copy(update={"is_latest": True, "grading_run_id": uuid.uuid4()})
    mem_store2.save_grading_run(run_old)
    mem_store2.save_grading_run(run_new)
    assert mem_store2.get_latest_grading_run(dummy_id) == run_new

    # 6. apply_visibility_mask unknown role
    with pytest.raises(ForbiddenError, match="Peran Anda tidak berwenang"):
        apply_visibility_mask(
            run1, viewer_role="UNKNOWN_ROLE", session_status=SessionStatus.FINISHED
        )

    # 7. grade_session actor_role != Role.ADMIN
    sesi = _setup_sesi_lengkap(db_session, produk, pembuat, status=SessionStatus.FINISHED)
    with pytest.raises(ForbiddenError, match="Hanya Admin"):
        grade_session(db_session, sesi.id, actor_id=pembuat.id, actor_role=Role.VIEWER)

    # 8. get_session_results MEASUREMENT_NOT_FOUND
    with pytest.raises(NotFoundError, match="Pengukuran tidak ditemukan"):
        get_session_results(db_session, uuid.uuid4(), viewer_role=Role.ADMIN)

    # 9. get_session_results GRADING_NOT_FOUND (session exists but has never been graded)
    sesi_ungraded = _setup_sesi_lengkap(db_session, produk, pembuat, status=SessionStatus.FINISHED)
    with pytest.raises(NotFoundError, match="Hasil penilaian belum tersedia"):
        get_session_results(db_session, sesi_ungraded.id, viewer_role=Role.ADMIN)

    # 10. Helper branch coverage: empty legal ref, non-digit article, empty logs, None failure
    assert score_deterministic_citations(
        "jawaban UU 40/2007 Pasal 1", [{"number": "40", "year": "2007", "article": "1"}]
    ) == Decimal("1.00")
    assert score_deterministic_citations(
        "jawaban 40/2007", [{"number": "40", "year": "2007", "article": "pasal tanpa nomor"}]
    ) == Decimal("0.00")
    assert (
        score_deterministic_citations("jawaban", [{"number": "", "year": "", "article": "pasal 1"}])
        is None
    )
    assert _is_judge_failure(None) is False
    assert _load_call_logs_metadata(db_session, []) == ({}, {})

    # 11. is_ai_refusal branch coverage
    assert is_ai_refusal(None) is False
    assert is_ai_refusal("") is False
    assert is_ai_refusal("Catatan: [Platform menolak menjawab] sepenuhnya.") is True
    long_refusal_short_words = "maaf, saya tidak dapat menjawab " + (
        "frasa_sangat_panjang_sekali " * 15
    )
    assert len(long_refusal_short_words) > 250
    assert len(long_refusal_short_words.split()) <= 40
    assert is_ai_refusal(long_refusal_short_words) is True
    long_refusal_many_words = "maaf, saya tidak dapat menjawab " + ("kata " * 45)
    assert len(long_refusal_many_words) > 250
    assert len(long_refusal_many_words.split()) > 40
    assert is_ai_refusal(long_refusal_many_words) is False

    # 12. _get_jenis_catalog & normalize_legal_refs edge cases
    class BadDB:
        pass

    assert _get_jenis_catalog(BadDB()) is not None
    monkeypatch.setattr(
        "app.modules.runs.session_grade.load_regulation_types",
        lambda: (_ for _ in ()).throw(RuntimeError("fail")),
    )
    assert _get_jenis_catalog() == {}
    assert normalize_legal_refs([{"kind": "free_text", "free_text": "bebas"}]) == [
        {"kind": "free_text", "free_text": "bebas"}
    ]

    # 13. score_judge_criteria and _extract_criteria_items edge cases
    assert score_judge_criteria("jawaban", None) is None
    assert score_judge_criteria(
        "jawaban catatan penting", {"catatan": "catatan penting"}
    ) == Decimal("1.00")
    assert _extract_criteria_items(None) == ([], [], None)
    assert _extract_criteria_items(12345) == ([], [], None)

    # 14. _resolve_rubric_version UUID and non-digit lookups
    db_session.query(RubricVersion).filter(RubricVersion.is_active).update({"is_active": False})
    db_session.commit()
    active_rubric = RubricVersion(
        id=uuid.uuid4(),
        version_no=12,
        effective_date=date.today(),
        is_active=True,
        locked_at=datetime.now(UTC),
        judge_prompt_quality="Quality prompt v12",
        judge_prompt_format="Format prompt v12",
        created_by=pembuat.id,
    )
    db_session.add(active_rubric)
    db_session.commit()

    with pytest.raises(NotFoundError, match="tidak ditemukan"):
        _resolve_rubric_version(db_session, str(uuid.uuid4()))
    with pytest.raises(NotFoundError, match="tidak ditemukan"):
        _resolve_rubric_version(db_session, "non-numeric-rubric-version")

    assert _resolve_rubric_version(db_session, None) == "v12"

    # 15. grade_session with non-DbGradingStore
    sesi = _setup_sesi_lengkap(db_session, produk, pembuat)
    mem_store_custom = MemoryGradingStore()
    resp_mem = grade_session(
        db_session,
        sesi.id,
        actor_id=pembuat.id,
        actor_role=Role.ADMIN,
        store=mem_store_custom,
    )
    assert resp_mem.grading_run_id is not None


def test_session_label_and_product_name_in_results(db_session, produk, pembuat):
    """Memastikan session_label dan product_name tercantum di hasil penilaian (SCRUM-226/227)."""
    sesi = _setup_sesi_lengkap(db_session, produk, pembuat, status=SessionStatus.FINISHED)
    graded = grade_session(db_session, sesi.id, actor_id=pembuat.id, actor_role=Role.ADMIN)

    assert graded.session_label == sesi.label
    assert graded.product_name == produk.name

    fetched = get_session_results(db_session, sesi.id, viewer_role=Role.VIEWER)
    assert fetched.session_label == sesi.label
    assert fetched.product_name == produk.name

    # Fallback saat grading run yang tersimpan belum memiliki session_label/product_name
    old_run = graded.model_copy(update={"session_label": None, "product_name": None})
    mock_store = MemoryGradingStore()
    mock_store.save_grading_run(old_run)
    fetched_fallback = get_session_results(
        db_session, sesi.id, viewer_role=Role.ADMIN, store=mock_store
    )
    assert fetched_fallback.session_label == sesi.label
    assert fetched_fallback.product_name == produk.name


def test_list_graded_sessions_logic(db_session, produk, pembuat):
    """Memastikan list_graded_sessions hanya mengembalikan sesi selesai yang memiliki hasil penilaian."""
    # 1. Saat belum ada sesi selesai dinilai -> []
    assert list_graded_sessions(db_session, viewer_role=Role.VIEWER) == []

    # 2. Peran Kontributor & Developer AI ditolak
    with pytest.raises(ForbiddenError, match="tidak berwenang"):
        list_graded_sessions(db_session, viewer_role=Role.CONTRIBUTOR)
    with pytest.raises(ForbiddenError, match="tidak berwenang"):
        list_graded_sessions(db_session, viewer_role=Role.DEVELOPER_AI)

    # 3. Sesi running tidak masuk
    _setup_sesi_lengkap(db_session, produk, pembuat, status=SessionStatus.RUNNING)
    assert list_graded_sessions(db_session, viewer_role=Role.ADMIN) == []

    # 4. Sesi selesai tetapi belum dinilai -> tidak masuk
    sesi_finished = _setup_sesi_lengkap(db_session, produk, pembuat, status=SessionStatus.FINISHED)
    assert list_graded_sessions(db_session, viewer_role=Role.ADMIN) == []

    # 5. Sesi selesai dan sudah dinilai -> masuk
    grade_session(db_session, sesi_finished.id, actor_id=pembuat.id, actor_role=Role.ADMIN)
    results = list_graded_sessions(db_session, viewer_role=Role.VIEWER)
    assert len(results) == 1
    item = results[0]
    assert item.session_id == sesi_finished.id
    assert item.label == sesi_finished.label
    assert item.product_name == produk.name
    assert item.rubric_version == "v1"

    # 6. Sesi diarsipkan -> tidak muncul
    sesi_finished.archived_at = datetime.now(UTC)
    sesi_finished.archived_by = pembuat.id
    db_session.commit()
    assert list_graded_sessions(db_session, viewer_role=Role.ADMIN) == []

    # 7. Fallback AuditLog: sesi selesai dengan grading di AuditLog tetapi tanpa baris GradingRun
    sesi_audit = _setup_sesi_lengkap(db_session, produk, pembuat, status=SessionStatus.FINISHED)
    log = AuditLog(
        occurred_at=datetime.now(UTC),
        actor_user_id=pembuat.id,
        actor_role=Role.ADMIN.value,
        action="measurement_session.graded",
        entity_type="measurement_session",
        entity_id=sesi_audit.id,
        after={"rubric_version": "v2"},
        reason="manual grade audit",
    )
    db_session.add(log)
    db_session.commit()
    audit_results = list_graded_sessions(db_session, viewer_role=Role.ADMIN)
    assert any(r.session_id == sesi_audit.id and r.rubric_version == "v2" for r in audit_results)


def test_list_graded_sessions_api_endpoints(as_role, db_session, produk, pembuat):
    """Memastikan endpoint GET /measurements/results/sessions dan GET /results/sessions dapat diakses Viewer."""
    sesi = _setup_sesi_lengkap(db_session, produk, pembuat, status=SessionStatus.FINISHED)
    grade_session(db_session, sesi.id, actor_id=pembuat.id, actor_role=Role.ADMIN)

    # Viewer can access /measurements/results/sessions
    res1 = as_role(Role.VIEWER).get("/measurements/results/sessions")
    assert res1.status_code == 200
    data1 = res1.json()
    assert len(data1) >= 1
    assert data1[0]["session_id"] == str(sesi.id)
    assert data1[0]["label"] == sesi.label
    assert data1[0]["product_name"] == produk.name

    # Viewer can access /results/sessions alias
    res2 = as_role(Role.VIEWER).get("/results/sessions")
    assert res2.status_code == 200
    data2 = res2.json()
    assert data2 == data1

    # Contributor & Developer AI are rejected with 403
    res3 = as_role(Role.CONTRIBUTOR).get("/results/sessions")
    assert res3.status_code == 403
    res4 = as_role(Role.DEVELOPER_AI).get("/results/sessions")
    assert res4.status_code == 403


class _MockJudgeAsker:
    """Mock asker untuk penilai LLM SCRUM-224."""

    def __init__(
        self,
        text: str = "",
        error: str | None = None,
        failure_kind: str | None = None,
        raise_exc: bool = False,
    ) -> None:
        self.text = text
        self.error = error
        self.failure_kind = failure_kind
        self.raise_exc = raise_exc
        self.input_tokens = 25
        self.output_tokens = 30

    def ask(self, prompt: str):
        if self.raise_exc:
            raise RuntimeError("API timeout simulation")
        return self


def test_llm_judge_integration_and_d6_persistence(db_session, produk, pembuat):
    """Integrasi SCRUM-224 run_judge, persistensi tabel D6, dan pembacaan ulang metadata."""
    from app.modules.grading.models import LlmCriterionResult, LlmJudgeResult

    sesi = _setup_sesi_lengkap(db_session, produk, pembuat, status=SessionStatus.FINISHED)
    reply_text = (
        "CRITERION: badan hukum\n"
        "MET: yes\n"
        "CRITERION: pesangon\n"
        "MET: no\n"
        "EXPLANATION: pesangon tidak disebutkan secara eksplisit\n"
        "CRITERION: Conclusion: sah\n"
        "MET: yes\n"
    )
    asker = _MockJudgeAsker(text=reply_text)

    # 1. Grade sesi dengan judge_asker
    response = grade_session(
        db_session,
        sesi.id,
        actor_id=pembuat.id,
        actor_role=Role.ADMIN,
        judge_asker=asker,
    )
    assert response.status == "completed"

    dev_case = next(c for c in response.cases if c.case_code == "DEV-01")
    dev_att = dev_case.attempts[0]
    assert dev_att.score is not None
    assert dev_att.score.judge is not None
    assert dev_att.score.judge == Decimal("0.67")
    assert "pesangon tidak disebutkan" in (dev_att.explanation or "")
    assert len(dev_att.criteria) == 3

    # 2. Verifikasi baris tersimpan di D6 tabel llm_judge_results dan llm_criterion_results
    judge_rows = db_session.scalars(select(LlmJudgeResult)).all()
    assert len(judge_rows) >= 1
    j_row = judge_rows[0]
    assert j_row.criteria_score == Decimal("0.67")
    assert j_row.status.value == "succeeded"

    crit_rows = db_session.scalars(select(LlmCriterionResult)).all()
    assert len(crit_rows) >= 3
    unmet_c = next(c for c in crit_rows if not c.is_met)
    assert unmet_c.reason_code == "unmet"
    met_c = next(c for c in crit_rows if c.is_met)
    assert met_c.reason_code is None  # ck_llm_criterion_results_reason_unmet

    # 3. Pembacaan ulang dari database oleh _load_case_grade_inputs
    from app.modules.runs.session_grade import _load_case_grade_inputs

    inputs = _load_case_grade_inputs(db_session, sesi.id)
    dev_input = next(c for c in inputs if c.case_code == "DEV-01")
    att_in = dev_input.attempts[0]
    assert att_in.judge_score == Decimal("0.67")
    assert "pesangon tidak disebutkan" in (att_in.explanation or "")
    assert len(att_in.criteria) == 3


def test_coverage_edge_cases_and_uncovered_branches(db_session, produk, pembuat, monkeypatch):
    """Memastikan 100% statement coverage untuk fungsi-fungsi helper di session_grade."""
    from app.modules.runs.session_grade import (
        _execute_judge_for_attempt,
        _load_judge_metadata_for_attempts,
        _resolve_rubric_version,
        evaluate_criteria_details,
        is_ai_refusal,
    )

    # 1. is_ai_refusal dengan kata substansi hukum (line 155)
    disclaimer_with_legal = (
        "Saya tidak dapat memberikan nasihat hukum. Berdasarkan Pasal 156 UU Ketenagakerjaan..."
    )
    assert is_ai_refusal(disclaimer_with_legal) is False

    # 2. evaluate_criteria_details dengan kesimpulan tidak terpenuhi (lines 464-465)
    crit_concl = {"expected_conclusion": "bersalah"}
    res_concl = evaluate_criteria_details("Terdakwa dinyatakan bebas murni.", crit_concl)
    assert res_concl is not None
    score, exp, reasons, c_list = res_concl
    assert score == Decimal("0.00")
    assert exp is not None and "Kesimpulan tidak memuat" in exp
    assert reasons == ["unmet"]

    # 3. DbGradingStore._resolve_db_rubric_version_id cabang fallback
    store = DbGradingStore(db_session)
    # Saat versi tidak cocok tetapi ada versi aktif
    resolved_id = store._resolve_db_rubric_version_id("v9999", pembuat.id)
    assert resolved_id is not None
    # Saat tidak ada versi di DB sama sekali
    db_session.query(RubricVersion).delete()
    db_session.commit()
    created_id = store._resolve_db_rubric_version_id("v9999", pembuat.id)
    assert created_id is not None
    # _resolve_rubric_version saat tidak ada versi aktif -> DEFAULT_RUBRIC_VERSION ("v1")
    db_session.query(RubricVersion).filter(RubricVersion.is_active).update({"is_active": False})
    db_session.commit()
    assert _resolve_rubric_version(db_session, None) == "v1"

    # 4. _load_judge_metadata_for_attempts dengan daftar kosong (line 1084)
    assert _load_judge_metadata_for_attempts(db_session, []) == {}

    # 5. _execute_judge_for_attempt error handling
    case_in = CaseGradeInput(
        snapshot_item_id=uuid.uuid4(),
        case_id=uuid.uuid4(),
        case_code="ERR-01",
        title="Kasus Error",
        answer_criteria={"must_contain": ["kriteria"]},
    )
    att_fail = AttemptGradeInput(
        attempt_id=uuid.uuid4(),
        repetition_no=1,
        status=AttemptStatus.SUCCEEDED,
        answer_text="Jawaban",
    )
    # Penilai mengembalikan error
    err_asker = _MockJudgeAsker(error="provider timeout", failure_kind="timeout")
    _execute_judge_for_attempt(err_asker, att_fail, case_in)
    assert att_fail.judge_error is not None

    # Penilai memicu exception
    att_exc = AttemptGradeInput(
        attempt_id=uuid.uuid4(),
        repetition_no=1,
        status=AttemptStatus.SUCCEEDED,
        answer_text="Jawaban",
    )
    exc_asker = _MockJudgeAsker(raise_exc=True)
    _execute_judge_for_attempt(exc_asker, att_exc, case_in)
    assert att_exc.judge_error is not None

    # Case tanpa kriteria khusus
    case_empty = CaseGradeInput(
        snapshot_item_id=uuid.uuid4(),
        case_id=uuid.uuid4(),
        case_code="EMPTY-01",
        title="Kasus Tanpa Kriteria",
        answer_criteria={},
    )
    att_empty = AttemptGradeInput(
        attempt_id=uuid.uuid4(),
        repetition_no=1,
        status=AttemptStatus.SUCCEEDED,
        answer_text="Jawaban",
    )
    _execute_judge_for_attempt(
        _MockJudgeAsker(text="CRITERION: Kasus Tanpa Kriteria\nMET: yes\n"), att_empty, case_empty
    )
    assert att_empty.judge_score == Decimal("1.00")

    # 6. grade_session dengan session.judge_provider_id aktif
    sesi_judge = _setup_sesi_lengkap(db_session, produk, pembuat)
    sesi_judge.judge_provider_id = uuid.uuid4()
    db_session.commit()
    # build_judge akan gagal karena judge_provider_id acak, fallback ke active_asker = None
    res_judge_prov = grade_session(
        db_session, sesi_judge.id, actor_id=pembuat.id, actor_role=Role.ADMIN
    )
    assert res_judge_prov.status == "completed"

    # build_judge sukses memuat asker
    from unittest.mock import MagicMock

    mock_binding = MagicMock()
    mock_binding.asker = _MockJudgeAsker(text="CRITERION: badan hukum\nMET: yes\n")
    monkeypatch.setattr(
        "app.modules.providers.judge_service.build_judge", lambda db, j_id: mock_binding
    )
    res_judge_prov2 = grade_session(
        db_session, sesi_judge.id, actor_id=pembuat.id, actor_role=Role.ADMIN
    )
    assert res_judge_prov2.status == "completed"

    # 7. ScorePair quality, format, dan judge sync
    sp_qual = ScorePair(quality=Decimal("4.00"))
    assert sp_qual.judge == Decimal("4.00")
    sp_judge = ScorePair(judge=Decimal("3.00"))
    assert sp_judge.quality == Decimal("3.00")
    sp_empty = ScorePair()
    assert aggregate_case_scores([sp_empty]) is None

    # 8. _load_judge_metadata_for_attempts dengan format dan status failed
    from app.modules.grading.models import (
        GradingResult,
        JudgeKind,
        JudgeStatus,
        LlmJudgeResult,
    )
    from app.modules.runs.session_grade import _apply_judge_row_to_attempt

    test_att_id = uuid.uuid4()
    gr = GradingResult(
        id=uuid.uuid4(),
        grading_run_id=res_judge_prov2.grading_run_id,
        attempt_id=test_att_id,
        is_technical_disruption=True,
    )
    db_session.add(gr)
    db_session.flush()

    failed_j = LlmJudgeResult(
        id=uuid.uuid4(),
        grading_result_id=gr.id,
        judge_run_no=1,
        kind=JudgeKind.QUALITY,
        status=JudgeStatus.FAILED,
        prompt="prompt",
        criteria_met=None,
        criteria_total=None,
        criteria_score=None,
        error="timeout",
        failure_kind="judge_timeout",
    )
    format_j = LlmJudgeResult(
        id=uuid.uuid4(),
        grading_result_id=gr.id,
        judge_run_no=1,
        kind=JudgeKind.FORMAT,
        status=JudgeStatus.SUCCEEDED,
        prompt="format prompt",
        criteria_met=1,
        criteria_total=1,
        criteria_score=Decimal("0.90"),
    )
    db_session.add_all([failed_j, format_j])
    db_session.flush()

    loaded_data = _load_judge_metadata_for_attempts(db_session, [test_att_id])
    assert loaded_data[test_att_id]["format_score"] == Decimal("0.90")
    assert loaded_data[test_att_id]["judge_error"] == "timeout"

    # 9. _save_format_judge_record
    store._save_format_judge_record(gr.id, "CASE-01", 2, Decimal("0.85"))

    # 10. _apply_judge_row_to_attempt dengan kind format
    mock_fmt_row = MagicMock()
    mock_fmt_row.kind = "format"
    mock_fmt_row.score = Decimal("0.80")
    mock_fmt_row.error = None
    mock_fmt_row.failure_kind = None
    att_fmt_test = AttemptGradeInput(
        attempt_id=uuid.uuid4(),
        repetition_no=1,
        status=AttemptStatus.SUCCEEDED,
    )
    _apply_judge_row_to_attempt(att_fmt_test, mock_fmt_row)
    assert att_fmt_test.format_score == Decimal("0.80")
