"""Unduh berkas skor satu sesi pengukuran. PBI-16 SCRUM-83 SCRUM-327.

Alur satu unduhan:

1. Sesi harus layak menurut aturan D2 (AC6). Kodenya sama dengan pembuat laporan
   (eligibility_problems, SCRUM-301), ditambah kotak pertama D2: sesi arsip tidak layak
   untuk laporan, tren, maupun unduhan baru. Sesi tak layak ditolak 409 dengan SEMUA
   alasannya, supaya pengguna tahu apa yang harus dilakukan.
2. Isi berkas mengikuti peran (lihat visible_rows), lalu disusun score_csv (AC5).
3. Berkas yang diserahkan disimpan selamanya lewat port ScoreFileStore (AC7). Isi yang sama
   untuk sesi yang sama hanya disimpan sekali; isi yang berubah (misalnya setelah dinilai
   ulang) menjadi berkas baru, berkas lama tetap ada.

Modul ini murni: hanya mengenal port ScoreSource dan ScoreFileStore, tanpa database dan
tanpa HTTP. Jawaban asli tidak pernah dibaca di sini; retensinya dijaga trigger attempts.
"""

import hashlib
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from app.modules.cases.models import SplitTag
from app.modules.reports.access import DEV_SCORE_ROLES, visible_case_code
from app.modules.reports.comparison_rules import (
    SESSION_CANCELLED,
    SESSION_NOT_ACCEPTED_AS_IS,
    SESSION_NOT_FINISHED,
    SESSION_NOT_GRADED,
    SESSION_RUN_FAILED,
    eligibility_problems,
)
from app.modules.reports.score_csv import ScoreCsvRow, ScoreCsvSession, render_score_csv
from app.modules.reports.session_record import SessionRecord
from app.modules.runs.models import SessionPurpose
from app.shared.exceptions import ConflictError, NotFoundError
from app.shared.security import Role

# D2 kotak A: sesi arsip tidak bisa dipakai untuk laporan, tren, maupun unduhan baru.
SESSION_ARCHIVED = "SESSION_ARCHIVED"

# Kode ketidaklayakannya sama dengan laporan; kalimatnya untuk tombol Unduh skor (AC6).
_PESAN_UNDUH = {
    SESSION_ARCHIVED: "Sesi ini sudah diarsipkan, jadi berkas skornya tidak bisa diunduh.",
    SESSION_NOT_FINISHED: (
        "Sesi ini belum selesai, jadi berkas skornya belum bisa diunduh. "
        "Tunggu sampai sesi selesai."
    ),
    SESSION_CANCELLED: (
        "Sesi ini dibatalkan, jadi berkas skornya belum bisa diunduh. "
        "Lanjutkan sesi sampai selesai dulu."
    ),
    SESSION_RUN_FAILED: (
        "Proses run sesi ini gagal, jadi tidak ada skor yang bisa diunduh. Buat sesi baru."
    ),
    SESSION_NOT_ACCEPTED_AS_IS: (
        "Sesi ini selesai dengan kegagalan dan belum diterima apa adanya oleh Admin, "
        "jadi berkas skornya belum bisa diunduh."
    ),
    SESSION_NOT_GRADED: "Sesi ini belum dinilai, jadi berkas skornya belum bisa diunduh.",
}

_SEMUA_SPLIT = frozenset({SplitTag.DEV, SplitTag.TEST})
_HANYA_TEST = frozenset({SplitTag.TEST})


@dataclass(frozen=True)
class SessionScoreData:
    """Satu sesi sebagaimana dibutuhkan unduhan, dibaca adapter dari D2.

    record: fakta sesi dan kasus yang dinilai, dari pembaca yang sama dengan laporan.
    grading_run_id: grading run terakhir yang selesai, dicatat bersama berkasnya.
    rubric_version_no / case_version_nos: nomor versi yang terbaca manusia untuk catatan
    versi; kosong bila tidak diketahui.
    """

    record: SessionRecord
    archived: bool
    grading_run_id: uuid.UUID | None
    rubric_version_no: int | None
    case_version_nos: Mapping[uuid.UUID, int]


@dataclass(frozen=True)
class ScoreFile:
    session_id: uuid.UUID
    file_name: str
    content: bytes
    sha256: str


class ScoreSource(Protocol):
    def read(self, session_id: uuid.UUID) -> SessionScoreData | None: ...


class ScoreFileStore(Protocol):
    def keep(
        self, file: ScoreFile, *, grading_run_id: uuid.UUID | None, created_by: uuid.UUID
    ) -> ScoreFile:
        """Simpan berkas selamanya. Isi yang sudah tersimpan untuk sesi yang sama dipakai ulang."""
        ...


def score_file_name(session_id: uuid.UUID) -> str:
    """Satu sesi satu berkas; nama yang sama dengan yang dipakai halaman sesi."""
    return f"skor-{session_id}.csv"


def download_problems(data: SessionScoreData) -> tuple[str, ...]:
    """Semua alasan sesi ini belum boleh diunduh (AC6). Kosong = boleh."""
    alasan = list(eligibility_problems(data.record.facts))
    if data.archived:
        alasan.insert(0, SESSION_ARCHIVED)
    return tuple(alasan)


def visible_rows(
    record: SessionRecord, role: Role, case_version_nos: Mapping[uuid.UUID, int]
) -> list[ScoreCsvRow]:
    """Baris yang boleh dilihat peran ini, urut menurut kode kasus.

    Isi per peran masih menunggu keputusan SA. Sampai saat itu yang berlaku adalah aturan
    D9 yang sudah dipakai laporan, tertutup bawaan:
    - skor kasus dev hanya untuk Admin dan Developer AI (DEV_SCORE_ROLES), peran lain hanya
      mendapat kasus test;
    - kode kasus test disamarkan untuk Developer AI (visible_case_code).
    Keputusan SA nanti cukup mengubah fungsi ini.
    """
    boleh = _SEMUA_SPLIT if role in DEV_SCORE_ROLES else _HANYA_TEST
    kasus = sorted(
        (g for g in record.graded_cases if g.split in boleh),
        key=lambda g: (g.ref.case_code, str(g.ref.case_version_id)),
    )
    return [
        ScoreCsvRow(
            case_code=visible_case_code(role, g.ref.case_code, g.split),
            case_version=_teks(case_version_nos.get(g.ref.case_version_id)),
            det_score=g.det,
            llm_score=g.llm,
        )
        for g in kasus
    ]


def _teks(nilai: object | None) -> str:
    return "" if nilai is None else str(nilai)


def _catatan_sesi(data: SessionScoreData) -> ScoreCsvSession:
    fakta = data.record.facts
    return ScoreCsvSession(
        product_name=fakta.product_name,
        measurement_kind=data.record.measurement_kind,
        rubric_version=_teks(data.rubric_version_no),
        product_model_version=fakta.model_version or "",
        judge_model_version=fakta.judge_model_version or "",
        trial=SessionPurpose(fakta.purpose) is SessionPurpose.TRIAL,
    )


def _tolak(alasan: tuple[str, ...]) -> ConflictError:
    return ConflictError(
        _PESAN_UNDUH[alasan[0]],
        code=alasan[0],
        details={"reasons": [{"code": kode, "message": _PESAN_UNDUH[kode]} for kode in alasan]},
    )


class SessionScoreFileService:
    def __init__(self, source: ScoreSource, store: ScoreFileStore) -> None:
        self._source = source
        self._store = store

    def download(self, session_id: uuid.UUID, *, user_id: uuid.UUID, role: Role) -> ScoreFile:
        """Berkas skor satu sesi untuk pengguna ini, setelah disimpan (AC5 sampai AC7)."""
        data = self._source.read(session_id)
        if data is None:
            raise NotFoundError("Sesi pengukuran tidak ditemukan.")

        alasan = download_problems(data)
        if alasan:
            raise _tolak(alasan)

        isi = render_score_csv(
            _catatan_sesi(data), visible_rows(data.record, role, data.case_version_nos)
        )
        berkas = ScoreFile(
            session_id=session_id,
            file_name=score_file_name(session_id),
            content=isi,
            sha256=hashlib.sha256(isi).hexdigest(),
        )
        return self._store.keep(berkas, grading_run_id=data.grading_run_id, created_by=user_id)
