"""Satu aturan: sesi mana yang boleh dipakai, dan kapan produk boleh dibandingkan.

PBI-15 SCRUM-79 SCRUM-301, D2. Dipakai bersama oleh PBI-15, PBI-16, dan PBI-17 supaya
laporan perbandingan, laporan tren, dan halaman pembuat laporan memakai aturan yang
sama, bukan tiga salinan yang pelan-pelan berbeda.

Modul ini murni: tidak menyentuh database dan tidak mengenal HTTP. Pemanggil (adapter
D2, dikerjakan SCRUM-313) membaca baris jadi SessionFacts, lalu memakai hasilnya. Impor
app.modules.runs.models di sini hanya untuk enum status/tipe/tujuan sesi; modul grading
pun mengimpornya lintas modul, jadi ini pola yang sudah diterima, bukan pelanggaran.

Kontrak yang wajib dipenuhi adapter saat mengisi SessionFacts (bukan diputuskan di sini):

- rubric_version_id dan judge_* diambil dari grading run TERAKHIR yang selesai
  (grading_runs.is_latest dan status selesai). rubric_version_id kosong = belum dinilai.
- judge_model_version diisi identitas model penilai yang terekam saat grading
  (llm_judge_results.model_version). Nilai itu tercatat per pemanggilan judge, jadi adapter
  wajib memastikan satu versi yang konsisten untuk seluruh sesi; bila tidak konsisten atau
  tak diketahui, isi None, dan aturan ini akan menolak perbandingannya (bukan menganggap
  dua None sama). Penilaian memakai judge LLM untuk sesi otomatis MAUPUN manual (alur
  grading sama), jadi setiap sesi yang layak punya identitas judge; None = tak terverifikasi.
- snapshot_cases diisi SELURUH case_snapshot_items sesi (dinilai atau tidak), termasuk
  split_tag dari body.sections.split_tag. split_filter dibaca dari measurement_sessions.
  Untuk AC13, kasus pada split yang sama dibandingkan versi DAN keanggotaannya. Perbedaan
  snapshot yang memang disebabkan pilihan dev/test/both tidak dianggap konflik.
  Perubahan pada split yang tidak terekam oleh sesi lama tidak bisa dibuktikan dari data
  sekarang; versi suite imutabel penuh harus disediakan tersendiri oleh pemilik D2.
- scored_cases sudah difilter ke split yang diminta (test, dev, atau keduanya) sebelum
  masuk; irisan kasus dihitung apa adanya dari scored_cases.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from app.modules.runs.manual_session_rules import date_range_exceeds
from app.modules.runs.models import SessionPurpose, SessionStatus, SessionType, SplitFilter
from app.modules.runs.session_rules import is_report_eligible
from app.shared.exceptions import ConflictError

# Seleksi tidak sah: bukan soal satu sesi, melainkan kombinasi yang diminta (NB5).
NEED_AT_LEAST_TWO_PRODUCTS = "NEED_AT_LEAST_TWO_PRODUCTS"
DUPLICATE_PRODUCT = "DUPLICATE_PRODUCT"

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

_SPLITS_FOR_FILTER = {
    SplitFilter.DEV: frozenset({"dev"}),
    SplitFilter.TEST: frozenset({"test"}),
    SplitFilter.BOTH: frozenset({"dev", "test"}),
}

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
    """Identitas kasus; split_tag berasal dari snapshot body.sections.split_tag."""

    case_id: uuid.UUID
    case_version_id: uuid.UUID
    case_code: str
    split_tag: str


@dataclass(frozen=True)
class SessionFacts:
    """Satu sesi pengukuran, sebagaimana dibutuhkan aturan ini. Dibaca dari D2.

    scored_cases: kasus yang sudah punya skor, sudah difilter ke split yang diminta.
    snapshot_cases: SELURUH kasus yang masuk snapshot sesi, bukan suite keseluruhan.
    split_filter: filter saat snapshot dibuat; diperlukan untuk cek komposisi yang adil.
    judge_model_version: identitas model penilai yang terekam saat grading.
    Lihat kontrak adapter di docstring modul.
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
    judge_model_version: str | None
    suite_ids: frozenset[uuid.UUID]
    model_version: str | None
    repetitions: int
    answer_date_from: datetime | None
    answer_date_to: datetime | None
    scored_cases: tuple[CaseRef, ...]
    snapshot_cases: tuple[CaseRef, ...]
    split_filter: SplitFilter


@dataclass(frozen=True)
class Rejection:
    """Satu syarat perbandingan yang tidak terpenuhi, dengan alasannya (AC13)."""

    requirement: str
    message: str


@dataclass(frozen=True)
class ComparisonPlan:
    """Hasil aturan untuk satu permintaan laporan.

    comparable False berarti laporan tidak boleh menampilkan angka perbandingan: alasannya
    ada di selection_errors (seleksi tidak sah), ineligible (per sesi), rejections (per
    syarat), atau warning (irisan kosong). session_markers melekatkan penanda ke sesinya
    (AC4, AC5); markers hanya penanda tingkat perbandingan (AC3).
    """

    sessions: tuple[SessionFacts, ...]
    selection_errors: tuple[str, ...]
    ineligible: dict[uuid.UUID, tuple[str, ...]]
    rejections: tuple[Rejection, ...]
    markers: tuple[str, ...]
    session_markers: dict[uuid.UUID, tuple[str, ...]]
    shared_cases: tuple[CaseRef, ...]
    warning: str | None

    @property
    def comparable(self) -> bool:
        return (
            not self.selection_errors
            and not self.ineligible
            and not self.rejections
            and self.warning is None
        )


def selection_errors(sessions: Sequence[SessionFacts]) -> tuple[str, ...]:
    """Masalah pada kombinasi sesi yang dipilih, bukan pada satu sesi (NB5).

    Perbandingan butuh minimal dua produk, dan satu produk hanya boleh diwakili satu sesi
    dalam satu laporan (kontrak report-builder, UNIQUE report_id, product_id di D1).
    """
    galat: list[str] = []

    if len(sessions) < 2:
        galat.append(NEED_AT_LEAST_TWO_PRODUCTS)

    product_ids = [sesi.product_id for sesi in sessions]
    session_ids = [sesi.session_id for sesi in sessions]
    if len(set(product_ids)) != len(product_ids) or len(set(session_ids)) != len(session_ids):
        galat.append(DUPLICATE_PRODUCT)

    return tuple(galat)


def eligibility_problems(facts: SessionFacts) -> tuple[str, ...]:
    """Semua alasan sesi ini belum boleh jadi dasar laporan (AC2, AC4). Kosong = layak.

    Status layak diputuskan is_report_eligible (D4, PBI-12 AC7 dan AC9), jadi aturan
    status hanya hidup di satu tempat. Sesi manual ikut aturan yang sama: hanya Selesai
    (finalized) yang layak, Dinilai belum. rubric_version_id harus berasal dari grading
    run terakhir yang selesai (kontrak adapter); kosong berarti belum dinilai.
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
    """Penanda yang melekat pada satu sesi (AC4, AC5): Uji Coba dan rentang tanggal manual."""
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


def _conflicts_for_split(sessions: Sequence[SessionFacts], split: str) -> set[uuid.UUID]:
    konflik: set[uuid.UUID] = set()
    acuan: dict[uuid.UUID, uuid.UUID] | None = None

    for sesi in sessions:
        if split not in _SPLITS_FOR_FILTER[SplitFilter(sesi.split_filter)]:
            continue

        sekarang = {
            kasus.case_id: kasus.case_version_id
            for kasus in sesi.snapshot_cases
            if kasus.split_tag == split
        }

        if acuan is not None and sekarang != acuan:
            konflik.update(acuan.keys() ^ sekarang.keys())
            konflik.update(
                case_id
                for case_id in acuan.keys() & sekarang.keys()
                if acuan[case_id] != sekarang[case_id]
            )

        acuan = sekarang

    return konflik


def _snapshot_conflicts(sessions: Sequence[SessionFacts]) -> list[str]:
    """Konflik versi atau komposisi kasus pada split yang dibandingkan."""
    versi: dict[uuid.UUID, set[uuid.UUID]] = {}
    kode: dict[uuid.UUID, str] = {}

    for sesi in sessions:
        for kasus in sesi.snapshot_cases:
            if kasus.split_tag not in {"dev", "test"}:
                raise ValueError(f"Invalid case split tag: {kasus.split_tag!r}")

            if kasus.split_tag not in _SPLITS_FOR_FILTER[SplitFilter(sesi.split_filter)]:
                raise ValueError(f"Case split {kasus.split_tag!r} is outside session split filter")

            versi.setdefault(kasus.case_id, set()).add(kasus.case_version_id)
            kode.setdefault(kasus.case_id, kasus.case_code)

    konflik = {case_id for case_id, versions in versi.items() if len(versions) > 1}

    for split in ("dev", "test"):
        konflik.update(_conflicts_for_split(sessions, split))

    return sorted(kode[case_id] for case_id in konflik)


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

    # Model penilai (B2): berlaku untuk SETIAP sesi yang dibandingkan. Penilaian memakai
    # judge LLM lewat alur yang sama untuk percobaan otomatis maupun manual (D-grade AC9,
    # resolve_judge_id), jadi session_type MANUAL tidak berarti tanpa judge. Identitas judge
    # tiap sesi = id provider + versi model efektif saat grading; id provider bisa tetap sama
    # walau config modelnya diganti, jadi id saja tak cukup. Identitas tak diketahui (provider
    # atau versi model None) tidak bisa diverifikasi, jadi ditolak, bukan dianggap sama dengan
    # None lain. Adapter wajib memberi identitas andal untuk semua sesi.
    providers = {sesi.judge_provider_id for sesi in sessions}
    models = {sesi.judge_model_version for sesi in sessions}
    if None in providers or None in models or len(providers) > 1 or len(models) > 1:
        tolakan.append(
            Rejection(
                JUDGE_MODEL,
                "Sesi yang dipilih dinilai oleh model penilai yang berbeda atau tidak "
                "pasti, jadi skornya tidak setara.",
            )
        )

    if len({sesi.suite_ids for sesi in sessions}) > 1:
        tolakan.append(Rejection(SUITE_VERSION, "Sesi yang dipilih memakai suite yang berbeda."))
    elif konflik := _snapshot_conflicts(sessions):
        tolakan.append(
            Rejection(
                SUITE_VERSION,
                "Kasus pada snapshot suite berbeda versi atau komposisi dalam split yang sama: "
                f"{', '.join(konflik)}.",
            )
        )

    return tuple(tolakan)


def comparison_markers(sessions: Sequence[SessionFacts]) -> tuple[str, ...]:
    """Penanda tingkat perbandingan: beda versi model dan beda jumlah percobaan (AC3, AC5).

    Keduanya tidak menolak perbandingan, tetapi wajib tampil di laporan. Penanda tingkat
    sesi (Uji Coba, rentang manual) tidak di sini; itu melekat per sesi di ComparisonPlan.
    """
    penanda: list[str] = []

    if len({sesi.model_version for sesi in sessions}) > 1:
        penanda.append(MODEL_VERSION_DIFFERS)

    if len({sesi.repetitions for sesi in sessions}) > 1:
        penanda.append(REPETITIONS_DIFFER)

    return tuple(penanda)


def per_session_markers(sessions: Sequence[SessionFacts]) -> dict[uuid.UUID, tuple[str, ...]]:
    """Penanda tiap sesi, dipetakan ke session_id-nya (NB2, AC4, AC5).

    Hanya sesi yang benar-benar punya penanda yang masuk, supaya laporan bisa menunjuk
    sesi mana yang Uji Coba atau rentang manualnya lewat batas.
    """
    return {sesi.session_id: penanda for sesi in sessions if (penanda := session_markers(sesi))}


def shared_cases(sessions: Sequence[SessionFacts]) -> tuple[CaseRef, ...]:
    """Kasus yang dijawab semua sesi (AC1), diurutkan menurut kode kasus.

    Identitas kasus di sini adalah (case_id, case_version_id): kasus yang sama pada versi
    berbeda bukan kasus yang sama, sesuai AC1. Beda versi juga sudah ditolak lebih dulu
    oleh comparison_rejections (AC13), jadi dalam perbandingan yang sah kunci ini setara
    dengan case_id, tetapi tetap eksplisit supaya identitasnya benar.
    """
    if not sessions:
        return ()

    kunci_per_sesi = [
        {(kasus.case_id, kasus.case_version_id) for kasus in sesi.scored_cases} for sesi in sessions
    ]
    irisan = set.intersection(*kunci_per_sesi)

    pertama = {(kasus.case_id, kasus.case_version_id): kasus for kasus in sessions[0].scored_cases}
    return tuple(sorted((pertama[kunci] for kunci in irisan), key=lambda k: k.case_code))


def plan_comparison(sessions: Sequence[SessionFacts]) -> ComparisonPlan:
    """Aturan lengkap untuk satu permintaan laporan (AC1–AC5, AC13).

    Begitu seleksi tidak sah, ada sesi tidak layak, atau syarat gagal, irisan kasus tidak
    dihitung dan laporan tidak menampilkan angka: yang keluar hanya alasannya. Penanda
    tetap dihitung supaya laporan bisa menuliskannya walau perbandingan ditolak.
    """
    sessions = tuple(sessions)

    galat_seleksi = selection_errors(sessions)
    tidak_layak = {
        sesi.session_id: alasan for sesi in sessions if (alasan := eligibility_problems(sesi))
    }
    # Syarat perbandingan hanya diperiksa pada sesi yang layak. Sesi tak layak punya
    # rubric/judge None dan snapshot bisa sebagian (sesi dibatalkan/gagal), yang akan
    # memicu penolakan palsu RUBRIC_VERSION/JUDGE_MODEL/SUITE_VERSION padahal sebab
    # sebenarnya adalah ketidaklayakan, bukan beda rubrik/penilai/suite.
    layak = tuple(sesi for sesi in sessions if sesi.session_id not in tidak_layak)
    tolakan = comparison_rejections(layak)
    penanda = comparison_markers(sessions)
    penanda_sesi = per_session_markers(sessions)

    if galat_seleksi or tidak_layak or tolakan:
        return ComparisonPlan(
            sessions=sessions,
            selection_errors=galat_seleksi,
            ineligible=tidak_layak,
            rejections=tolakan,
            markers=penanda,
            session_markers=penanda_sesi,
            shared_cases=(),
            warning=None,
        )

    irisan = shared_cases(sessions)
    kosong = not irisan

    return ComparisonPlan(
        sessions=sessions,
        selection_errors=(),
        ineligible={},
        rejections=(),
        markers=penanda,
        session_markers=penanda_sesi,
        shared_cases=() if kosong else irisan,
        warning=NO_SHARED_CASES if kosong else None,
    )
