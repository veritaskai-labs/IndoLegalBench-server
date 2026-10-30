"""Pytest-only RBAC probes. Not mounted on the production app.

SCRUM-91 ships require_role; PBI-2+ attach it to their own routers.
These paths exist so auth tests can assert 401/403 without touching /suites.
"""

from fastapi import APIRouter, Depends

from app.shared.security import CurrentUser, Role, require_role

probe = APIRouter(prefix="/_test/rbac", include_in_schema=False)


@probe.get("/contributor")
def contributor_only(
    _user: CurrentUser = Depends(require_role(Role.CONTRIBUTOR)),
) -> dict[str, bool]:
    return {"ok": True}


@probe.get("/admin")
def admin_only(_user: CurrentUser = Depends(require_role(Role.ADMIN))) -> dict[str, bool]:
    return {"ok": True}
