"""Penerjemah perubahan baris jadi event audit, sesuai katalog D6a.

PBI-18 AC1. Satu tracker per tabel (pola Strategy). Listener ORM cukup
mencari tracker lewat nama tabel lalu memanggil created() atau
updated(), tanpa tahu aturan tiap entitas. Tabel baru cukup menambah
satu kelas di TRACKERS tanpa mengubah listener.

Tracker dikunci dengan nama tabel, bukan kelas model, supaya modul audit
tidak mengimpor models milik modul lain.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from app.modules.audit.models import AuditEntityType
from app.modules.audit.redaction import CREDENTIAL_ROTATED


@dataclass(frozen=True)
class Change:
    old: Any
    new: Any


@dataclass(frozen=True)
class AuditEvent:
    action: str
    entity_id: uuid.UUID
    case_id: uuid.UUID | None = None
    before: dict[str, Any] | None = None
    after: dict[str, Any] | None = None


class Tracker:
    """Perilaku bawaan: <prefix>.created dan <prefix>.updated berisi field yang berubah.

    Subkelas mengisi `special` untuk field yang punya event sendiri,
    misalnya status suite yang jadi suite.archived.
    """

    entity_type: AuditEntityType
    prefix: str
    created_action = "created"
    # Field yang dicatat saat baris dibuat.
    created_fields: tuple[str, ...] = ()
    # Field yang dipantau saat baris diubah. Field lain diabaikan.
    fields: tuple[str, ...] = ()
    special: frozenset[str] = frozenset()

    def case_id(self, row: Any) -> uuid.UUID | None:
        return None

    def created(self, row: Any) -> list[AuditEvent]:
        after = {field: _json(getattr(row, field)) for field in self.created_fields}
        return [self._event(row, self.created_action, after=after)]

    def updated(self, row: Any, changes: dict[str, Change]) -> list[AuditEvent]:
        events = [
            self._special_event(row, field, changes[field])
            for field in sorted(self.special)
            if field in changes
        ]
        rest = {field: change for field, change in changes.items() if field not in self.special}
        if rest:
            events.append(
                self._event(
                    row,
                    "updated",
                    before={field: _json(change.old) for field, change in rest.items()},
                    after={field: _json(change.new) for field, change in rest.items()},
                )
            )
        return events

    def _special_event(self, row: Any, field: str, change: Change) -> AuditEvent:
        raise NotImplementedError  # pragma: no cover, setiap subkelas dengan special wajib mengisi

    def _event(self, row: Any, verb: str, **values: Any) -> AuditEvent:
        return AuditEvent(
            action=f"{self.prefix}.{verb}", entity_id=row.id, case_id=self.case_id(row), **values
        )

    def _field_event(self, row: Any, verb: str, field: str, change: Change) -> AuditEvent:
        return self._event(
            row, verb, before={field: _json(change.old)}, after={field: _json(change.new)}
        )


class SuiteTracker(Tracker):
    entity_type = AuditEntityType.SUITE
    prefix = "suite"
    created_fields = ("name", "description", "status")
    fields = ("name", "description", "status", "deleted_at")
    special = frozenset({"status", "deleted_at"})

    def _special_event(self, row: Any, field: str, change: Change) -> AuditEvent:
        if field == "deleted_at":
            return self._event(row, "deleted", after={"deleted_at": _json(change.new)})
        verb = "archived" if _json(change.new) == "archived" else "unarchived"
        return self._field_event(row, verb, field, change)


class CaseTracker(Tracker):
    entity_type = AuditEntityType.CASE
    prefix = "case"
    fields = (
        "case_code",
        "title",
        "question",
        "category",
        "legal_refs",
        "answer_criteria",
        "traps",
        "split_tag",
        "status",
    )
    special = frozenset({"split_tag", "status"})
    _STATUS_VERB = {
        "in_review": "submitted",
        "approved": "approved",
        "needs_revision": "revision_requested",
    }

    def case_id(self, row: Any) -> uuid.UUID | None:
        return row.id

    def created(self, row: Any) -> list[AuditEvent]:
        return [
            self._event(
                row, "created", after={"case_code": row.case_code, "version_no": row.version}
            )
        ]

    def _special_event(self, row: Any, field: str, change: Change) -> AuditEvent:
        if field == "split_tag":
            event = self._field_event(row, "tag_changed", field, change)
            if (_json(change.old), _json(change.new)) == ("dev", "test"):
                event.after["warning"] = True
            return event
        verb = self._STATUS_VERB.get(_json(change.new), "status_changed")
        return self._field_event(row, verb, field, change)


class AiProductTracker(Tracker):
    entity_type = AuditEntityType.AI_PRODUCT
    prefix = "ai_product"
    created_action = "registered"
    # credential_encrypted sengaja tidak ada di sini (PBI-10 AC2).
    created_fields = (
        "name",
        "provider_type",
        "base_url",
        "model_name",
        "rate_limit_per_minute",
        "monthly_budget_idr",
        "is_active",
    )
    fields = (*created_fields, "credential_encrypted")
    special = frozenset({"credential_encrypted", "is_active"})

    def _special_event(self, row: Any, field: str, change: Change) -> AuditEvent:
        if field == "credential_encrypted":
            return self._event(row, "credential_rotated", after=dict(CREDENTIAL_ROTATED))
        verb = "activated" if change.new else "deactivated"
        return self._field_event(row, verb, field, change)


class UserTracker(Tracker):
    entity_type = AuditEntityType.USER
    prefix = "user"
    created_action = "added"
    created_fields = ("email", "role")
    # Nama dan zitadel_sub disinkronkan dari IdP saat login, bukan aksi pengguna.
    fields = ("role", "is_active")
    special = frozenset(fields)

    def _special_event(self, row: Any, field: str, change: Change) -> AuditEvent:
        if field == "role":
            return self._field_event(row, "role_changed", field, change)
        verb = "activated" if change.new else "deactivated"
        return self._field_event(row, verb, field, change)


TRACKERS: dict[str, Tracker] = {
    "suites": SuiteTracker(),
    "cases": CaseTracker(),
    "ai_products": AiProductTracker(),
    "users": UserTracker(),
}


def tracker_for(table_name: str) -> Tracker | None:
    return TRACKERS.get(table_name)


def _json(value: Any) -> Any:
    """Nilai kolom jadi nilai yang aman disimpan di JSONB."""
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, uuid.UUID | Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return value
