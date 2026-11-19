"""Satu aturan: sesi mana yang boleh dipakai, dan kapan produk boleh dibandingkan.

PBI-15 SCRUM-79 SCRUM-301, D2. Dipakai bersama oleh PBI-15, PBI-16, dan PBI-17 supaya
laporan perbandingan, laporan tren, dan halaman pembuat laporan memakai aturan yang
sama, bukan tiga salinan yang pelan-pelan berbeda.

Modul ini murni: tidak menyentuh database dan tidak mengenal HTTP. Pemanggil membaca
D2 menjadi SessionFacts, lalu memakai hasilnya.

Dua keputusan yang perlu dicatat:

1. "Versi suite sama" (AC13 syarat c) dicek dari apa yang memang disimpan D2. Tabel
   suites tidak punya nomor versi, dan suite_snapshots sudah dipensiunkan PBI-11
   (SCRUM-207), jadi versi suite satu sesi = suite yang dipilih (measurement_session_suites)
   ditambah versi kasus pada snapshot sesinya. Dua sesi dianggap beda versi suite bila
   suite-nya berbeda, atau bila satu kasus yang sama muncul pada case_version_id berbeda.

2. Karena versi kasus sudah dijaga syarat di atas, irisan kasus (AC1) cukup dikunci pada
   reference id kasus; kasus yang sama pada versi berbeda ditolak dengan alasan, bukan
   diam-diam dibuang dari irisan.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from app.modules.runs.manual_session_rules import date_range_exceeds
from app.modules.runs.models import SessionPurpose, SessionStatus, SessionType
from app.modules.runs.session_rules import is_report_eligible
from app.shared.exceptions import ConflictError

# Alasan satu sesi tidak layak jadi dasar laporan (AC2, AC4).
SESSION_NOT_FINISHED = "SESSION_NOT_FINISHED"
SESSION_CANCELLED = "SESSION_CANCELLED"
SESSION_RUN_FAILED = "SESSION_RUN_FAILED"
SESSION_NOT_ACCEPTED_AS_IS = "SESSION_NOT_ACCEPTED_AS_IS"
SESSION_NOT_GRADED = "SESSION_NOT_GRADED"

# Syarat perbandingan antar produk (AC13). Satu kode per syarat, supaya penolakannya
# bisa dijelaskan satu per satu, bukan sebagai satu pesan gabungan.
RUBRIC_VERSION = "RUBRIC_VERSION"
JUDGE_MODEL = "JUDGE_MODEL"
SUITE_VERSION = "SUITE_VERSION"

# Penanda: perbandingan tetap jalan, tetapi laporan wajib menuliskannya (AC3, AC4, AC5).
TRIAL_SESSION = "TRIAL_SESSION"
MANUAL_ANSWER_RANGE_OVER_LIMIT = "MANUAL_ANSWER_RANGE_OVER_LIMIT"
MODEL_VERSION_DIFFERS = "MODEL_VERSION_DIFFERS"
REPETITIONS_DIFFER = "REPETITIONS_DIFFER"

# Irisan kosong: peringatan, tanpa satu pun angka perbandingan (AC1).
NO_SHARED_CASES = "NO_SHARED_CASES"

# PBI-15 AC5. D8 membiarkan batasnya TBA; laporan memakai 7 hari.
MANUAL_ANSWER_RANGE_LIMIT_DAYS = 7

_ALASAN_STATUS = {
    SessionStatus.CANCELLED: SESSION_CANCELLED,
    SessionStatus.FAILED: SESSION_RUN_FAILED,
    SessionStatus.FINISHED_WITH_FAILURES: SESSION_NOT_ACCEPTED_AS_IS,
}

_PESAN_TIDAK_LAYAK = {
    SESSION_NOT_FINISHED: "Sesi ini belum selesai, jadi belum bisa menjadi dasar laporan.",
    SESSION_CANCELLED: (
        "Sesi ini dibatalkan. Lanjutkan sampai selesai dulu sebelum dipakai di laporan."
    ),
    SESSION_RUN_FAILED: "Proses run sesi ini gagal. Perbaiki dulu, lalu run ulang.",
    SESSION_NOT_ACCEPTED_AS_IS: (
        "Sesi ini selesai dengan kegagalan dan belum diterima apa adanya oleh Admin."
    ),
    SESSION_NOT_GRADED: "Sesi ini belum dinilai, jadi belum punya skor untuk dilaporkan.",
}


@dataclass(frozen=True)
class CaseRef:
    """Identitas satu kasus yang sudah punya skor di sebuah sesi.

    case_id adalah reference id yang tetap lintas versi; case_version_id menyebut versi
    yang benar-benar dijawab; case_code hanya untuk pesan ke pembaca laporan.
    """

    case_id: uuid.UUID
    case_version_id: uuid.UUID
    case_code: str


@dataclass(frozen=True)
class SessionFacts:
    """Satu sesi pengukuran, sebagaimana dibutuhkan aturan ini. Dibaca dari D2.

    rubric_version_id dan judge_provider_id berasal dari grading run terakhir sesi
    (grading_runs.is_latest); rubric_version_id kosong berarti sesi belum pernah dinilai.
    scored_cases hanya berisi kasus yang sudah punya skor, bukan seluruh isi snapshot.
    """

    session_id: uuid.UUID
    product_id: uuid.UUID
    product_name: str
    session_type: SessionType
    purpose: SessionPurpose
    status: SessionStatus
    accepted_as_is_at: datetime | None
    rubric_version_id: uuid.UUID | None
    judge_provider_id: uuid.UUID | None
    suite_ids: frozenset[uuid.UUID]
    model_version: str | None
    repetitions: int
    answer_date_from: datetime | None
    answer_date_to: datetime | None
    scored_cases: tuple[CaseRef, ...]


@dataclass(frozen=True)
class Rejection:
    """Satu syarat perbandingan yang tidak terpenuhi, dengan alasannya (AC13)."""

    requirement: str
    message: str


@dataclass(frozen=True)
class ComparisonPlan:
    """Hasil aturan untuk satu permintaan laporan.

    comparable False berarti laporan tidak boleh menampilkan angka perbandingan: alasannya
    ada di ineligible (per sesi), rejections (per syarat), atau warning (irisan kosong).
    """

    sessions: tuple[SessionFacts, ...]
    ineligible: dict[uuid.UUID, tuple[str, ...]]
    rejections: tuple[Rejection, ...]
    markers: tuple[str, ...]
    shared_cases: tuple[CaseRef, ...]
    warning: str | None

    @property
    def comparable(self) -> bool:
        return not self.ineligible and not self.rejections and self.warning is None


def eligibility_problems(facts: SessionFacts) -> tuple[str, ...]:
    """Semua alasan sesi ini belum boleh jadi dasar laporan (AC2, AC4). Kosong = layak.

    Status layak diputuskan is_report_eligible (D4, PBI-12 AC7 dan AC9), jadi aturan
    status hanya hidup di satu tempat. Sesi manual ikut aturan yang sama: hanya Selesai
    (finalized) yang layak, Dinilai belum.
    """
    status = SessionStatus(facts.status)
    alasan: list[str] = []

    if not is_report_eligible(status, accepted_as_is_at=facts.accepted_as_is_at):
        alasan.append(_ALASAN_STATUS.get(status, SESSION_NOT_FINISHED))

    # "Selesai dan dinilai": status saja belum cukup, skornya harus ada.
    if facts.rubric_version_id is None:
        alasan.append(SESSION_NOT_GRADED)

    return tuple(alasan)


def is_eligible(facts: SessionFacts) -> bool:
    return not eligibility_problems(facts)


def ensure_eligible(facts: SessionFacts) -> None:
    """Tolak sesi yang tidak layak dengan alasan pertamanya sebagai kode galat."""
    alasan = eligibility_problems(facts)
    if alasan:
        raise ConflictError(_PESAN_TIDAK_LAYAK[alasan[0]], code=alasan[0])


def session_markers(facts: SessionFacts) -> tuple[str, ...]:
    """Penanda yang melekat pada satu sesi, bukan pada perbandingannya (AC4, AC5)."""
    penanda: list[str] = []

    if SessionPurpose(facts.purpose) is SessionPurpose.TRIAL:
        penanda.append(TRIAL_SESSION)

    if SessionType(facts.session_type) is SessionType.MANUAL and date_range_exceeds(
        facts.answer_date_from,
        facts.answer_date_to,
        limit_days=MANUAL_ANSWER_RANGE_LIMIT_DAYS,
    ):
        penanda.append(MANUAL_ANSWER_RANGE_OVER_LIMIT)

    return tuple(penanda)


def _versi_kasus_bentrok(sessions: Sequence[SessionFacts]) -> list[str]:
    """Kode kasus yang muncul pada lebih dari satu versi, diurutkan supaya pesannya stabil."""
    versi: dict[uuid.UUID, set[uuid.UUID]] = {}
    kode: dict[uuid.UUID, str] = {}

    for sesi in sessions:
        for kasus in sesi.scored_cases:
            versi.setdefault(kasus.case_id, set()).add(kasus.case_version_id)
            kode.setdefault(kasus.case_id, kasus.case_code)

    return sorted(kode[case_id] for case_id, dipakai in versi.items() if len(dipakai) > 1)


def comparison_rejections(sessions: Sequence[SessionFacts]) -> tuple[Rejection, ...]:
    """Syarat perbandingan yang tidak terpenuhi, satu alasan per syarat (AC13).

    Urutannya tetap rubrik, penilai, suite, supaya pesan yang dibaca pengguna dan yang
    diuji tidak berubah-ubah.
    """
    tolakan: list[Rejection] = []

    if len({sesi.rubric_version_id for sesi in sessions}) > 1:
        tolakan.append(
            Rejection(
                RUBRIC_VERSION,
                "Sesi yang dipilih dinilai dengan versi rubrik yang berbeda, "
                "jadi skornya tidak setara.",
            )
        )

    if len({sesi.judge_provider_id for sesi in sessions}) > 1:
        tolakan.append(
            Rejection(
                JUDGE_MODEL,
                "Sesi yang dipilih dinilai oleh model penilai yang berbeda, "
                "jadi skornya tidak setara.",
            )
        )

    bentrok = _versi_kasus_bentrok(sessions)
    if len({sesi.suite_ids for sesi in sessions}) > 1:
        tolakan.append(Rejection(SUITE_VERSION, "Sesi yang dipilih memakai suite yang berbeda."))
    elif bentrok:
        tolakan.append(
            Rejection(
                SUITE_VERSION,
                "Suite yang dipakai ada pada versi berbeda antar sesi. "
                f"Kasus yang berbeda versi: {', '.join(bentrok)}.",
            )
        )

    return tuple(tolakan)


def comparison_markers(sessions: Sequence[SessionFacts]) -> tuple[str, ...]:
    """Penanda perbandingan: beda versi model dan beda jumlah percobaan (AC3, AC5).

    Keduanya tidak menolak perbandingan, tetapi wajib tampil di laporan.
    """
    penanda: list[str] = []

    if len({sesi.model_version for sesi in sessions}) > 1:
        penanda.append(MODEL_VERSION_DIFFERS)

    if len({sesi.repetitions for sesi in sessions}) > 1:
        penanda.append(REPETITIONS_DIFFER)

    for sesi in sessions:
        penanda.extend(tanda for tanda in session_markers(sesi) if tanda not in penanda)

    return tuple(penanda)


def shared_cases(sessions: Sequence[SessionFacts]) -> tuple[CaseRef, ...]:
    """Kasus yang dijawab semua sesi (AC1), diurutkan menurut kode kasus.

    Tanpa sesi, tidak ada irisan. Versi kasus tidak dipakai sebagai kunci di sini karena
    beda versi sudah ditolak comparison_rejections; kalau dipakai, kasus yang berbeda
    versi akan hilang diam-diam dari irisan.
    """
    if not sessions:
        return ()

    irisan = set.intersection(
        *({kasus.case_id for kasus in sesi.scored_cases} for sesi in sessions)
    )

    pertama = {kasus.case_id: kasus for kasus in sessions[0].scored_cases}
    return tuple(sorted((pertama[case_id] for case_id in irisan), key=lambda k: k.case_code))


def plan_comparison(sessions: Sequence[SessionFacts]) -> ComparisonPlan:
    """Aturan lengkap untuk satu permintaan laporan (AC1–AC5, AC13).

    Begitu ada sesi tidak layak atau syarat yang gagal, irisan kasus tidak dihitung dan
    laporan tidak menampilkan angka: yang keluar hanya alasannya.
    """
    sessions = tuple(sessions)

    tidak_layak = {
        sesi.session_id: alasan for sesi in sessions if (alasan := eligibility_problems(sesi))
    }
    tolakan = comparison_rejections(sessions)
    penanda = comparison_markers(sessions)

    if tidak_layak or tolakan:
        return ComparisonPlan(
            sessions=sessions,
            ineligible=tidak_layak,
            rejections=tolakan,
            markers=penanda,
            shared_cases=(),
            warning=None,
        )

    irisan = shared_cases(sessions)
    kosong = not irisan

    return ComparisonPlan(
        sessions=sessions,
        ineligible={},
        rejections=(),
        markers=penanda,
        shared_cases=() if kosong else irisan,
        warning=NO_SHARED_CASES if kosong else None,
    )
