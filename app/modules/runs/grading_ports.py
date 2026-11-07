"""Grading read contract shared by run score reports and SQL adapters."""

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol


@dataclass(frozen=True)
class GradeScoreSnapshot:
    run_id: uuid.UUID | None
    rubric_version_id: uuid.UUID | None
    deterministic: Mapping[uuid.UUID, Decimal | None]
    quality: Mapping[uuid.UUID, Decimal | None]
    format: Mapping[uuid.UUID, Decimal | None]


class GradingReaderPort(Protocol):
    """Read grades without importing the database grading models."""

    def latest(self, session_id: uuid.UUID) -> GradeScoreSnapshot: ...
