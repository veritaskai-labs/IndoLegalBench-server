"""Daftar sesi pengukuran di halaman Eksekusi. PBI-11 AC10, SCRUM-210.

Sesi yang diarsipkan tidak tampil. Nama produk dan pembuat datang dari modul
providers dan auth lewat NameLookup (adapters.py), karena providers sudah
mengimpor runs.service dan impor balik dari sini akan melingkar.

File terpisah dari service.py supaya tidak bertumpuk dengan perubahan SCRUM-207
dan SCRUM-215 di file yang sama.
"""

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from app.modules.runs.case_progress import case_progress
from app.modules.runs.models import SessionStatus, SessionType
from app.modules.runs.schemas import SessionListItem, SessionProgressRead
from app.modules.runs.session_rules import progress


@dataclass(frozen=True)
class SessionRow:
    """Satu sesi (aktif atau arsip) beserta hitungan dari snapshot dan attempts."""

    id: uuid.UUID
    label: str
    created_at: datetime
    created_by: uuid.UUID
    product_id: uuid.UUID
    repetitions: int
    status: SessionStatus
    # None selama sesi antre: snapshot baru dibuat saat sesi mulai (SCRUM-207).
    case_count: int | None
    attempt_counts: Mapping[str, int]
    # PBI-22 AC9 (SCRUM-233): label manual terbawa ke daftar sesi. Wajib (review #121):
    # bawaan automatic akan diam-diam melabeli sesi manual dari pembuat baris yang lupa.
    session_type: SessionType
    # Kasus yang semua pengulangannya selesai, dari repository.case_completion (SCRUM-265).
    completed_cases: int
    total_cases: int


class SessionListStore(Protocol):
    def active_sessions(self, *, session_type: SessionType | None = None) -> list[SessionRow]: ...

    def archived_sessions(self, *, session_type: SessionType | None = None) -> list[SessionRow]: ...

    def latest_active(self) -> SessionRow | None: ...


class NameLookup(Protocol):
    def product_names(self, product_ids: set[uuid.UUID]) -> Mapping[uuid.UUID, str]: ...

    def user_names(self, user_ids: set[uuid.UUID]) -> Mapping[uuid.UUID, str]: ...


class SessionListService:
    def __init__(self, sessions: SessionListStore, names: NameLookup) -> None:
        self._sessions = sessions
        self._names = names

    def list(
        self, *, archived: bool = False, session_type: SessionType | None = None
    ) -> list[SessionListItem]:
        """Daftar utama, atau daftar arsip bila archived. Nama dibaca sekali untuk semua baris."""
        if session_type is not None:
            rows = (
                self._sessions.archived_sessions(session_type=session_type)
                if archived
                else self._sessions.active_sessions(session_type=session_type)
            )
        else:
            rows = (
                self._sessions.archived_sessions() if archived else self._sessions.active_sessions()
            )
        products = self._names.product_names({row.product_id for row in rows})
        creators = self._names.user_names({row.created_by for row in rows})
        return [to_list_item(row, products, creators) for row in rows]

    def latest(self) -> SessionListItem | None:
        """Satu baris daftar untuk sesi terbaru, atau None bila belum ada."""
        row = self._sessions.latest_active()
        if row is None:
            return None
        return to_list_item(
            row,
            self._names.product_names({row.product_id}),
            self._names.user_names({row.created_by}),
        )


def to_list_item(
    row: SessionRow,
    products: Mapping[uuid.UUID, str],
    creators: Mapping[uuid.UUID, str],
) -> SessionListItem:
    """Kolom daftar untuk satu sesi, dipakai juga sebagai kepala detail sesi."""
    hasil = progress(row.attempt_counts)
    kasus = case_progress(completed_cases=row.completed_cases, total_cases=row.total_cases)
    return SessionListItem(
        id=row.id,
        label=row.label,
        created_at=row.created_at,
        created_by=row.created_by,
        created_by_name=creators.get(row.created_by),
        product_id=row.product_id,
        product_name=products.get(row.product_id),
        case_count=row.case_count,
        repetitions=row.repetitions,
        status=row.status,
        session_type=row.session_type,
        progress=SessionProgressRead(
            completed=hasil.completed,
            total=hasil.total,
            completed_cases=kasus.completed_cases,
            total_cases=kasus.total_cases,
            percent_cases_complete=kasus.percent_cases_complete,
        ),
    )
