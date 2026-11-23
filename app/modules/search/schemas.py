import uuid
from typing import Literal

from pydantic import BaseModel


class SearchHit(BaseModel):
    id: uuid.UUID
    label: str
    archived: bool


class CaseSearchHit(SearchHit):
    case_code: str


SearchDestination = Literal["automatic_detail", "manual_detail", "grading_results"]


class SessionSearchHit(SearchHit):
    session_type: Literal["automatic", "manual"]
    destination: SearchDestination


class SearchResponse(BaseModel):
    suites: list[SearchHit]
    cases: list[CaseSearchHit]
    sessions: list[SessionSearchHit]
