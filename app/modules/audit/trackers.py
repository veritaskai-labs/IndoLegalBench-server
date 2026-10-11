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

    def _soft_deleted(self, row: Any, change: Change) -> AuditEvent:
        """<prefix>.deleted untuk soft delete lewat deleted_at (D6a)."""
        return self._event(row, "deleted", after={"deleted_at": _json(change.new)})

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
            return self._soft_deleted(row, change)
        verb = "archived" if _json(change.new) == "archived" else "unarchived"
        return self._field_event(row, verb, field, change)


class CaseTracker(Tracker):
    """Identity of a case. The wording lives on case_versions."""

    entity_type = AuditEntityType.CASE
    prefix = "case"
    fields = ("case_code",)

    def case_id(self, row: Any) -> uuid.UUID | None:
        return row.id

    def created(self, row: Any) -> list[AuditEvent]:
        return [self._event(row, "created", after={"case_code": row.case_code})]


class CaseVersionTracker(Tracker):
    """Wording of a case. Status, tag, and content changes are case events."""

    entity_type = AuditEntityType.CASE
    prefix = "case"
    fields = ("status", "split_tag", "content")
    special = frozenset({"split_tag", "status"})
    _STATUS_VERB = {
        "approved": "approved",
        "needs_revision": "revision_requested",
    }

    def case_id(self, row: Any) -> uuid.UUID | None:
        return row.case_id

    def created(self, row: Any) -> list[AuditEvent]:
        return [self._event(row, "version_created", after={"version_no": row.version_no})]

    def updated(self, row: Any, changes: dict[str, Change]) -> list[AuditEvent]:
        content = changes.get("content")
        rest = {
            field: change
            for field, change in changes.items()
            if field != "content" and not _masuk_review(field, change)
        }
        events = super().updated(row, rest)
        if content is not None:
            before, after = _content_diff(content)
            if before or after:
                events.append(self._event(row, "updated", before=before, after=after))
        return events

    def _special_event(self, row: Any, field: str, change: Change) -> AuditEvent:
        if field == "split_tag":
            lama, baru = _json(change.old), _json(change.new)
            # D6a: dev→test diberi tanda peringatan. Dict dibangun lengkap di
            # sini karena AuditEvent frozen dan tidak boleh diubah setelah jadi.
            after = (
                {field: baru, "warning": True} if (lama, baru) == ("dev", "test") else {field: baru}
            )
            return self._event(row, "tag_changed", before={field: lama}, after=after)
        verb = self._STATUS_VERB.get(_json(change.new), "status_changed")
        return self._field_event(row, verb, field, change)


class ReviewRoundTracker(Tracker):
    """Round baru berarti kasus diajukan (PBI-6 AC1, SCRUM-143).

    case.submitted ditulis di sini, bukan dari status versi, supaya
    round_no ikut tercatat sesuai D6a. Keputusan round tidak dicatat
    terpisah karena status versi sudah menghasilkan case.approved atau
    case.revision_requested.
    """

    entity_type = AuditEntityType.CASE
    prefix = "case"

    def case_id(self, row: Any) -> uuid.UUID | None:
        return row.case_id

    def created(self, row: Any) -> list[AuditEvent]:
        return [
            AuditEvent(
                action="case.submitted",
                entity_id=row.case_version_id,
                case_id=row.case_id,
                after={"status": "in_review", "round_no": row.round_no},
            )
        ]


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
    fields = (*created_fields, "credential_encrypted", "deleted_at")
    special = frozenset({"credential_encrypted", "is_active", "deleted_at"})

    def _special_event(self, row: Any, field: str, change: Change) -> AuditEvent:
        if field == "credential_encrypted":
            return self._event(row, "credential_rotated", after=dict(CREDENTIAL_ROTATED))
        if field == "deleted_at":
            return self._soft_deleted(row, change)
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


def _masuk_review(field: str, change: Change) -> bool:
    """Versi yang masuk in_review dicatat ReviewRoundTracker, bukan di sini."""
    return field == "status" and _json(change.new) == "in_review"


def _content_diff(change: Change) -> tuple[dict[str, Any], dict[str, Any]]:
    """Keys inside the version body that actually changed."""
    old = change.old if isinstance(change.old, dict) else {}
    new = change.new if isinstance(change.new, dict) else {}
    before: dict[str, Any] = {}
    after: dict[str, Any] = {}
    for key in sorted(set(old) | set(new)):
        if old.get(key) == new.get(key):
            continue
        before[key] = _json(old.get(key))
        after[key] = _json(new.get(key))
    return before, after


TRACKERS: dict[str, Tracker] = {
    "suites": SuiteTracker(),
    "cases": CaseTracker(),
    "case_versions": CaseVersionTracker(),
    "review_rounds": ReviewRoundTracker(),
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
