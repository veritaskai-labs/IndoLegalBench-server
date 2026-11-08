"""Allow failed judge calls on technical-disruption results in llm_judge_guard.

SCRUM-78 SCRUM-271, PBI-14 AC6.

A judge call that fails after it used tokens is billed by the provider, and the
attempt it belongs to ends as a technical disruption (judge_unavailable,
judge_incomplete). The guard rejected every judge row for such a result, so the
cost of these calls could not be saved and was missing from the monthly spend of
the judge product.

The guard now accepts a row with status failed for a technical-disruption
result. A succeeded row is still rejected there (there is no answer to judge),
criterion rows are still rejected (a failed call has none), and the rule that
rows are insert-only and only go into an open run is unchanged. Only the function
is replaced, no table or data changes, and the downgrade restores the previous
function.

Revision ID: c4e8a1d7f093
Revises: a7c3e91b4d52
"""

from alembic import op

revision = "c4e8a1d7f093"  # pragma: allowlist secret
down_revision = "a7c3e91b4d52"  # pragma: allowlist secret
branch_labels = None
depends_on = None

_GUARD_AFTER = """
    CREATE FUNCTION llm_judge_guard() RETURNS trigger AS $$
    DECLARE
        result_id uuid;
        judge_status text;
        is_disruption boolean;
        run_status text;
    BEGIN
        IF TG_OP <> 'INSERT' THEN
            RAISE EXCEPTION '% is insert-only, grade again with a new run', TG_TABLE_NAME
                USING ERRCODE = 'insufficient_privilege';
        END IF;
        IF TG_TABLE_NAME = 'llm_criterion_results' THEN
            SELECT grading_result_id, status::text INTO result_id, judge_status
                FROM llm_judge_results WHERE id = NEW.llm_judge_result_id;
            IF judge_status = 'failed' THEN
                RAISE EXCEPTION 'a failed judge call has no criterion results'
                    USING ERRCODE = 'check_violation';
            END IF;
            PERFORM 1 FROM reason_codes
                WHERE code = NEW.reason_code AND (NOT is_active OR grader_path <> 'llm');
            IF FOUND THEN
                RAISE EXCEPTION 'reason code % is not an active LLM reason', NEW.reason_code
                    USING ERRCODE = 'check_violation';
            END IF;
        ELSE
            result_id := NEW.grading_result_id;
            judge_status := NEW.status::text;
        END IF;
        SELECT g.is_technical_disruption, r.status::text INTO is_disruption, run_status
            FROM grading_results g JOIN grading_runs r ON r.id = g.grading_run_id
            WHERE g.id = result_id
            FOR SHARE OF r;
        IF is_disruption AND judge_status IS DISTINCT FROM 'failed' THEN
            RAISE EXCEPTION 'a technical-disruption attempt has no answer to judge'
                USING ERRCODE = 'check_violation';
        END IF;
        IF run_status IN ('finished', 'failed') THEN
            RAISE EXCEPTION 'grading run is closed'
                USING ERRCODE = 'insufficient_privilege';
        END IF;
        RETURN NEW;
    END;
    $$ LANGUAGE plpgsql
"""

_GUARD_BEFORE = """
    CREATE FUNCTION llm_judge_guard() RETURNS trigger AS $$
    DECLARE
        result_id uuid;
        judge_status text;
        is_disruption boolean;
        run_status text;
    BEGIN
        IF TG_OP <> 'INSERT' THEN
            RAISE EXCEPTION '% is insert-only, grade again with a new run', TG_TABLE_NAME
                USING ERRCODE = 'insufficient_privilege';
        END IF;
        IF TG_TABLE_NAME = 'llm_criterion_results' THEN
            SELECT grading_result_id, status::text INTO result_id, judge_status
                FROM llm_judge_results WHERE id = NEW.llm_judge_result_id;
            IF judge_status = 'failed' THEN
                RAISE EXCEPTION 'a failed judge call has no criterion results'
                    USING ERRCODE = 'check_violation';
            END IF;
            PERFORM 1 FROM reason_codes
                WHERE code = NEW.reason_code AND (NOT is_active OR grader_path <> 'llm');
            IF FOUND THEN
                RAISE EXCEPTION 'reason code % is not an active LLM reason', NEW.reason_code
                    USING ERRCODE = 'check_violation';
            END IF;
        ELSE
            result_id := NEW.grading_result_id;
        END IF;
        SELECT g.is_technical_disruption, r.status::text INTO is_disruption, run_status
            FROM grading_results g JOIN grading_runs r ON r.id = g.grading_run_id
            WHERE g.id = result_id
            FOR SHARE OF r;
        IF is_disruption THEN
            RAISE EXCEPTION 'a technical-disruption attempt has no answer to judge'
                USING ERRCODE = 'check_violation';
        END IF;
        IF run_status IN ('finished', 'failed') THEN
            RAISE EXCEPTION 'grading run is closed'
                USING ERRCODE = 'insufficient_privilege';
        END IF;
        RETURN NEW;
    END;
    $$ LANGUAGE plpgsql
"""


def upgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(_GUARD_AFTER.replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1))


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(_GUARD_BEFORE.replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1))
