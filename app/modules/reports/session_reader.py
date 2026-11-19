"""Pembaca data sesi dari database untuk pembuat laporan. PBI-15 SCRUM-79 SCRUM-313.

Adapter D2 yang dijanjikan di docstring comparison_rules: mengisi SessionFacts menurut
kontraknya, ditambah kasus yang sudah dinilai dan jumlah upaya pemanggilan.
Memenuhi port SessionRecordReader.
"""

import uuid
from collections.abc import Sequence

from sqlalchemy.orm import Session

from app.modules.reports.session_record import SessionRecord


class SqlSessionRecordReader:
    def __init__(self, db: Session) -> None:
        self._db = db

    def read(self, session_ids: Sequence[uuid.UUID]) -> list[SessionRecord]:
        raise NotImplementedError
