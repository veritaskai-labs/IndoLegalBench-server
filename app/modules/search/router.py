from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BeforeValidator
from sqlalchemy.orm import Session

from app.modules.auth.schemas import ErrorBody
from app.modules.search import service
from app.modules.search.schemas import SearchResponse
from app.shared.database import get_db
from app.shared.security import CurrentUser, get_current_user
from app.shared.text import strip_blank

router = APIRouter(tags=["search"])


@router.get(
    "/search",
    response_model=SearchResponse,
    summary="Cari suite, kasus, dan sesi pengukuran",
    responses={401: {"model": ErrorBody, "description": "Autentikasi diperlukan"}},
)
def search(
    q: Annotated[
        str,
        Query(min_length=1, max_length=200, description="Kata kunci pencarian"),
        BeforeValidator(strip_blank),
    ],
    response: Response,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> SearchResponse:
    response.headers["Cache-Control"] = "private, no-store"
    return service.search(db, q.casefold(), role=user.role)
