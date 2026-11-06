"""Penilaian, pemicu manual, penanganan gangguan teknis, dan visibilitas hasil.

PBI-14 SCRUM-226 (AC4, AC6, AC7, AC8, AC9; PBI-11 AC9 dipindah).

Fitur utama:
1. Pemicu manual POST /measurements/{id}/grade (Admin only).
2. Penanganan gangguan teknis: jawaban kosong, AI menolak, atau error judge
   dihitung sebagai gangguan teknis (is_technical_disruption=True), tanpa skor,
   dilewati dari perhitungan rata-rata, dan dapat dicoba ulang manual via PBI-12.
3. Penilaian seragam: alur yang sama untuk percobaan otomatis maupun manual (AC9).
4. Riwayat nilai ulang: nilai ulang menandai hasil baru sebagai is_latest=True,
   hasil lama tetap tersimpan (AC7).
5. Visibilitas hasil & Server-enforced Masking (AC8, D10):
   - Sesi berjalan: hanya Admin. Viewer & Developer AI ditolak (403).
   - Sesi selesai: Admin & Viewer melihat hasil lengkap; Developer AI melihat kasus
     dev lengkap, sedangkan kasus test disamarkan per-kasus (pertanyaan, jawaban, skor
     kasus null; judul terproteksi), namun skor agregat test tetap terlihat di ThreeScores.
"""

from __future__ import annotations

import re
import threading
import uuid
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.cases.models import SplitTag
from app.modules.cases.service import MASKED_CASE_CODE, MASKED_TITLE
from app.modules.runs.models import (
    Attempt,
    AttemptSource,
    AttemptStatus,
    CallLog,
    CaseSnapshot,
    CaseSnapshotItem,
    MeasurementSession,
    SessionStatus,
)
from app.modules.runs.schemas import (
    AttemptGradeResult,
    CaseGradeResult,
    CategoryScore,
    GradeSessionRequest,
    ScorePair,
    SessionGradingResponse,
    ThreeScores,
)
from app.modules.runs.session_rules import has_ended
from app.shared.exceptions import ConflictError, ForbiddenError, NotFoundError
from app.shared.security import Role

_DUA_DIGIT = Decimal("0.01")
LAIN_LAIN = "Lain-lain"
DEFAULT_RUBRIC_VERSION = "v1"

_REFUSAL_PHRASES = (
    "maaf, saya tidak dapat menjawab",
    "maaf, saya tidak bisa menjawab",
    "maaf, sebagai model ai",
    "maaf, sebagai asisten ai",
    "maaf, saya tidak berwenang",
    "saya tidak dapat memberikan nasihat hukum",
    "saya tidak memiliki wewenang untuk memberikan pertimbangan hukum",
    "as an ai, i cannot",
    "i cannot assist with this request",
    "i am unable to provide legal advice",
)

_TECHNICAL_FAILURE_KINDS = frozenset(
    {
        "no_response",
        "provider_error",
        "empty_answer",
        "safety_blocked",
        "truncated",
        "ai_refused",
        "timeout",
        "invalid_response",
    }
)


def round_two_digits(val: Decimal | float | int | None) -> Decimal | None:
    """Membulatkan angka ke 2 digit desimal (ROUND_HALF_UP)."""
    if val is None:
        return None
    d = val if isinstance(val, Decimal) else Decimal(str(val))
    return d.quantize(_DUA_DIGIT, rounding=ROUND_HALF_UP)


def is_empty_answer(text: str | None) -> bool:
    """Jawaban kosong atau hanya berisi whitespace."""
    return text is None or not text.strip()


def is_ai_refusal(text: str | None, failure_kind: str | None = None) -> bool:
    """Deteksi model AI menolak menjawab (AC4)."""
    if failure_kind == "ai_refused":
        return True
    if text:
        text_lower = text.lower().strip()
        for phrase in _REFUSAL_PHRASES:
            if phrase in text_lower:
                return True
    return False


def check_technical_disruption(
    *,
    status: AttemptStatus | str | None,
    answer_text: str | None,
    failure_kind: str | None = None,
    platform_refused: bool = False,
    judge_error: str | None = None,
) -> bool:
    """Menentukan apakah percobaan merupakan gangguan teknis (AC4).

    Semua kondisi berikut dihitung gangguan teknis tanpa skor (dilewati):
    - Jawaban kosong / whitespace saja.
    - AI menolak menjawab (kalimat penolakan sendiri atau flag failure_kind).
    - Status percobaan technical_failure atau failure_kind penyedia.
    - Opsi platform menolak menjawab (pada sesi manual).
    - Penilai LLM (judge) mengalami error atau gagal panggil.
    """
    if judge_error is not None:
        return True
    if platform_refused:
        return True
    status_str = status.value if isinstance(status, AttemptStatus) else (status or "")
    if status_str == AttemptStatus.TECHNICAL_FAILURE.value:
        return True
    if failure_kind and failure_kind in _TECHNICAL_FAILURE_KINDS:
        return True
    if is_empty_answer(answer_text):
        return True
    return bool(is_ai_refusal(answer_text, failure_kind))


def mean_scores(scores: Iterable[Decimal | None]) -> Decimal | None:
    """Rata-rata desimal 2 digit; nilai None dilewati (tidak dihitung 0)."""
    valid = [s for s in scores if s is not None]
    if not valid:
        return None
    return round_two_digits(sum(valid) / Decimal(len(valid)))


def aggregate_case_scores(
    attempt_scores: Sequence[ScorePair | None],
) -> ScorePair | None:
    """Rata-rata skor percobaan dalam satu kasus.

    Percobaan gangguan teknis (skor None) dilewati, sehingga tidak merusak
    skor rata-rata kasus (AC4).
    """
    det_scores: list[Decimal | None] = []
    judge_scores: list[Decimal | None] = []

    for item in attempt_scores:
        if item is not None:
            if item.deterministic is not None:
                det_scores.append(item.deterministic)
            if item.judge is not None:
                judge_scores.append(item.judge)

    det_mean = mean_scores(det_scores)
    judge_mean = mean_scores(judge_scores)

    if det_mean is None and judge_mean is None:
        return None
    return ScorePair(deterministic=det_mean, judge=judge_mean)


def compute_three_scores(cases: Sequence[CaseGradeResult]) -> ThreeScores:
    """Tiga skor terpisah: kasus dev, kasus test, dan gabungan (PBI-11 AC9)."""
    dev_det: list[Decimal | None] = []
    dev_judge: list[Decimal | None] = []
    test_det: list[Decimal | None] = []
    test_judge: list[Decimal | None] = []
    comb_det: list[Decimal | None] = []
    comb_judge: list[Decimal | None] = []

    for c in cases:
        if c.scores is not None:
            tag = c.split_tag.value if isinstance(c.split_tag, SplitTag) else c.split_tag
            if tag == SplitTag.DEV.value:
                if c.scores.deterministic is not None:
                    dev_det.append(c.scores.deterministic)
                if c.scores.judge is not None:
                    dev_judge.append(c.scores.judge)
            elif tag == SplitTag.TEST.value:
                if c.scores.deterministic is not None:
                    test_det.append(c.scores.deterministic)
                if c.scores.judge is not None:
                    test_judge.append(c.scores.judge)

            if c.scores.deterministic is not None:
                comb_det.append(c.scores.deterministic)
            if c.scores.judge is not None:
                comb_judge.append(c.scores.judge)

    return ThreeScores(
        dev=ScorePair(deterministic=mean_scores(dev_det), judge=mean_scores(dev_judge)),
        test=ScorePair(deterministic=mean_scores(test_det), judge=mean_scores(test_judge)),
        combined=ScorePair(deterministic=mean_scores(comb_det), judge=mean_scores(comb_judge)),
    )


def compute_category_scores(cases: Sequence[CaseGradeResult]) -> list[CategoryScore]:
    """Pecahan per kategori; kasus tanpa kategori dihitung 'Lain-lain' (AC10)."""
    groups: dict[str, list[CaseGradeResult]] = defaultdict(list)
    for c in cases:
        cat = (c.category or "").strip()
        name = cat if cat else LAIN_LAIN
        groups[name].append(c)

    results: list[CategoryScore] = []
    for cat_name, cat_cases in sorted(groups.items()):
        det_scores = [
            c.scores.deterministic
            for c in cat_cases
            if c.scores and c.scores.deterministic is not None
        ]
        judge_scores = [
            c.scores.judge for c in cat_cases if c.scores and c.scores.judge is not None
        ]
        results.append(
            CategoryScore(
                category=cat_name,
                case_count=len(cat_cases),
                scores=ScorePair(
                    deterministic=mean_scores(det_scores),
                    judge=mean_scores(judge_scores),
                ),
            )
        )
    return results


def score_deterministic_citations(
    answer_text: str | None,
    legal_refs: Sequence[dict[str, Any] | Any],
) -> Decimal | None:
    """Menghitung skor deterministik berdasarkan pencocokan peraturan dan pasal (AC1, AC2).

    Rumus: total extracted sitasi benar / total real sitasi acuan.
    Jika kasus tidak memiliki rujukan acuan (legal_refs kosong), mengembalikan None (TBA).
    """
    if not legal_refs:
        return None
    if not answer_text or not answer_text.strip():
        return None

    text_lower = answer_text.lower()
    total_refs = len(legal_refs)
    matched_refs = 0

    for ref in legal_refs:
        ref_dict = ref if isinstance(ref, dict) else getattr(ref, "__dict__", {})
        num = str(ref_dict.get("number") or ref_dict.get("nomor") or "").lower()
        year = str(ref_dict.get("year") or ref_dict.get("tahun") or "").lower()
        article = str(ref_dict.get("article") or ref_dict.get("pasal") or "").lower()

        # Cocokkan nomor dan tahun peraturan
        reg_matched = False
        if num and year:
            patterns = [
                rf"{re.escape(num)}/{re.escape(year)}",
                rf"nomor\s+{re.escape(num)}\s+tahun\s+{re.escape(year)}",
                rf"no\.?\s*{re.escape(num)}\s+tahun\s+{re.escape(year)}",
                rf"no\.?\s*{re.escape(num)}/{re.escape(year)}",
            ]
            if any(re.search(p, text_lower) for p in patterns):
                reg_matched = True
        elif num:
            reg_matched = bool(re.search(rf"\b{re.escape(num)}\b", text_lower))

        # Cocokkan pasal jika ada di acuan
        art_matched = True
        if article:
            art_num_match = re.search(r"\d+", article)
            if art_num_match:
                art_num = art_num_match.group()
                art_matched = bool(re.search(rf"pasal\s+{art_num}\b", text_lower))

        if reg_matched and art_matched:
            matched_refs += 1

    score = Decimal(matched_refs) / Decimal(total_refs)
    return round_two_digits(score)


def score_judge_criteria(
    answer_text: str | None,
    criteria: dict[str, Any] | Sequence[Any] | None,
) -> Decimal | None:
    """Menghitung skor judge berbasis kriteria jawaban pada kasus secara proporsional (AC3, AC5).

    Jika tidak ada kriteria yang dapat dinilai, mengembalikan None (TBA).
    Skala mengikuti dokumen rubrik v1: skor proporsional dikalikan skala 5.00 (lulus >= 4.00).
    """
    if not criteria or not answer_text or not answer_text.strip():
        return None

    crit_items: list[str] = []
    if isinstance(criteria, dict):
        if "items" in criteria and isinstance(criteria["items"], list):
            crit_items = [str(x) for x in criteria["items"]]
        elif "kriteria" in criteria and isinstance(criteria["kriteria"], list):
            crit_items = [str(x) for x in criteria["kriteria"]]
        elif "catatan" in criteria and criteria["catatan"]:
            crit_items = [str(criteria["catatan"])]
    elif isinstance(criteria, (list, tuple)):
        crit_items = [str(x) for x in criteria]

    if not crit_items:
        return None

    text_lower = answer_text.lower()
    met_count = sum(
        1 for item in crit_items if item.lower() in text_lower or len(item.split()) <= 2
    )
    total = len(crit_items)
    if total == 0:
        return None

    prop = Decimal(met_count) / Decimal(total)
    score = prop * Decimal("5.00")
    return round_two_digits(score)


@dataclass
class AttemptGradeInput:
    attempt_id: uuid.UUID
    repetition_no: int
    status: AttemptStatus | str
    source: AttemptSource | str = AttemptSource.AUTOMATIC
    answer_text: str | None = None
    failure_kind: str | None = None
    platform_refused: bool = False
    judge_error: str | None = None
    deterministic_score: Decimal | None = None
    judge_score: Decimal | None = None


@dataclass
class CaseGradeInput:
    snapshot_item_id: uuid.UUID
    case_id: uuid.UUID
    case_code: str
    title: str
    split_tag: SplitTag | str | None = None
    category: str | None = None
    question: str | None = None
    legal_refs: list[dict[str, Any]] = field(default_factory=list)
    answer_criteria: dict[str, Any] | None = None
    attempts: list[AttemptGradeInput] = field(default_factory=list)


class GradingStore(Protocol):
    """Port penyimpanan hasil grading run."""

    def get_grading_runs(self, session_id: uuid.UUID) -> list[SessionGradingResponse]: ...
    def save_grading_run(self, run: SessionGradingResponse) -> None: ...
    def get_latest_grading_run(self, session_id: uuid.UUID) -> SessionGradingResponse | None: ...


class MemoryGradingStore:
    """Implementasi thread-safe in-memory store untuk grading run."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._runs: dict[uuid.UUID, list[SessionGradingResponse]] = defaultdict(list)

    def get_grading_runs(self, session_id: uuid.UUID) -> list[SessionGradingResponse]:
        with self._lock:
            return list(self._runs.get(session_id, []))

    def save_grading_run(self, run: SessionGradingResponse) -> None:
        with self._lock:
            history = self._runs[run.session_id]
            # Set older runs to is_latest=False if this new run is is_latest
            if run.is_latest:
                updated = [
                    item.model_copy(update={"is_latest": False}) if item.is_latest else item
                    for item in history
                ]
                self._runs[run.session_id] = updated
            self._runs[run.session_id].append(run)

    def get_latest_grading_run(self, session_id: uuid.UUID) -> SessionGradingResponse | None:
        with self._lock:
            history = self._runs.get(session_id, [])
            for run in reversed(history):
                if run.is_latest:
                    return run
            return history[-1] if history else None


class DbGradingStore:
    """Implementasi GradingStore berbasis database via AuditLog.

    Menjamin persistensi riwayat penilaian dan regrade lintas restart server
    dan lintas proses/worker tanpa memerlukan migrasi tabel baru yang sedang
    dikerjakan di subtask SCRUM-222.
    """

    def __init__(self, db: Session) -> None:
        self.db = db

    def get_grading_runs(self, session_id: uuid.UUID) -> list[SessionGradingResponse]:
        from app.modules.audit.models import AuditEntityType, AuditLog

        logs = self.db.scalars(
            select(AuditLog)
            .where(
                AuditLog.entity_type == AuditEntityType.MEASUREMENT_SESSION,
                AuditLog.entity_id == session_id,
                AuditLog.action.in_(("measurement_session.graded", "measurement_session.regraded")),
            )
            .order_by(AuditLog.occurred_at.asc(), AuditLog.id.asc())
        ).all()

        runs: list[SessionGradingResponse] = []
        for log in logs:
            if log.after and isinstance(log.after, dict):
                try:
                    runs.append(SessionGradingResponse.model_validate(log.after))
                except Exception:
                    continue

        if runs:
            for i, r in enumerate(runs):
                r.is_latest = i == len(runs) - 1
            return runs

        return default_grading_store.get_grading_runs(session_id)

    def save_grading_run(self, run: SessionGradingResponse) -> None:
        from app.modules.audit.models import AuditEntityType, AuditLog

        default_grading_store.save_grading_run(run)

        action = (
            "measurement_session.graded"
            if run.kind == "initial"
            else "measurement_session.regraded"
        )
        log_entry = AuditLog(
            occurred_at=run.triggered_at,
            actor_user_id=run.triggered_by,
            actor_role=Role.ADMIN.value,
            action=action,
            entity_type=AuditEntityType.MEASUREMENT_SESSION,
            entity_id=run.session_id,
            after=run.model_dump(mode="json"),
            reason=f"Rubrik {run.rubric_version} - {run.kind}",
        )
        self.db.add(log_entry)
        self.db.commit()

    def get_latest_grading_run(self, session_id: uuid.UUID) -> SessionGradingResponse | None:
        runs = self.get_grading_runs(session_id)
        if runs:
            for r in reversed(runs):
                if r.is_latest:
                    return r
            return runs[-1]
        return default_grading_store.get_latest_grading_run(session_id)


# Default global store instance (can be swapped or injected in tests)
default_grading_store = MemoryGradingStore()


def grade_session_data(
    *,
    session_id: uuid.UUID,
    cases_input: Sequence[CaseGradeInput],
    triggered_by: uuid.UUID,
    rubric_version: str | None = None,
    existing_runs: Sequence[SessionGradingResponse] = (),
    now: datetime | None = None,
) -> SessionGradingResponse:
    """Melakukan evaluasi gangguan teknis, penilaian tiap percobaan, dan agregasi skor."""
    timestamp = now or datetime.now(UTC)
    rubric_ver = rubric_version or DEFAULT_RUBRIC_VERSION
    kind = "regrade" if len(existing_runs) > 0 else "initial"

    processed_cases: list[CaseGradeResult] = []

    for c in cases_input:
        attempt_results: list[AttemptGradeResult] = []
        attempt_scores: list[ScorePair | None] = []

        for att in c.attempts:
            disrupted = check_technical_disruption(
                status=att.status,
                answer_text=att.answer_text,
                failure_kind=att.failure_kind,
                platform_refused=att.platform_refused,
                judge_error=att.judge_error,
            )

            if disrupted:
                # AC4: Gangguan teknis dilewati, tanpa skor.
                att_score = None
            else:
                # Skor percobaan: deterministik dan judge (dihitung dari rujukan kasus bila belum ada)
                det: Decimal | None
                if att.deterministic_score is not None:
                    det = round_two_digits(att.deterministic_score)
                else:
                    det = score_deterministic_citations(att.answer_text, c.legal_refs)

                jud: Decimal | None
                if att.judge_score is not None:
                    jud = round_two_digits(att.judge_score)
                else:
                    jud = score_judge_criteria(att.answer_text, c.answer_criteria)

                if det is None and jud is None:
                    att_score = None
                else:
                    att_score = ScorePair(deterministic=det, judge=jud)

            att_status = (
                att.status
                if isinstance(att.status, AttemptStatus)
                else AttemptStatus(str(att.status))
            )

            attempt_results.append(
                AttemptGradeResult(
                    attempt_id=att.attempt_id,
                    repetition_no=att.repetition_no,
                    status=att_status,
                    is_technical_disruption=disrupted,
                    score=att_score,
                    answer_text=att.answer_text,
                )
            )
            attempt_scores.append(att_score)

        case_score = aggregate_case_scores(attempt_scores)
        tag = (
            c.split_tag
            if isinstance(c.split_tag, SplitTag) or c.split_tag is None
            else SplitTag(str(c.split_tag))
        )

        processed_cases.append(
            CaseGradeResult(
                snapshot_item_id=c.snapshot_item_id,
                case_id=c.case_id,
                case_code=c.case_code,
                title=c.title,
                split_tag=tag,
                category=c.category,
                question=c.question,
                masked=False,
                scores=case_score,
                attempts=attempt_results,
            )
        )

    three_sc = compute_three_scores(processed_cases)
    cat_sc = compute_category_scores(processed_cases)

    return SessionGradingResponse(
        grading_run_id=uuid.uuid4(),
        session_id=session_id,
        rubric_version=rubric_ver,
        kind=kind,
        status="completed",
        triggered_by=triggered_by,
        triggered_at=timestamp,
        is_latest=True,
        scores=three_sc,
        category_scores=cat_sc,
        cases=processed_cases,
    )


def apply_visibility_mask(
    grading: SessionGradingResponse,
    viewer_role: Role,
    session_status: str | SessionStatus,
) -> SessionGradingResponse:
    """Menegakkan aturan visibilitas dan penyamaran server (AC8, D10).

    - Sesi berjalan: HANYA Admin. Viewer dan Developer AI ditolak (403).
    - Sesi selesai: Admin & Viewer melihat lengkap. Developer AI melihat kasus dev,
      sedangkan kasus test disamarkan per-kasus (isi & skor kasus disembunyikan),
      namun skor total agregat test tetap terlihat di ThreeScores.
    - Kontributor: ditolak (403).
    """
    status_str = (
        session_status.value if isinstance(session_status, SessionStatus) else str(session_status)
    )

    # D10 / AC8: Sesi yang masih berjalan hanya dapat dilihat Admin
    if not has_ended(status_str):
        if viewer_role != Role.ADMIN:
            raise ForbiddenError(
                "Hasil sesi yang masih berjalan hanya dapat dilihat Admin.",
                code="FORBIDDEN",
            )
        return grading

    # Sesi sudah berakhir / selesai
    if viewer_role == Role.CONTRIBUTOR:
        raise ForbiddenError(
            "Peran Kontributor tidak berwenang melihat hasil penilaian.",
            code="FORBIDDEN",
        )

    if viewer_role in (Role.ADMIN, Role.VIEWER):
        return grading

    if viewer_role == Role.DEVELOPER_AI:
        masked_cases: list[CaseGradeResult] = []
        for case in grading.cases:
            tag_str = (
                case.split_tag.value
                if isinstance(case.split_tag, SplitTag)
                else (case.split_tag or "")
            )
            if tag_str == SplitTag.TEST.value:
                masked_attempts = [
                    AttemptGradeResult(
                        attempt_id=att.attempt_id,
                        repetition_no=att.repetition_no,
                        status=att.status,
                        is_technical_disruption=att.is_technical_disruption,
                        score=None,  # Disamarkan per percobaan
                        answer_text=None,  # Disamarkan
                    )
                    for att in case.attempts
                ]
                masked_cases.append(
                    CaseGradeResult(
                        snapshot_item_id=case.snapshot_item_id,
                        case_id=case.case_id,
                        case_code=MASKED_CASE_CODE,
                        title=MASKED_TITLE,
                        split_tag=case.split_tag,
                        category=case.category,
                        question=None,  # Disamarkan
                        masked=True,
                        scores=None,  # Skor per kasus disamarkan!
                        attempts=masked_attempts,
                    )
                )
            else:
                masked_cases.append(case)

        # PBI-11 AC9: Dev AI hanya melihat skor total agregat test (ThreeScores.test).
        # category_scores untuk Dev AI hanya dihitung dari kasus dev agar tidak
        # membocorkan performa per kategori pada kasus test.
        dev_cases = [
            c
            for c in grading.cases
            if (c.split_tag.value if isinstance(c.split_tag, SplitTag) else (c.split_tag or ""))
            != SplitTag.TEST.value
        ]
        dev_category_scores = compute_category_scores(dev_cases)

        # ThreeScores (termasuk skor agregat test) tetap utuh
        return SessionGradingResponse(
            grading_run_id=grading.grading_run_id,
            session_id=grading.session_id,
            rubric_version=grading.rubric_version,
            kind=grading.kind,
            status=grading.status,
            triggered_by=grading.triggered_by,
            triggered_at=grading.triggered_at,
            is_latest=grading.is_latest,
            scores=grading.scores,
            category_scores=dev_category_scores,
            cases=masked_cases,
        )

    raise ForbiddenError("Peran Anda tidak berwenang melihat hasil penilaian.", code="FORBIDDEN")


GRADABLE_SESSION_STATUSES = frozenset(
    {
        SessionStatus.RUNNING.value,
        SessionStatus.FINISHED.value,
        SessionStatus.FINISHED_WITH_FAILURES.value,
    }
)


def ensure_session_gradable(session: MeasurementSession) -> None:
    """Memeriksa kelayakan sesi untuk dinilai (AC6)."""
    if session.archived_at is not None:
        raise ConflictError("Sesi ini sudah diarsipkan.", code="SESSION_ARCHIVED")

    status_str = (
        session.status.value if isinstance(session.status, SessionStatus) else str(session.status)
    )
    if status_str not in GRADABLE_SESSION_STATUSES:
        raise ConflictError(
            f"Sesi dengan status '{status_str}' belum bisa dinilai.",
            code="SESSION_NOT_GRADABLE",
        )


def _load_case_grade_inputs(db: Session, session_id: uuid.UUID) -> list[CaseGradeInput]:
    """Membaca snapshot item dan attempts dari database untuk grading."""
    items = db.scalars(
        select(CaseSnapshotItem)
        .join(CaseSnapshot, CaseSnapshot.id == CaseSnapshotItem.snapshot_id)
        .where(CaseSnapshot.session_id == session_id)
    ).all()

    # Query attempts
    attempts_db = db.scalars(
        select(Attempt)
        .where(Attempt.session_id == session_id)
        .order_by(Attempt.snapshot_item_id, Attempt.repetition_no)
    ).all()

    # Query failure_kind dan error judge dari call_logs
    call_failure_kinds: dict[uuid.UUID, str] = {}
    call_judge_errors: dict[uuid.UUID, str] = {}
    if attempts_db:
        attempt_ids = [a.id for a in attempts_db]
        logs = db.scalars(
            select(CallLog)
            .where(CallLog.attempt_id.in_(attempt_ids))
            .order_by(CallLog.try_no.desc())
        ).all()
        for log in logs:
            if log.attempt_id not in call_failure_kinds and log.failure_kind:
                call_failure_kinds[log.attempt_id] = log.failure_kind
            if (
                log.attempt_id not in call_judge_errors
                and log.failure_kind
                and (
                    "judge" in log.failure_kind.lower()
                    or log.failure_kind in ("judge_error", "judge_failed", "judge_timeout")
                )
            ):
                call_judge_errors[log.attempt_id] = log.failure_kind

    attempts_by_item: dict[uuid.UUID, list[AttemptGradeInput]] = defaultdict(list)
    for a in attempts_db:
        f_kind = call_failure_kinds.get(a.id)
        j_err = call_judge_errors.get(a.id)
        # Deteksi penolakan platform manual (PBI-22)
        platform_refused = bool(
            a.answer_text and "[Platform menolak menjawab]" in a.answer_text
        ) or (f_kind == "platform_refused")

        attempts_by_item[a.snapshot_item_id].append(
            AttemptGradeInput(
                attempt_id=a.id,
                repetition_no=a.repetition_no,
                status=a.status,
                source=a.source,
                answer_text=a.answer_text,
                failure_kind=f_kind,
                platform_refused=platform_refused,
                judge_error=j_err,
            )
        )

    cases: list[CaseGradeInput] = []
    for item in items:
        body = item.body or {}
        sections = body.get("sections") or {}
        split_tag = sections.get("split_tag") or "dev"
        case_code = sections.get("case_code") or ""
        title = sections.get("identity.title") or ""
        question = sections.get("identity.question")
        category = sections.get("identity.category") or sections.get("category")
        legal_refs = sections.get("legal_refs") or body.get("legal_refs") or []
        answer_criteria = sections.get("answer_criteria") or body.get("answer_criteria")

        cases.append(
            CaseGradeInput(
                snapshot_item_id=item.id,
                case_id=item.case_id,
                case_code=case_code,
                title=title,
                split_tag=split_tag,
                category=category,
                question=question,
                legal_refs=legal_refs,
                answer_criteria=answer_criteria,
                attempts=attempts_by_item.get(item.id, []),
            )
        )

    return cases


def grade_session(
    db: Session,
    session_id: uuid.UUID,
    payload: GradeSessionRequest | None = None,
    *,
    actor_id: uuid.UUID,
    actor_role: Role,
    store: GradingStore | None = None,
    now: datetime | None = None,
) -> SessionGradingResponse:
    """Memicu penilaian manual untuk sesi pengukuran (AC6, AC7)."""
    if actor_role != Role.ADMIN:
        raise ForbiddenError("Hanya Admin yang dapat memicu penilaian.", code="FORBIDDEN")

    session = db.get(MeasurementSession, session_id)
    if session is None:
        raise NotFoundError("Pengukuran tidak ditemukan.", code="MEASUREMENT_NOT_FOUND")

    ensure_session_gradable(session)

    grading_store = store or DbGradingStore(db)
    cases = _load_case_grade_inputs(db, session_id)

    rubric_version = payload.rubric_version if payload else None
    existing_runs = grading_store.get_grading_runs(session_id)

    response = grade_session_data(
        session_id=session_id,
        cases_input=cases,
        triggered_by=actor_id,
        rubric_version=rubric_version,
        existing_runs=existing_runs,
        now=now,
    )
    grading_store.save_grading_run(response)
    db.commit()
    return response


def get_session_results(
    db: Session,
    session_id: uuid.UUID,
    *,
    viewer_role: Role,
    store: GradingStore | None = None,
) -> SessionGradingResponse:
    """Mengambil hasil penilaian terbaru dengan filter visibilitas per peran (AC8)."""
    session = db.get(MeasurementSession, session_id)
    if session is None:
        raise NotFoundError("Pengukuran tidak ditemukan.", code="MEASUREMENT_NOT_FOUND")

    grading_store = store or DbGradingStore(db)
    latest_run = grading_store.get_latest_grading_run(session_id)
    if latest_run is None:
        raise NotFoundError(
            "Hasil penilaian belum tersedia. Silakan picu penilaian terlebih dahulu.",
            code="GRADING_NOT_FOUND",
        )

    return apply_visibility_mask(
        latest_run,
        viewer_role=viewer_role,
        session_status=session.status,
    )
