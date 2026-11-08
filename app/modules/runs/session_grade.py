"""Penilaian, pemicu manual, penanganan gangguan teknis, dan visibilitas hasil.

PBI-14 SCRUM-226 (AC4, AC6, AC7, AC8, AC9; PBI-11 AC9 dipindah).

Fitur utama:
1. Pemicu manual POST /measurements/{id}/grade (Admin only).
2. Penanganan gangguan teknis: jawaban kosong, AI menolak, atau error judge
   dihitung sebagai gangguan teknis (is_technical_disruption=True), tanpa skor,
   dilewati dari perhitungan rata-rata, dan dapat dicoba ulang manual via PBI-12.
3. Penilaian seragam: alur yang sama untuk percobaan otomatis maupun manual (AC9).
4. Riwayat nilai ulang: nilai ulang menandai hasil baru sebagai is_latest=True,
   hasil lama tetap tersimpan (AC7) di tabel D6 (grading_runs, grading_results).
5. Visibilitas hasil & Server-enforced Masking (AC8, D10):
   - Sesi berjalan: hanya Admin. Viewer & Developer AI ditolak (403).
   - Sesi selesai: Admin & Viewer melihat hasil lengkap; Developer AI melihat kasus
     dev lengkap, sedangkan kasus test disamarkan per-kasus (pertanyaan, jawaban, skor
     kasus, dan kategori null; judul terproteksi), namun skor agregat test tetap terlihat di ThreeScores.
"""

from __future__ import annotations

import logging
import re
import threading
import uuid
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Protocol

from sqlalchemy import null, select
from sqlalchemy.orm import Session

import app.modules.legal_refs.service as legal_refs_service
from app.modules.cases.models import SplitTag
from app.modules.cases.service import MASKED_CASE_CODE, MASKED_TITLE
from app.modules.legal_refs.seed import load_regulation_types
from app.modules.legal_refs.service import TypeName
from app.modules.providers import service as providers_service
from app.modules.runs.citation_score import CitationScorer
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
    GradedSessionListItem,
    GradeSessionRequest,
    GradingRunSummary,
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
MEASUREMENT_NOT_FOUND_MESSAGE = "Pengukuran tidak ditemukan."
ACTION_MEASUREMENT_SESSION_GRADED = "measurement_session.graded"
ACTION_MEASUREMENT_SESSION_REGRADED = "measurement_session.regraded"
REASON_CRITERION_UNMET = "criterion_unmet"
_MAX_REFUSAL_LENGTH = 250

_REFUSAL_PHRASES = (
    "maaf, saya tidak dapat menjawab",
    "maaf, saya tidak bisa menjawab",
    "maaf, sebagai model ai",
    "maaf, sebagai asisten ai",
    "maaf, saya tidak berwenang",
    "as an ai, i cannot",
    "i cannot assist with this request",
    "saya tidak dapat memberikan nasihat hukum",
    "saya tidak memiliki wewenang untuk memberikan pertimbangan hukum",
    "i am unable to provide legal advice",
)

_LEGAL_SUBSTANCE_KEYWORDS = (
    "pasal",
    "undang-undang",
    "peraturan",
    "nomor",
    "kuhp",
    "kuhper",
    "putusan",
    "uu ",
)

_DISCLAIMER_TRANSITIONS = (
    "namun",
    "tetapi",
    "akan tetapi",
    "berikut",
    "namun demikian",
    "meskipun demikian",
    "adapun",
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


logger = logging.getLogger(__name__)

RUN_COMPLETED = "completed"
RUN_FAILED = "failed"
JUDGE_NOT_CONFIGURED = "judge_not_configured"
JUDGE_UNAVAILABLE = "judge_unavailable"


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
    """Deteksi model AI menolak menjawab (AC4).

    Penolakan teknis terjadi jika:
    1. failure_kind == 'ai_refused' (dari provider AI)
    2. Teks mengandung penanda eksplisit '[Platform menolak menjawab]'
    3. Teks memuat frasa penolakan, tidak memuat substansi/kutipan hukum,
       dan berukuran sangat pendek (<= 25 kata).
    Jawaban yang diawali disclaimer hukum ("Sebagai model AI, saya tidak dapat
    memberikan nasihat hukum resmi...") namun tetap memberikan analisis/jawaban
    hukum tidak dihitung sebagai gangguan teknis penolakan.
    """
    if failure_kind == "ai_refused":
        return True
    if not text:
        return False
    if "[Platform menolak menjawab]" in text:
        return True

    text_lower = text.lower().strip()
    if any(phrase in text_lower for phrase in _REFUSAL_PHRASES):
        if any(tr in text_lower for tr in _DISCLAIMER_TRANSITIONS):
            return False
        if any(kw in text_lower for kw in _LEGAL_SUBSTANCE_KEYWORDS):
            return False
        return len(text_lower.split()) <= 25
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
    valid_items = [item for item in attempt_scores if item is not None]
    if not valid_items:
        return None

    det_mean = mean_scores(item.deterministic for item in valid_items)
    qual_mean = mean_scores(item.quality for item in valid_items)
    fmt_mean = mean_scores(item.format for item in valid_items)

    if det_mean is None and qual_mean is None and fmt_mean is None:
        return None
    return ScorePair(
        deterministic=det_mean,
        quality=qual_mean,
        format=fmt_mean,
    )


def _case_tag(split_tag: SplitTag | str | None) -> str:
    if isinstance(split_tag, SplitTag):
        return split_tag.value
    return split_tag or ""


def _score_pair_from_cases(cases: Sequence[CaseGradeResult]) -> ScorePair:
    valid_scores = [c.scores for c in cases if c.scores is not None]
    return ScorePair(
        deterministic=mean_scores(s.deterministic for s in valid_scores),
        quality=mean_scores(s.quality for s in valid_scores),
        format=mean_scores(s.format for s in valid_scores),
    )


def compute_three_scores(cases: Sequence[CaseGradeResult]) -> ThreeScores:
    """Tiga skor terpisah: kasus dev, kasus test, dan gabungan (PBI-11 AC9)."""
    dev_cases = [c for c in cases if _case_tag(c.split_tag) == SplitTag.DEV.value]
    test_cases = [c for c in cases if _case_tag(c.split_tag) == SplitTag.TEST.value]
    return ThreeScores(
        dev=_score_pair_from_cases(dev_cases),
        test=_score_pair_from_cases(test_cases),
        combined=_score_pair_from_cases(cases),
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
        valid_scores = [c.scores for c in cat_cases if c.scores is not None]
        results.append(
            CategoryScore(
                category=cat_name,
                case_count=len(cat_cases),
                scores=ScorePair(
                    deterministic=mean_scores(s.deterministic for s in valid_scores),
                    quality=mean_scores(s.quality for s in valid_scores),
                    format=mean_scores(s.format for s in valid_scores),
                ),
            )
        )
    return results


def _get_jenis_catalog(db: Session | None = None) -> dict[str, TypeName]:
    """Mengambil katalog jenis peraturan dari database atau fallback ke seed data."""
    if db is not None:
        try:
            return legal_refs_service.load_catalog(db).names
        except Exception:
            pass
    try:
        seeds = load_regulation_types()
        return {s.code: TypeName(s.label, s.singkatan) for s in seeds}
    except Exception:
        return {}


def _normalize_ref_links(links: Iterable[Any]) -> list[dict[str, Any]]:
    norm_links = []
    for link in links:
        ld = dict(link) if isinstance(link, dict) else getattr(link, "__dict__", {})
        reg_type = str(ld.get("regulation_type") or "").strip().lower()
        norm_links.append({**ld, "regulation_type": reg_type})
    return norm_links


def _normalize_flat_ref(ref_dict: dict[str, Any]) -> dict[str, Any]:
    reg_type = str(ref_dict.get("regulation_type") or "uu").strip().lower()
    num = str(ref_dict.get("number") or ref_dict.get("nomor") or "").strip()
    year_val = ref_dict.get("year") or ref_dict.get("tahun")
    year = int(year_val) if year_val else None
    pasal = ref_dict.get("article") or ref_dict.get("pasal")
    return {
        "kind": "structured",
        "free_text": None,
        "links": [
            {
                "regulation_type": reg_type,
                "number": num,
                "year": year,
                "pasal": str(pasal) if pasal is not None else None,
            }
        ],
    }


def normalize_legal_refs(refs: Sequence[Any]) -> list[dict[str, Any]]:
    """Normalisasi format rujukan acuan ke bentuk LegalRefStored untuk CitationScorer."""
    normalized: list[dict[str, Any]] = []
    for ref in refs:
        ref_dict = ref if isinstance(ref, dict) else getattr(ref, "__dict__", {})
        if "links" in ref_dict and isinstance(ref_dict["links"], (list, tuple)):
            normalized.append({**ref_dict, "links": _normalize_ref_links(ref_dict["links"])})
        elif "number" in ref_dict or "nomor" in ref_dict:
            normalized.append(_normalize_flat_ref(ref_dict))
        else:
            normalized.append(ref_dict)
    return normalized


def score_deterministic_citations(
    answer_text: str | None,
    legal_refs: Sequence[dict[str, Any] | Any] | None,
    *,
    scorer: CitationScorer | None = None,
    jenis_catalog: Mapping[str, TypeName] | None = None,
) -> Decimal | None:
    """Menghitung skor deterministik berdasarkan pencocokan peraturan dan pasal (AC1, AC2).

    Menggunakan CitationScorer dari SCRUM-223 (#107).
    Jika kasus tidak memiliki rujukan acuan yang dapat dinilai, mengembalikan None.
    """
    if not legal_refs or not answer_text or not answer_text.strip():
        return None

    active_scorer = scorer
    if active_scorer is None:
        catalog = jenis_catalog or _get_jenis_catalog()
        active_scorer = CitationScorer(catalog)

    norm_refs = normalize_legal_refs(legal_refs)
    score_obj = active_scorer.score(answer_text, norm_refs)
    if score_obj.score is None:
        return None
    return round_two_digits(score_obj.score)


def _extract_dict_fallback_items(criteria: dict[str, Any]) -> list[str]:
    raw_list = criteria.get("items") or criteria.get("kriteria")
    if isinstance(raw_list, list):
        return [str(x) for x in raw_list]
    catatan = criteria.get("catatan")
    if catatan:
        return [str(catatan)]
    return []


def _extract_criteria_items(
    criteria: dict[str, Any] | Sequence[Any] | None,
) -> tuple[list[str], list[str], str | None]:
    """Mengekstrak must_contain, must_not_contain, dan expected_conclusion."""
    if not criteria:
        return [], [], None

    if isinstance(criteria, dict):
        must_contain = [str(x) for x in criteria.get("must_contain", [])]
        must_not_contain = [str(x) for x in criteria.get("must_not_contain", [])]
        concl = criteria.get("expected_conclusion")
        expected_conclusion = str(concl) if concl else None
        if must_contain or must_not_contain or expected_conclusion:
            return must_contain, must_not_contain, expected_conclusion
        return _extract_dict_fallback_items(criteria), [], None

    if isinstance(criteria, (list, tuple)):
        return [str(x) for x in criteria], [], None

    return [], [], None


def _evaluate_contain_criteria(
    items: list[str], text_lower: str
) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    c_list: list[dict[str, Any]] = []
    exps: list[str] = []
    reasons: list[str] = []
    for idx, item in enumerate(items):
        is_met = item.lower() in text_lower
        exp = None if is_met else f"Tidak memuat: {item}"
        reason = None if is_met else REASON_CRITERION_UNMET
        c_list.append(
            {
                "key": f"must_contain_{idx + 1}",
                "text": item,
                "is_met": is_met,
                "explanation": exp,
                "reason_code": reason,
            }
        )
        if not is_met:
            exps.append(exp)
            reasons.append(reason)
    return c_list, exps, reasons


def _evaluate_not_contain_criteria(
    items: list[str], text_lower: str
) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    c_list: list[dict[str, Any]] = []
    exps: list[str] = []
    reasons: list[str] = []
    for idx, item in enumerate(items):
        is_met = item.lower() not in text_lower
        exp = None if is_met else f"Memuat teks yang dilarang: {item}"
        reason = None if is_met else REASON_CRITERION_UNMET
        c_list.append(
            {
                "key": f"must_not_contain_{idx + 1}",
                "text": f"Tidak memuat: {item}",
                "is_met": is_met,
                "explanation": exp,
                "reason_code": reason,
            }
        )
        if not is_met:
            exps.append(exp)
            reasons.append(reason)
    return c_list, exps, reasons


def _evaluate_conclusion_criterion(
    conclusion: str, text_lower: str
) -> tuple[dict[str, Any], str | None, str | None]:
    is_met = conclusion.lower() in text_lower
    exp = None if is_met else f"Kesimpulan tidak memuat: {conclusion}"
    reason = None if is_met else REASON_CRITERION_UNMET
    crit = {
        "key": "expected_conclusion",
        "text": f"Kesimpulan: {conclusion}",
        "is_met": is_met,
        "explanation": exp,
        "reason_code": reason,
    }
    return crit, exp, reason


def evaluate_criteria_details(
    answer_text: str | None,
    criteria: dict[str, Any] | Sequence[Any] | None,
) -> tuple[Decimal, str | None, list[str], list[dict[str, Any]]] | None:
    """Evaluasi kriteria jawaban secara proporsional skala 0.00 - 1.00 (AC3, AC5).

    Mendukung AnswerCriteria dari cases/schemas.py (must_contain, must_not_contain, expected_conclusion).
    Mengembalikan tuple (skor, penjelasan_kriteria_tidak_terpenuhi, reasons, criteria_details).
    """
    if not criteria or not answer_text or not answer_text.strip():
        return None

    must_contain, must_not_contain, conclusion = _extract_criteria_items(criteria)
    total_count = len(must_contain) + len(must_not_contain) + (1 if conclusion else 0)
    if total_count == 0:
        return None

    text_lower = answer_text.lower()
    c_list1, exps1, r1 = _evaluate_contain_criteria(must_contain, text_lower)
    c_list2, exps2, r2 = _evaluate_not_contain_criteria(must_not_contain, text_lower)
    criteria_list = c_list1 + c_list2
    unmet_exps = exps1 + exps2
    reasons = r1 + r2

    if conclusion:
        c_concl, exp_concl, r_concl = _evaluate_conclusion_criterion(conclusion, text_lower)
        criteria_list.append(c_concl)
        if exp_concl:
            unmet_exps.append(exp_concl)
            reasons.append(r_concl)

    met_count = sum(1 for c in criteria_list if c["is_met"])
    score = round_two_digits(Decimal(met_count) / Decimal(total_count))
    explanation = " ".join(unmet_exps) if unmet_exps else None
    return score, explanation, reasons, criteria_list


def score_judge_criteria(
    answer_text: str | None,
    criteria: dict[str, Any] | Sequence[Any] | None,
) -> Decimal | None:
    """Menghitung skor judge berbasis kriteria jawaban pada kasus secara proporsional (AC3, AC5).

    Mendukung AnswerCriteria dari cases/schemas.py (must_contain, must_not_contain, expected_conclusion).
    Jika tidak ada kriteria yang dapat dinilai, mengembalikan None.
    Skala proporsional 0.00 - 1.00 sesuai constraints DB llm_judge_results.criteria_score.
    """
    res = evaluate_criteria_details(answer_text, criteria)
    return res[0] if res is not None else None


_FORMAT_SECTIONS: tuple[tuple[str, str], ...] = (
    ("ringkasan", r"\b(ringkasan|ikhtisar)\b"),
    ("pembahasan", r"\b(pembahasan|analisis)\b"),
    ("penutup", r"\b(penutup|kesimpulan)\b"),
    ("rujukan", r"\b(rujukan|dasar\s+hukum|referensi)\b"),
)


def score_format_structure(answer_text: str | None) -> Decimal | None:
    """Menghitung skor kepatuhan format 4 bagian sesuai Format Jawaban Hukum (skala 0.00-1.00)."""
    if is_empty_answer(answer_text):
        return None
    text_lower = (answer_text or "").lower()
    matched = sum(1 for _, pat in _FORMAT_SECTIONS if re.search(pat, text_lower))
    if matched > 0:
        return round_two_digits(Decimal(matched) / Decimal(len(_FORMAT_SECTIONS)))
    paragraphs = [p.strip() for p in (answer_text or "").split("\n\n") if p.strip()]
    if len(paragraphs) >= 3:
        return Decimal("0.50")
    return Decimal("0.25")


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
    quality_score: Decimal | None = None
    format_score: Decimal | None = None
    explanation: str | None = None
    reasons: list[str] = field(default_factory=list)
    criteria: list[dict[str, Any]] = field(default_factory=list)


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
    """Implementasi thread-safe in-memory store untuk grading run (khusus isolasi pengujian)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._runs: dict[uuid.UUID, list[SessionGradingResponse]] = defaultdict(list)

    def get_grading_runs(self, session_id: uuid.UUID) -> list[SessionGradingResponse]:
        with self._lock:
            return list(self._runs.get(session_id, []))

    def save_grading_run(self, run: SessionGradingResponse) -> None:
        with self._lock:
            history = self._runs[run.session_id]
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


def pilih_run_terbaru(
    runs: Sequence[SessionGradingResponse],
) -> SessionGradingResponse | None:
    """Run yang ditampilkan sebagai hasil sesi."""
    return runs[-1] if runs else None


def _extract_d6_criteria(crits: list[Any]) -> tuple[list[dict[str, Any]], list[str]]:
    crit_dicts: list[dict[str, Any]] = []
    reasons: list[str] = []
    for c in crits:
        crit_dicts.append(
            {
                "key": c.criterion_key,
                "text": c.criterion_text,
                "is_met": c.is_met,
                "explanation": c.explanation,
                "reason_code": c.reason_code,
            }
        )
        if not c.is_met and c.reason_code:
            reasons.append(c.reason_code)
    return crit_dicts, reasons


def _resolve_d6_judge_score(judge_row: Any | None) -> Decimal | None:
    from app.modules.grading.models import JudgeStatus

    if judge_row is not None and judge_row.status == JudgeStatus.SUCCEEDED:
        return judge_row.criteria_score
    return None


def _resolve_d6_attempt_score(
    g: Any | None,
    q_j: Any | None,
    f_j: Any | None,
) -> tuple[bool, ScorePair | None]:
    if g is None or g.is_technical_disruption:
        return True, None
    det = g.det_score
    qual = _resolve_d6_judge_score(q_j)
    fmt = _resolve_d6_judge_score(f_j)
    if det is None and qual is None and fmt is None:
        return False, None
    return False, ScorePair(deterministic=det, quality=qual, format=fmt)


def _extract_d6_explanation(q_j: Any | None) -> str | None:
    if q_j is None:
        return None
    return q_j.explanation or q_j.error


class DbGradingStore:
    """Implementasi GradingStore berbasis database tabel D6 (grading_runs, grading_results) dan AuditLog.

    Menjamin persistensi riwayat penilaian dan regrade lintas restart server
    dan lintas proses/worker tanpa ketergantungan pada memory global.
    """

    def __init__(self, db: Session) -> None:
        self.db = db

    def _build_attempt_grade_from_d6(
        self,
        att_in: AttemptGradeInput,
        g: Any | None,
        j_by_res: dict[tuple[uuid.UUID, Any], Any],
        crits_by_judge_id: dict[uuid.UUID, list[Any]],
    ) -> AttemptGradeResult:
        from app.modules.grading.models import JudgeKind

        q_j = j_by_res.get((g.id, JudgeKind.QUALITY)) if g else None
        f_j = j_by_res.get((g.id, JudgeKind.FORMAT)) if g else None
        crits = crits_by_judge_id.get(q_j.id, []) if q_j else []

        is_disrupted, sc = _resolve_d6_attempt_score(g, q_j, f_j)
        crit_dicts, reasons = _extract_d6_criteria(crits)
        exp = _extract_d6_explanation(q_j)

        return AttemptGradeResult(
            attempt_id=att_in.attempt_id,
            repetition_no=att_in.repetition_no,
            status=att_in.status,
            source=att_in.source,
            is_technical_disruption=is_disrupted,
            score=sc,
            answer_text=att_in.answer_text,
            explanation=exp,
            reasons=reasons,
            criteria=crit_dicts,
        )

    def _build_case_grade_from_d6(
        self,
        case_in: CaseGradeInput,
        g_by_att: dict[uuid.UUID, Any],
        j_by_res: dict[tuple[uuid.UUID, Any], Any],
        crits_by_judge_id: dict[uuid.UUID, list[Any]],
    ) -> CaseGradeResult:
        att_results = [
            self._build_attempt_grade_from_d6(
                att_in, g_by_att.get(att_in.attempt_id), j_by_res, crits_by_judge_id
            )
            for att_in in case_in.attempts
        ]
        case_scores = aggregate_case_scores([a.score for a in att_results])
        tag = (
            case_in.split_tag
            if isinstance(case_in.split_tag, SplitTag) or case_in.split_tag is None
            else SplitTag(str(case_in.split_tag))
        )
        return CaseGradeResult(
            snapshot_item_id=case_in.snapshot_item_id,
            case_id=case_in.case_id,
            case_code=case_in.case_code,
            title=case_in.title,
            split_tag=tag,
            category=case_in.category,
            question=case_in.question,
            masked=False,
            scores=case_scores,
            attempts=att_results,
        )

    def _build_run_from_d6(
        self,
        run_db: Any,
        session: MeasurementSession | None,
    ) -> SessionGradingResponse:
        from app.modules.grading.models import (
            GradingResult,
            GradingRunStatus,
            JudgeKind,
            JudgeStatus,
            LlmCriterionResult,
            LlmJudgeResult,
            RubricVersion,
        )

        rubric_ver = self.db.get(RubricVersion, run_db.rubric_version_id)
        rubric_version_str = f"v{rubric_ver.version_no}" if rubric_ver else DEFAULT_RUBRIC_VERSION
        session_id = run_db.session_id
        session_label = session.label if session else None
        p_name = ""
        if session:
            p_names = providers_service.product_names(self.db, {session.product_id})
            p_name = p_names.get(session.product_id, "")

        g_results = self.db.scalars(
            select(GradingResult).where(GradingResult.grading_run_id == run_db.id)
        ).all()
        g_by_att = {g.attempt_id: g for g in g_results}
        g_ids = [g.id for g in g_results]

        judge_rows = (
            self.db.scalars(
                select(LlmJudgeResult).where(LlmJudgeResult.grading_result_id.in_(g_ids))
            ).all()
            if g_ids
            else []
        )
        j_by_res: dict[tuple[uuid.UUID, Any], Any] = {
            (j.grading_result_id, j.kind): j for j in judge_rows
        }

        q_judge_ids = [
            j.id
            for j in judge_rows
            if j.kind == JudgeKind.QUALITY and j.status == JudgeStatus.SUCCEEDED
        ]
        crit_rows = (
            self.db.scalars(
                select(LlmCriterionResult).where(
                    LlmCriterionResult.llm_judge_result_id.in_(q_judge_ids)
                )
            ).all()
            if q_judge_ids
            else []
        )
        crits_by_judge_id: dict[uuid.UUID, list[Any]] = defaultdict(list)
        for cr in crit_rows:
            crits_by_judge_id[cr.llm_judge_result_id].append(cr)

        cases_input = _load_case_grade_inputs(self.db, session_id)
        cases_result = [
            self._build_case_grade_from_d6(c, g_by_att, j_by_res, crits_by_judge_id)
            for c in cases_input
        ]

        kind_str = run_db.kind.value if hasattr(run_db.kind, "value") else str(run_db.kind)
        return SessionGradingResponse(
            grading_run_id=run_db.id,
            session_id=session_id,
            session_label=session_label,
            product_name=p_name,
            rubric_version=rubric_version_str,
            kind=kind_str,
            status=RUN_FAILED if run_db.status == GradingRunStatus.FAILED else RUN_COMPLETED,
            triggered_by=run_db.triggered_by,
            triggered_at=run_db.triggered_at,
            is_latest=run_db.is_latest,
            scores=compute_three_scores(cases_result),
            category_scores=compute_category_scores(cases_result),
            cases=cases_result,
        )

    def _get_grading_runs_from_audit(self, session_id: uuid.UUID) -> list[SessionGradingResponse]:
        from app.modules.audit.models import AuditEntityType, AuditLog

        logs = self.db.scalars(
            select(AuditLog)
            .where(
                AuditLog.entity_type == AuditEntityType.MEASUREMENT_SESSION,
                AuditLog.entity_id == session_id,
                AuditLog.action.in_(
                    (ACTION_MEASUREMENT_SESSION_GRADED, ACTION_MEASUREMENT_SESSION_REGRADED)
                ),
            )
            .order_by(AuditLog.occurred_at.asc(), AuditLog.id.asc())
        ).all()

        runs: list[SessionGradingResponse] = []
        for log in logs:
            if log.after and isinstance(log.after, dict) and "cases" in log.after:
                try:
                    runs.append(SessionGradingResponse.model_validate(log.after))
                except Exception:
                    continue

        if runs:
            for i, r in enumerate(runs):
                r.is_latest = i == len(runs) - 1
            return runs
        return []

    def get_grading_runs(self, session_id: uuid.UUID) -> list[SessionGradingResponse]:
        from app.modules.grading.models import GradingRun

        runs_db = self.db.scalars(
            select(GradingRun)
            .where(GradingRun.session_id == session_id)
            .order_by(GradingRun.triggered_at.asc(), GradingRun.id.asc())
        ).all()

        if runs_db:
            session = self.db.get(MeasurementSession, session_id)
            return [self._build_run_from_d6(r, session) for r in runs_db]

        return self._get_grading_runs_from_audit(session_id)

    def _resolve_db_rubric_version_id(self, rubric_ver_str: str | None) -> uuid.UUID:
        from app.modules.grading.models import RubricVersion

        ver_str = (rubric_ver_str or "v1").lower().lstrip("v")
        ver_no = int(ver_str) if ver_str.isdigit() else 1
        rubric_ver = self.db.scalars(
            select(RubricVersion).where(RubricVersion.version_no == ver_no)
        ).first()
        if rubric_ver is None:
            rubric_ver = self.db.scalars(
                select(RubricVersion).where(RubricVersion.is_active)
            ).first()
        if rubric_ver is None:
            raise ConflictError(
                "Tidak ada versi rubrik yang aktif.",
                code="RUBRIC_NOT_ACTIVE",
            )
        return rubric_ver.id

    def _deactivate_previous_latest_runs(self, session_id: uuid.UUID) -> None:
        from app.modules.grading.models import GradingRun

        old_latest_runs = self.db.scalars(
            select(GradingRun).where(
                GradingRun.session_id == session_id,
                GradingRun.is_latest,
            )
        ).all()
        for old_run in old_latest_runs:
            old_run.is_latest = False
        self.db.flush()

    def _create_grading_result_row(
        self,
        run_id: uuid.UUID,
        att: AttemptGradeResult,
        norm_refs: list[dict[str, Any]],
        scorer: CitationScorer,
    ) -> Any:
        from app.modules.grading.models import GradingResult

        if att.is_technical_disruption:
            return GradingResult(
                id=uuid.uuid4(),
                grading_run_id=run_id,
                attempt_id=att.attempt_id,
                is_technical_disruption=True,
                det_score=None,
                det_regulation_matches=null(),
                det_article_matches=null(),
                det_ungradable_refs=null(),
                det_all_chained_cited=None,
                det_reference_total=None,
                det_reference_correct=None,
                det_extracted_total=None,
                det_extracted_correct=None,
                det_formula=None,
            )
        c_score = scorer.score(att.answer_text or "", norm_refs)
        formula_val = (
            c_score.formula.value if hasattr(c_score.formula, "value") else str(c_score.formula)
        )
        return GradingResult(
            id=uuid.uuid4(),
            grading_run_id=run_id,
            attempt_id=att.attempt_id,
            is_technical_disruption=False,
            det_score=c_score.score,
            det_regulation_matches=[m.__dict__ for m in c_score.regulation_matches],
            det_article_matches=[m.__dict__ for m in c_score.article_matches],
            det_ungradable_refs=list(c_score.ungradable_refs),
            det_all_chained_cited=c_score.all_chained_cited,
            det_reference_total=c_score.reference_total,
            det_reference_correct=c_score.reference_correct,
            det_extracted_total=c_score.extracted_total,
            det_extracted_correct=c_score.extracted_correct,
            det_formula=formula_val,
        )

    def _save_case_results(
        self,
        run_id: uuid.UUID,
        cases: Sequence[CaseGradeResult],
        case_inputs: Sequence[CaseGradeInput] | None,
        scorer: CitationScorer,
    ) -> None:
        case_input_by_item_id = {c.snapshot_item_id: c for c in (case_inputs or [])}
        for case in cases:
            case_in = case_input_by_item_id.get(case.snapshot_item_id)
            norm_refs = normalize_legal_refs(case_in.legal_refs) if case_in else []
            for att in case.attempts:
                g_result = self._create_grading_result_row(run_id, att, norm_refs, scorer)
                self.db.add(g_result)
                self.db.flush()
                self._save_llm_judge_records(g_result.id, att, case.case_code)

    def save_grading_run(
        self,
        run: SessionGradingResponse,
        cases_input: Sequence[CaseGradeInput] | None = None,
    ) -> None:
        from app.modules.audit.models import AuditEntityType, AuditLog
        from app.modules.grading.models import (
            GradingRun,
            GradingRunKind,
            GradingRunStatus,
        )

        rubric_version_id = self._resolve_db_rubric_version_id(run.rubric_version)

        session = self.db.get(MeasurementSession, run.session_id)
        judge_provider_id = session.judge_provider_id if session else None

        has_earlier = bool(
            self.db.scalar(
                select(
                    select(GradingRun.id).where(GradingRun.session_id == run.session_id).exists()
                )
            )
        )
        run_kind = GradingRunKind.REGRADE if has_earlier else GradingRunKind.INITIAL

        grading_run_row = GradingRun(
            id=run.grading_run_id,
            session_id=run.session_id,
            rubric_version_id=rubric_version_id,
            judge_provider_id=judge_provider_id,
            kind=run_kind,
            status=GradingRunStatus.QUEUED,
            is_latest=False,
            triggered_by=run.triggered_by,
            triggered_at=run.triggered_at,
            finished_at=None,
        )
        self.db.add(grading_run_row)
        self.db.flush()

        grading_run_row.status = GradingRunStatus.RUNNING
        self.db.flush()

        catalog = _get_jenis_catalog(self.db)
        scorer = CitationScorer(catalog)
        self._save_case_results(run.grading_run_id, run.cases, cases_input, scorer)

        if run.is_latest:
            self._deactivate_previous_latest_runs(run.session_id)

        grading_run_row.status = (
            GradingRunStatus.FINISHED if run.status == RUN_COMPLETED else GradingRunStatus.FAILED
        )
        grading_run_row.finished_at = run.triggered_at
        if run.is_latest:
            grading_run_row.is_latest = True
        self.db.flush()

        action = (
            ACTION_MEASUREMENT_SESSION_GRADED
            if run_kind == GradingRunKind.INITIAL
            else ACTION_MEASUREMENT_SESSION_REGRADED
        )
        log_entry = AuditLog(
            occurred_at=run.triggered_at,
            actor_user_id=run.triggered_by,
            actor_role=Role.ADMIN.value,
            action=action,
            entity_type=AuditEntityType.MEASUREMENT_SESSION,
            entity_id=run.session_id,
            after={
                "grading_run_id": str(run.grading_run_id),
                "rubric_version": run.rubric_version,
                "kind": run.kind,
            },
            reason=f"Rubrik {run.rubric_version} - {run.kind}",
        )
        self.db.add(log_entry)
        self.db.flush()

    def get_latest_grading_run(self, session_id: uuid.UUID) -> SessionGradingResponse | None:
        runs = self.get_grading_runs(session_id)
        if runs:
            return runs[-1]
        return None

    def _save_criterion_records(
        self,
        judge_result_id: uuid.UUID,
        criteria: list[dict[str, Any]],
    ) -> None:
        from app.modules.grading.models import LlmCriterionResult

        for idx, c in enumerate(criteria):
            c_is_met = bool(c.get("is_met"))
            if c_is_met:
                reason_code = None
            else:
                raw_code = c.get("reason_code")
                reason_code = (
                    REASON_CRITERION_UNMET if (not raw_code or raw_code == "unmet") else raw_code
                )
            crit_obj = LlmCriterionResult(
                id=uuid.uuid4(),
                llm_judge_result_id=judge_result_id,
                criterion_key=c.get("key") or f"crit_{idx + 1}",
                criterion_text=c.get("text") or f"Kriteria {idx + 1}",
                is_met=c_is_met,
                explanation=c.get("explanation"),
                reason_code=reason_code,
            )
            self.db.add(crit_obj)
        self.db.flush()

    def _save_quality_judge_record(
        self,
        grading_result_id: uuid.UUID,
        att: AttemptGradeResult,
        case_code: str,
        rep_no: int,
        score_val: Decimal,
    ) -> None:
        from app.modules.grading.models import (
            JudgeKind,
            JudgeStatus,
            LlmJudgeResult,
        )

        crit_count = len(att.criteria)
        if crit_count > 0:
            total = crit_count
            met = sum(1 for c in att.criteria if c.get("is_met"))
        else:
            total = 1
            met = 1 if score_val >= Decimal("0.50") else 0

        judge_row = LlmJudgeResult(
            id=uuid.uuid4(),
            grading_result_id=grading_result_id,
            judge_run_no=rep_no,
            kind=JudgeKind.QUALITY,
            status=JudgeStatus.SUCCEEDED,
            prompt=f"Penilaian kualitas jawaban untuk kasus {case_code}",
            criteria_met=met,
            criteria_total=total,
            criteria_score=score_val,
            rubric_level=None,
            explanation=att.explanation,
            error=None,
            failure_kind=None,
        )
        self.db.add(judge_row)
        self.db.flush()
        self._save_criterion_records(judge_row.id, att.criteria)

    def _save_format_judge_record(
        self,
        grading_result_id: uuid.UUID,
        case_code: str,
        rep_no: int,
        format_score: Decimal,
    ) -> None:
        from app.modules.grading.models import (
            JudgeKind,
            JudgeStatus,
            LlmJudgeResult,
        )

        format_row = LlmJudgeResult(
            id=uuid.uuid4(),
            grading_result_id=grading_result_id,
            judge_run_no=rep_no,
            kind=JudgeKind.FORMAT,
            status=JudgeStatus.SUCCEEDED,
            prompt=f"Penilaian format jawaban untuk kasus {case_code}",
            criteria_met=1 if format_score >= Decimal("0.50") else 0,
            criteria_total=1,
            criteria_score=format_score,
            rubric_level=None,
            explanation=None,
            error=None,
            failure_kind=None,
        )
        self.db.add(format_row)
        self.db.flush()

    def _save_llm_judge_records(
        self,
        grading_result_id: uuid.UUID,
        att: AttemptGradeResult,
        case_code: str,
    ) -> None:
        rep_no = max(1, min(5, att.repetition_no))
        if att.is_technical_disruption or att.score is None:
            return

        if att.score.quality is not None:
            self._save_quality_judge_record(
                grading_result_id, att, case_code, rep_no, att.score.quality
            )

        if att.score.format is not None:
            self._save_format_judge_record(grading_result_id, case_code, rep_no, att.score.format)


def _evaluate_attempt_score(
    att: AttemptGradeInput,
    case: CaseGradeInput,
) -> ScorePair | None:
    det = (
        round_two_digits(att.deterministic_score)
        if att.deterministic_score is not None
        else score_deterministic_citations(att.answer_text, case.legal_refs)
    )
    qual = round_two_digits(att.quality_score) if att.quality_score is not None else None
    fmt = round_two_digits(att.format_score) if att.format_score is not None else None

    if det is None and qual is None and fmt is None:
        return None
    return ScorePair(deterministic=det, quality=qual, format=fmt)


def _grade_single_attempt(
    att: AttemptGradeInput,
    case: CaseGradeInput,
) -> tuple[AttemptGradeResult, ScorePair | None]:
    disrupted = check_technical_disruption(
        status=att.status,
        answer_text=att.answer_text,
        failure_kind=att.failure_kind,
        platform_refused=att.platform_refused,
        judge_error=att.judge_error,
    )
    score = None if disrupted else _evaluate_attempt_score(att, case)
    att_status = (
        att.status if isinstance(att.status, AttemptStatus) else AttemptStatus(str(att.status))
    )
    att_source = (
        att.source if isinstance(att.source, AttemptSource) else AttemptSource(str(att.source))
    )
    exp = att.explanation or (att.judge_error if disrupted else None)
    result = AttemptGradeResult(
        attempt_id=att.attempt_id,
        repetition_no=att.repetition_no,
        status=att_status,
        source=att_source,
        is_technical_disruption=disrupted,
        score=score,
        answer_text=att.answer_text,
        explanation=exp,
        reasons=att.reasons,
        criteria=att.criteria,
    )
    return result, score


def _grade_single_case(case: CaseGradeInput) -> CaseGradeResult:
    attempt_results: list[AttemptGradeResult] = []
    attempt_scores: list[ScorePair | None] = []
    for att in case.attempts:
        res, sc = _grade_single_attempt(att, case)
        attempt_results.append(res)
        attempt_scores.append(sc)

    case_score = aggregate_case_scores(attempt_scores)
    tag = (
        case.split_tag
        if isinstance(case.split_tag, SplitTag) or case.split_tag is None
        else SplitTag(str(case.split_tag))
    )
    return CaseGradeResult(
        snapshot_item_id=case.snapshot_item_id,
        case_id=case.case_id,
        case_code=case.case_code,
        title=case.title,
        split_tag=tag,
        category=case.category,
        question=case.question,
        masked=False,
        scores=case_score,
        attempts=attempt_results,
    )


def grade_session_data(
    *,
    session_id: uuid.UUID,
    cases_input: Sequence[CaseGradeInput],
    triggered_by: uuid.UUID,
    rubric_version: str | None = None,
    existing_runs: Sequence[SessionGradingResponse] = (),
    now: datetime | None = None,
    session_label: str | None = None,
    product_name: str | None = None,
) -> SessionGradingResponse:
    """Melakukan evaluasi gangguan teknis, penilaian tiap percobaan, dan agregasi skor."""
    timestamp = now or datetime.now(UTC)
    rubric_ver = rubric_version or DEFAULT_RUBRIC_VERSION
    kind = "regrade" if len(existing_runs) > 0 else "initial"

    processed_cases = [_grade_single_case(c) for c in cases_input]
    three_sc = compute_three_scores(processed_cases)
    cat_sc = compute_category_scores(processed_cases)
    # Tanpa satu pun skor, run gagal dan tidak menggeser hasil terbaru yang sudah ada (AC4).
    has_score = any(case.scores is not None for case in processed_cases)

    return SessionGradingResponse(
        grading_run_id=uuid.uuid4(),
        session_id=session_id,
        session_label=session_label,
        product_name=product_name,
        rubric_version=rubric_ver,
        kind=kind,
        status=RUN_COMPLETED if has_score else RUN_FAILED,
        triggered_by=triggered_by,
        triggered_at=timestamp,
        is_latest=has_score,
        scores=three_sc,
        category_scores=cat_sc,
        cases=processed_cases,
    )


def _mask_test_case(case: CaseGradeResult) -> CaseGradeResult:
    masked_attempts = [
        AttemptGradeResult(
            attempt_id=att.attempt_id,
            repetition_no=att.repetition_no,
            status=att.status,
            source=att.source,
            is_technical_disruption=att.is_technical_disruption,
            score=None,  # Disamarkan per percobaan
            answer_text=None,  # Disamarkan
            explanation=None,
            reasons=[],
            criteria=[],
        )
        for att in case.attempts
    ]
    return CaseGradeResult(
        snapshot_item_id=case.snapshot_item_id,
        case_id=case.case_id,
        case_code=MASKED_CASE_CODE,
        title=MASKED_TITLE,
        split_tag=case.split_tag,
        category=None,  # Disamarkan untuk Developer AI agar tidak bocor
        question=None,  # Disamarkan
        masked=True,
        scores=None,  # Skor per kasus disamarkan!
        attempts=masked_attempts,
    )


def _mask_for_developer_ai(grading: SessionGradingResponse) -> SessionGradingResponse:
    masked_cases: list[CaseGradeResult] = []
    dev_cases: list[CaseGradeResult] = []
    for case in grading.cases:
        if _case_tag(case.split_tag) == SplitTag.TEST.value:
            masked_cases.append(_mask_test_case(case))
        else:
            masked_cases.append(case)
            dev_cases.append(case)

    # PBI-11 AC9: Dev AI hanya melihat skor total agregat test (ThreeScores.test).
    # category_scores untuk Dev AI hanya dihitung dari kasus dev agar tidak
    # membocorkan performa per kategori pada kasus test.
    dev_category_scores = compute_category_scores(dev_cases)

    return SessionGradingResponse(
        grading_run_id=grading.grading_run_id,
        session_id=grading.session_id,
        session_label=grading.session_label,
        product_name=grading.product_name,
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


def _mask_for_viewer(grading: SessionGradingResponse) -> SessionGradingResponse:
    masked_cases = [
        c.model_copy(
            update={
                "question": None,
                "attempts": [att.model_copy(update={"answer_text": None}) for att in c.attempts],
            }
        )
        for c in grading.cases
    ]
    return grading.model_copy(update={"cases": masked_cases})


def apply_visibility_mask(
    grading: SessionGradingResponse,
    viewer_role: Role,
    session_status: str | SessionStatus,
) -> SessionGradingResponse:
    """Menegakkan aturan visibilitas dan penyamaran server (AC8, D10).

    - Sesi berjalan: HANYA Admin. Viewer dan Developer AI ditolak (403).
    - Sesi selesai: Admin melihat lengkap. Viewer melihat skor, alasan, penjelasan, dan kriteria,
      namun question dan raw answer_text disamarkan (null) sesuai keputusan PO (SCRUM-227).
      Developer AI melihat kasus dev, sedangkan kasus test disamarkan per-kasus (isi & skor kasus disembunyikan),
      namun skor total agregat test tetap terlihat di ThreeScores.
    - Kontributor: ditolak (403).
    """
    status_str = (
        session_status.value if isinstance(session_status, SessionStatus) else str(session_status)
    )

    if not has_ended(status_str):
        if viewer_role != Role.ADMIN:
            raise ForbiddenError(
                "Hasil sesi yang masih berjalan hanya dapat dilihat Admin.",
                code="FORBIDDEN",
            )
        return grading

    if viewer_role == Role.CONTRIBUTOR:
        raise ForbiddenError(
            "Peran Kontributor tidak berwenang melihat hasil penilaian.",
            code="FORBIDDEN",
        )

    if viewer_role == Role.ADMIN:
        return grading

    if viewer_role == Role.VIEWER:
        return _mask_for_viewer(grading)

    if viewer_role == Role.DEVELOPER_AI:
        return _mask_for_developer_ai(grading)

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


def _is_judge_failure(failure_kind: str | None) -> bool:
    if not failure_kind:
        return False
    return "judge" in failure_kind.lower() or failure_kind in (
        "judge_error",
        "judge_failed",
        "judge_timeout",
    )


def _load_call_logs_metadata(
    db: Session,
    attempt_ids: list[uuid.UUID],
) -> tuple[dict[uuid.UUID, str], dict[uuid.UUID, str]]:
    if not attempt_ids:
        return {}, {}
    logs = db.scalars(
        select(CallLog).where(CallLog.attempt_id.in_(attempt_ids)).order_by(CallLog.try_no.desc())
    ).all()
    failure_kinds: dict[uuid.UUID, str] = {}
    judge_errors: dict[uuid.UUID, str] = {}
    for log in logs:
        if log.attempt_id not in failure_kinds and log.failure_kind:
            failure_kinds[log.attempt_id] = log.failure_kind
        if log.attempt_id not in judge_errors and _is_judge_failure(log.failure_kind):
            judge_errors[log.attempt_id] = log.failure_kind
    return failure_kinds, judge_errors


def _extract_criterion_info(
    db: Session,
    j_row_id: uuid.UUID,
) -> tuple[list[dict[str, Any]], list[str]]:
    from app.modules.grading.models import LlmCriterionResult

    crit_rows = db.scalars(
        select(LlmCriterionResult)
        .where(LlmCriterionResult.llm_judge_result_id == j_row_id)
        .order_by(LlmCriterionResult.id.asc())
    ).all()
    c_list = [
        {
            "key": cr.criterion_key,
            "text": cr.criterion_text,
            "is_met": cr.is_met,
            "explanation": cr.explanation,
            "reason_code": cr.reason_code,
        }
        for cr in crit_rows
    ]
    reasons = [cr.reason_code for cr in crit_rows if cr.reason_code and not cr.is_met]
    return c_list, reasons


def _calculate_db_quality_score(j_row: Any, is_succeeded: bool) -> Decimal | None:
    if not is_succeeded:
        return None
    if j_row.criteria_score is not None:
        return round_two_digits(j_row.criteria_score)
    if j_row.rubric_level is not None:
        return round_two_digits(Decimal(j_row.rubric_level))
    return None


def _populate_attempt_judge_row(db: Session, data: dict[str, Any], j_row: Any) -> None:
    from app.modules.grading.models import JudgeKind, JudgeStatus

    is_succeeded = j_row.status == JudgeStatus.SUCCEEDED
    if j_row.kind == JudgeKind.FORMAT:
        if "format_score" not in data and is_succeeded:
            data["format_score"] = j_row.criteria_score
    elif "quality_score" not in data:
        c_list, reasons = _extract_criterion_info(db, j_row.id)
        data["quality_score"] = _calculate_db_quality_score(j_row, is_succeeded)
        data["explanation"] = j_row.explanation
        data["reasons"] = reasons
        data["criteria"] = c_list
        if not is_succeeded and "judge_error" not in data:
            data["judge_error"] = j_row.error or j_row.failure_kind


def _load_judge_metadata_for_attempts(
    db: Session,
    attempt_ids: list[uuid.UUID],
) -> dict[uuid.UUID, dict[str, Any]]:
    if not attempt_ids:
        return {}
    from app.modules.grading.models import (
        GradingResult,
        LlmJudgeResult,
    )

    rows = db.execute(
        select(GradingResult.attempt_id, LlmJudgeResult)
        .join(LlmJudgeResult, LlmJudgeResult.grading_result_id == GradingResult.id)
        .where(GradingResult.attempt_id.in_(attempt_ids))
        .order_by(LlmJudgeResult.created_at.desc())
    ).all()

    judge_data: dict[uuid.UUID, dict[str, Any]] = defaultdict(dict)
    for att_id, j_row in rows:
        _populate_attempt_judge_row(db, judge_data[att_id], j_row)
    return dict(judge_data)


def _apply_judge_criteria_records(row: Any) -> list[dict[str, Any]]:
    records = []
    for idx, c in enumerate(row.criteria):
        if c.met:
            r_code = None
        else:
            r_code = (
                REASON_CRITERION_UNMET
                if (not c.reason_id or c.reason_id == "unmet")
                else c.reason_id
            )
        records.append(
            {
                "key": f"crit_{idx + 1}",
                "text": c.criterion,
                "is_met": c.met,
                "explanation": c.explanation,
                "reason_code": r_code,
            }
        )
    return records


def _apply_judge_quality(att: AttemptGradeInput, row: Any) -> None:
    att.quality_score = round_two_digits(row.score)
    att.explanation = row.explanation
    r_id = REASON_CRITERION_UNMET if row.reason_id == "unmet" else row.reason_id
    att.reasons = [r_id] if r_id else []
    att.criteria = _apply_judge_criteria_records(row)


def _apply_judge_row_to_attempt(att: AttemptGradeInput, row: Any) -> None:
    if row.score is not None:
        if getattr(row, "kind", None) == "format":
            att.format_score = round_two_digits(row.score)
        else:
            _apply_judge_quality(att, row)
    elif row.error or row.failure_kind:
        att.judge_error = row.error or row.failure_kind


def _build_judge_inputs(case: CaseGradeInput, answer_text: str | None):
    from app.modules.runs.judge import JudgeInputs

    must_contain, must_not_contain, conclusion = _extract_criteria_items(case.answer_criteria)
    crit_lines = list(must_contain) + [f"Not: {x}" for x in must_not_contain]
    if conclusion:
        crit_lines.append(f"Conclusion: {conclusion}")
    if not crit_lines:
        crit_lines = [case.title or "Jawaban substantif"]

    ground_truth = conclusion or (
        must_contain[0] if must_contain else (case.title or "Jawaban hukum")
    )
    return JudgeInputs(
        criteria="\n".join(crit_lines),
        ground_truth=ground_truth,
        question=case.question or case.title or "Pertanyaan kasus",
        provider_answer=answer_text or "",
    )


def _execute_judge_for_attempt(
    asker: object,
    att: AttemptGradeInput,
    case: CaseGradeInput,
) -> None:
    """Menjalankan evaluasi penilai LLM (SCRUM-224 run_judge) untuk percobaan non-gangguan."""
    from app.modules.runs.judge import run_judge

    judge_in = _build_judge_inputs(case, att.answer_text)
    try:
        writer = run_judge(asker, judge_in, repetitions=1)
        for row in writer.rows:
            _apply_judge_row_to_attempt(att, row)
        if att.judge_error is None and (att.quality_score is None or att.format_score is None):
            att.judge_error = "judge_incomplete"
    except Exception as exc:
        att.judge_error = str(exc)


def _build_attempt_input(
    a: Attempt,
    failure_kinds: dict[uuid.UUID, str],
    judge_errors: dict[uuid.UUID, str],
    judge_data: dict[uuid.UUID, dict[str, Any]] | None = None,
) -> AttemptGradeInput:
    f_kind = failure_kinds.get(a.id)
    j_err = judge_errors.get(a.id)
    platform_refused = bool(a.answer_text and "[Platform menolak menjawab]" in a.answer_text) or (
        f_kind == "platform_refused"
    )
    j_info = (judge_data or {}).get(a.id, {})
    return AttemptGradeInput(
        attempt_id=a.id,
        repetition_no=a.repetition_no,
        status=a.status,
        source=a.source,
        answer_text=a.answer_text,
        failure_kind=f_kind,
        platform_refused=platform_refused,
        judge_error=j_err or j_info.get("judge_error"),
        quality_score=j_info.get("quality_score"),
        format_score=j_info.get("format_score"),
        explanation=j_info.get("explanation"),
        reasons=j_info.get("reasons") or [],
        criteria=j_info.get("criteria") or [],
    )


def _build_case_grade_input(
    item: CaseSnapshotItem,
    attempts: list[AttemptGradeInput],
) -> CaseGradeInput:
    body = item.body or {}
    sections = body.get("sections") or {}
    return CaseGradeInput(
        snapshot_item_id=item.id,
        case_id=item.case_id,
        case_code=sections.get("case_code") or "",
        title=sections.get("identity.title") or "",
        split_tag=sections.get("split_tag") or "dev",
        category=sections.get("identity.category") or sections.get("category"),
        question=sections.get("identity.question"),
        legal_refs=sections.get("legal_refs") or body.get("legal_refs") or [],
        answer_criteria=sections.get("answer_criteria") or body.get("answer_criteria"),
        attempts=attempts,
    )


def _load_case_grade_inputs(db: Session, session_id: uuid.UUID) -> list[CaseGradeInput]:
    """Membaca snapshot item dan attempts dari database untuk grading."""
    items = db.scalars(
        select(CaseSnapshotItem)
        .join(CaseSnapshot, CaseSnapshot.id == CaseSnapshotItem.snapshot_id)
        .where(CaseSnapshot.session_id == session_id)
    ).all()

    attempts_db = db.scalars(
        select(Attempt)
        .where(Attempt.session_id == session_id)
        .order_by(Attempt.snapshot_item_id, Attempt.repetition_no)
    ).all()

    attempt_ids = [a.id for a in attempts_db]
    call_failure_kinds, call_judge_errors = _load_call_logs_metadata(db, attempt_ids)

    attempts_by_item: dict[uuid.UUID, list[AttemptGradeInput]] = defaultdict(list)
    for a in attempts_db:
        attempts_by_item[a.snapshot_item_id].append(
            _build_attempt_input(a, call_failure_kinds, call_judge_errors, None)
        )

    return [_build_case_grade_input(item, attempts_by_item.get(item.id, [])) for item in items]


def _resolve_rubric_version(db: Session, requested_version: str | None) -> str:
    """Mengambil versi rubrik aktif bila tidak diisi, dan memvalidasi ke tabel rubric_versions."""
    from app.modules.grading.models import RubricVersion

    if requested_version:
        ver_str = requested_version.lower().lstrip("v")
        ver_no = int(ver_str) if ver_str.isdigit() else None

        query = select(RubricVersion)
        if ver_no is not None:
            query = query.where(RubricVersion.version_no == ver_no)
        else:
            try:
                query = query.where(RubricVersion.id == uuid.UUID(requested_version))
            except (ValueError, TypeError):
                query = query.where(RubricVersion.version_no == -1)

        ver = db.scalars(query).first()
        if ver is None:
            raise NotFoundError(
                f"Versi rubrik '{requested_version}' tidak ditemukan.",
                code="RUBRIC_VERSION_NOT_FOUND",
            )
        return f"v{ver.version_no}"

    active_ver = db.scalars(select(RubricVersion).where(RubricVersion.is_active)).first()
    if active_ver is not None:
        return f"v{active_ver.version_no}"
    raise ConflictError("Tidak ada versi rubrik yang aktif.", code="RUBRIC_NOT_ACTIVE")


def _resolve_active_judge_asker(
    db: Session,
    session: MeasurementSession,
    judge_asker: object | None,
) -> tuple[object | None, str | None]:
    if judge_asker is not None:
        return judge_asker, None
    if session.judge_provider_id is None:
        # Sesi lama dari sebelum penilai wajib dipilih (PBI-14): gangguan teknis, bukan skip.
        return None, JUDGE_NOT_CONFIGURED
    try:
        from app.modules.providers.judge_service import build_judge

        binding = build_judge(db, session.judge_provider_id)
        return binding.asker, None
    except Exception:
        # Hanya id penilai yang dicatat: pesan galat bisa memuat isi kredensial.
        logger.error(
            "Judge %s could not be built, attempts will be marked judge_unavailable",
            session.judge_provider_id,
            exc_info=False,
        )
        return None, JUDGE_UNAVAILABLE


def _should_grade_with_judge(att: AttemptGradeInput) -> bool:
    return not check_technical_disruption(
        status=att.status,
        answer_text=att.answer_text,
        failure_kind=att.failure_kind,
        platform_refused=att.platform_refused,
        judge_error=att.judge_error,
    ) and (att.quality_score is None or att.format_score is None)


def _set_cases_judge_error(cases: Sequence[CaseGradeInput], error: str) -> None:
    for case in cases:
        for att in case.attempts:
            att.judge_error = error


def _execute_judge_for_all_cases(
    asker: object | None,
    judge_load_error: str | None,
    cases: Sequence[CaseGradeInput],
) -> None:
    if judge_load_error:
        _set_cases_judge_error(cases, judge_load_error)
        return
    if asker is None:
        return
    for case in cases:
        for att in case.attempts:
            if _should_grade_with_judge(att):
                _execute_judge_for_attempt(asker, att, case)


def grade_session(
    db: Session,
    session_id: uuid.UUID,
    payload: GradeSessionRequest | None = None,
    *,
    actor_id: uuid.UUID,
    actor_role: Role,
    store: GradingStore | None = None,
    now: datetime | None = None,
    judge_asker: object | None = None,
) -> SessionGradingResponse:
    """Memicu penilaian manual untuk sesi pengukuran (AC6, AC7)."""
    if actor_role != Role.ADMIN:
        raise ForbiddenError("Hanya Admin yang dapat memicu penilaian.", code="FORBIDDEN")

    session = db.get(MeasurementSession, session_id)
    if session is None:
        raise NotFoundError(MEASUREMENT_NOT_FOUND_MESSAGE, code="MEASUREMENT_NOT_FOUND")

    ensure_session_gradable(session)

    grading_store = store or DbGradingStore(db)
    cases = _load_case_grade_inputs(db, session_id)

    raw_rubric_version = payload.rubric_version if payload else None
    rubric_version = _resolve_rubric_version(db, raw_rubric_version)
    existing_runs = grading_store.get_grading_runs(session_id)

    p_names = providers_service.product_names(db, {session.product_id})
    product_name = p_names.get(session.product_id, "")

    active_asker, judge_load_error = _resolve_active_judge_asker(db, session, judge_asker)
    _execute_judge_for_all_cases(active_asker, judge_load_error, cases)

    response = grade_session_data(
        session_id=session_id,
        session_label=session.label,
        product_name=product_name,
        cases_input=cases,
        triggered_by=actor_id,
        rubric_version=rubric_version,
        existing_runs=existing_runs,
        now=now,
    )
    if isinstance(grading_store, DbGradingStore):
        grading_store.save_grading_run(response, cases_input=cases)
    else:
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
        raise NotFoundError(MEASUREMENT_NOT_FOUND_MESSAGE, code="MEASUREMENT_NOT_FOUND")

    grading_store = store or DbGradingStore(db)
    latest_run = grading_store.get_latest_grading_run(session_id)
    if latest_run is None:
        raise NotFoundError(
            "Hasil penilaian belum tersedia. Silakan picu penilaian terlebih dahulu.",
            code="GRADING_NOT_FOUND",
        )

    if not latest_run.session_label or not latest_run.product_name:
        p_names = providers_service.product_names(db, {session.product_id})
        p_name = p_names.get(session.product_id, "")
        latest_run = latest_run.model_copy(
            update={
                "session_label": latest_run.session_label or session.label,
                "product_name": latest_run.product_name or p_name,
            }
        )

    return apply_visibility_mask(
        latest_run,
        viewer_role=viewer_role,
        session_status=session.status,
    )


def to_run_summary(run: SessionGradingResponse) -> GradingRunSummary:
    """Meringkas satu hasil penilaian menjadi baris riwayat tanpa rincian kasus."""
    return GradingRunSummary(
        grading_run_id=run.grading_run_id,
        rubric_version=run.rubric_version,
        kind=run.kind,
        status=run.status,
        triggered_by=run.triggered_by,
        triggered_at=run.triggered_at,
        is_latest=run.is_latest,
        scores=run.scores,
    )


def list_grading_runs(
    db: Session,
    session_id: uuid.UUID,
    *,
    store: GradingStore | None = None,
) -> list[GradingRunSummary]:
    """Riwayat penilaian satu sesi, terbaru di atas (PBI-14 AC7).

    Sesi yang belum pernah dinilai mengembalikan daftar kosong. Run dengan waktu sama
    tetap dalam urutan simpan, karena sort Python stabil juga saat dibalik.
    """
    if db.get(MeasurementSession, session_id) is None:
        raise NotFoundError(MEASUREMENT_NOT_FOUND_MESSAGE, code="MEASUREMENT_NOT_FOUND")

    grading_store = store or DbGradingStore(db)
    runs = sorted(
        grading_store.get_grading_runs(session_id), key=lambda r: r.triggered_at, reverse=True
    )
    return [to_run_summary(run) for run in runs]


def _resolve_session_rubric_map(db: Session, session_ids: list[uuid.UUID]) -> dict[uuid.UUID, str]:
    from app.modules.grading.models import GradingRun, RubricVersion

    runs = db.scalars(
        select(GradingRun).where(
            GradingRun.session_id.in_(session_ids),
            GradingRun.is_latest,
        )
    ).all()

    rubric_map: dict[uuid.UUID, str] = {}
    if runs:
        run_by_session = {r.session_id: r for r in runs}
        rubric_ids = {r.rubric_version_id for r in runs}
        rubric_vers = db.scalars(
            select(RubricVersion).where(RubricVersion.id.in_(rubric_ids))
        ).all()
        version_no_map = {rv.id: f"v{rv.version_no}" for rv in rubric_vers}
        for s_id, run in run_by_session.items():
            rubric_map[s_id] = version_no_map.get(run.rubric_version_id, "v1")

    remaining_ids = [s_id for s_id in session_ids if s_id not in rubric_map]
    if remaining_ids:
        from app.modules.audit.models import AuditLog

        logs = db.scalars(
            select(AuditLog).where(
                AuditLog.entity_type == "measurement_session",
                AuditLog.entity_id.in_(remaining_ids),
                AuditLog.action.in_(
                    (ACTION_MEASUREMENT_SESSION_GRADED, ACTION_MEASUREMENT_SESSION_REGRADED)
                ),
            )
        ).all()
        for log in logs:
            if log.entity_id and log.after and isinstance(log.after, dict):
                ver = log.after.get("rubric_version", "v1")
                rubric_map[log.entity_id] = str(ver)
    return rubric_map


def list_graded_sessions(
    db: Session,
    *,
    viewer_role: Role | str,
) -> list[GradedSessionListItem]:
    """Daftar sesi selesai yang telah dinilai untuk Admin dan Viewer (SCRUM-226, SCRUM-227).

    Hanya sesi yang sudah selesai, tidak diarsipkan, dan memiliki hasil penilaian.
    """
    role = Role(viewer_role) if isinstance(viewer_role, str) else viewer_role
    if role not in (Role.ADMIN, Role.VIEWER):
        raise ForbiddenError(
            "Peran Anda tidak berwenang melihat daftar hasil penilaian.",
            code="FORBIDDEN",
        )

    ended_statuses = (
        SessionStatus.FINISHED,
        SessionStatus.FINISHED_WITH_FAILURES,
        SessionStatus.FAILED,
        SessionStatus.FINALIZED,
        "finished",
        "finished_with_failures",
        "failed",
        "finalized",
    )

    sessions = db.scalars(
        select(MeasurementSession)
        .where(
            MeasurementSession.archived_at.is_(None),
            MeasurementSession.status.in_(ended_statuses),
        )
        .order_by(
            MeasurementSession.finished_at.desc().nullslast(),
            MeasurementSession.created_at.desc(),
        )
    ).all()

    if not sessions:
        return []

    session_ids = [s.id for s in sessions]
    product_ids = {s.product_id for s in sessions}
    p_names = providers_service.product_names(db, product_ids)
    rubric_map = _resolve_session_rubric_map(db, session_ids)

    return [
        GradedSessionListItem(
            session_id=s.id,
            label=s.label,
            product_name=p_names.get(s.product_id, ""),
            finished_at=s.finished_at,
            rubric_version=rubric_map.get(s.id),
        )
        for s in sessions
        if s.id in rubric_map
    ]
