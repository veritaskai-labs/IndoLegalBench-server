from sqlalchemy.orm import Session

from app.modules.cases import service as cases
from app.modules.runs import service as runs
from app.modules.search.schemas import CaseSearchHit, SearchHit, SearchResponse, SessionSearchHit
from app.modules.suites import service as suites
from app.shared.security import Role

_RESULTS_PER_GROUP = 20


def search(db: Session, query: str, *, role: Role) -> SearchResponse:
    # Viewer destinations are pending the product decision for SCRUM-351.
    if role not in {Role.ADMIN, Role.CONTRIBUTOR, Role.DEVELOPER_AI}:
        return SearchResponse(suites=[], cases=[], sessions=[])

    catalog = suites.search_catalog(db)
    suite_hits = []
    for suite_id, label, archived in catalog:
        if query in label.casefold():
            suite_hits.append(SearchHit(id=suite_id, label=label, archived=archived))
            if len(suite_hits) == _RESULTS_PER_GROUP:
                break

    suite_archives = {suite_id: archived for suite_id, _, archived in catalog}
    case_hits = [
        CaseSearchHit(id=case_id, label=label, case_code=code, archived=archived)
        for case_id, label, code, archived in cases.search_cases(
            db,
            query,
            suite_archives,
            approved_dev_only=role == Role.DEVELOPER_AI,
            limit=_RESULTS_PER_GROUP,
        )
    ]
    session_hits = [
        SessionSearchHit(
            id=session_id,
            label=label,
            archived=archived,
            session_type=kind,
            destination=destination,
        )
        for session_id, label, archived, kind, destination in runs.search_sessions(
            db, query, role, limit=_RESULTS_PER_GROUP
        )
    ]
    return SearchResponse(suites=suite_hits, cases=case_hits, sessions=session_hits)
