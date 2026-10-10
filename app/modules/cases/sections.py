"""The eight sections SCRUM-137 compares.

Option 1, chosen for this ticket. A changed section is one of:

- identity.title
- identity.question
- category
- case_code
- split_tag
- legal_refs
- answer_criteria
- traps

`category` is the optional case-identity label, for example "ketenagakerjaan".
It is not a citation field. `legal_refs` stays one section so a later
free-text citation still highlights that one name. `completeness` is stored
on the version and is not a section.

`case_code` is stored on each `case_versions` row so history and compare
report the code that version had. After a version is approved the live
case row cannot be renamed (`CASE_CODE_LOCKED`), so a new draft keeps the
same code unless product later unlocks per-version renames. The suite
snapshot still copies that version-scoped code so the frozen body does not
read the live case row.
"""

from typing import Any

# Order is the order of `changed` in the API.
SECTIONS = (
    "identity.title",
    "identity.question",
    "category",
    "case_code",
    "split_tag",
    "legal_refs",
    "answer_criteria",
    "traps",
)


def sections_from(*, case_code: str, split_tag: str, content: dict | None) -> dict[str, Any]:
    """Map one version onto the eight section names."""
    stored = content or {}
    return {
        "identity.title": stored.get("title") or "",
        "identity.question": stored.get("question") or "",
        "category": stored.get("category"),
        "case_code": case_code,
        "split_tag": split_tag,
        "legal_refs": stored.get("legal_refs") or [],
        "answer_criteria": stored.get("answer_criteria") or {},
        "traps": stored.get("traps") or [],
    }


def changed_sections(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    """Section names whose values differ, in SECTIONS order."""
    return [name for name in SECTIONS if before.get(name) != after.get(name)]


def snapshot_body(
    *,
    case_code: str,
    version_no: int,
    status: str,
    split_tag: str,
    content: dict | None,
) -> dict[str, Any]:
    """Frozen case body stored on a snapshot item.

    The copy includes case_code and split_tag plus the content sections.
    Completeness is left out: it is derived and is not one of the sections.
    """
    return {
        "version_no": version_no,
        "status": status,
        "sections": sections_from(case_code=case_code, split_tag=split_tag, content=content),
    }
