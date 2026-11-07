"""SCRUM-231: PostgreSQL integration tests for manual answer history.

Set TEST_POSTGRES_URL to a dedicated, fully migrated PostgreSQL test database.
Each test runs in a transaction that is rolled back afterwards.
"""

import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, text

from tests.measurement_pg import (
    INSUFFICIENT_PRIVILEGE,
    buat_admin,
    buat_item_snapshot,
    buat_produk,
    buat_sesi,
    sqlstate_penolakan,
)

POSTGRES_URL = os.environ.get("TEST_POSTGRES_URL", "")

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(not POSTGRES_URL, reason="TEST_POSTGRES_URL tidak diisi"),
]


@pytest.fixture
def conn():
    engine = create_engine(POSTGRES_URL)
    try:
        with engine.connect() as connection:
            transaction = connection.begin()
            try:
                yield connection
            finally:
                transaction.rollback()
    finally:
        engine.dispose()


def _attempt(conn, *, source="manual"):
    actor = buat_admin(conn)
    product_id = buat_produk(conn, actor)
    session_id = buat_sesi(conn, product_id, actor, session_type=source)
    snapshot_item_id = buat_item_snapshot(conn, session_id, actor)
    attempt_id = uuid.uuid4()

    conn.execute(
        text(
            "INSERT INTO attempts "
            "(id, session_id, snapshot_item_id, repetition_no, source) "
            "VALUES (:id, :session_id, :item_id, 1, "
            "CAST(:source AS attempt_source_enum))"
        ),
        {
            "id": attempt_id,
            "session_id": session_id,
            "item_id": snapshot_item_id,
            "source": source,
        },
    )

    return attempt_id, actor


def _fill_manual(conn, attempt_id, actor, answer="**jawaban asli**"):
    when = datetime(2026, 11, 5, 5, 0, tzinfo=UTC)

    conn.execute(
        text(
            "UPDATE attempts SET answer_text = :answer, filled_by = :actor, "
            "filled_at = :when, answered_at_platform = :when "
            "WHERE id = :id"
        ),
        {"id": attempt_id, "actor": actor, "when": when, "answer": answer},
    )

    return when


def _archive_and_overwrite(conn, attempt_id, actor, *, new_answer, new_time):
    old = conn.execute(
        text(
            "SELECT answer_text, filled_by, filled_at, answered_at_platform, "
            "platform_refused, manual_note FROM attempts WHERE id = :id"
        ),
        {"id": attempt_id},
    ).one()

    conn.execute(
        text(
            "INSERT INTO answer_history "
            "(id, attempt_id, answer_md, filled_by, filled_at, obtained_at, "
            "platform_refused, note, overwritten_by, overwritten_at) "
            "VALUES (:history_id, :attempt_id, :answer_md, :filled_by, "
            ":filled_at, :obtained_at, :platform_refused, :note, :actor, :new_time)"
        ),
        {
            "history_id": uuid.uuid4(),
            "attempt_id": attempt_id,
            "answer_md": old.answer_text,
            "filled_by": old.filled_by,
            "filled_at": old.filled_at,
            "obtained_at": old.answered_at_platform,
            "platform_refused": old.platform_refused,
            "note": old.manual_note,
            "actor": actor,
            "new_time": new_time,
        },
    )

    conn.execute(
        text(
            "UPDATE attempts SET answer_text = :answer, filled_at = :when, "
            "filled_by = :actor WHERE id = :id"
        ),
        {"id": attempt_id, "answer": new_answer, "when": new_time, "actor": actor},
    )


def test_manual_first_save_without_history_is_allowed(conn):
    attempt_id, actor = _attempt(conn)
    _fill_manual(conn, attempt_id, actor)

    answer = conn.scalar(
        text("SELECT answer_text FROM attempts WHERE id = :id"),
        {"id": attempt_id},
    )

    history_count = conn.scalar(
        text("SELECT COUNT(*) FROM answer_history WHERE attempt_id = :id"),
        {"id": attempt_id},
    )

    assert answer == "**jawaban asli**"
    assert history_count == 0


def test_manual_overwrite_without_history_is_rejected(conn):
    attempt_id, actor = _attempt(conn)
    _fill_manual(conn, attempt_id, actor)

    sqlstate = sqlstate_penolakan(
        conn,
        "UPDATE attempts SET answer_text = 'tampered' WHERE id = :id",
        {"id": attempt_id},
    )

    assert sqlstate == INSUFFICIENT_PRIVILEGE


def test_manual_overwrite_with_wrong_history_is_rejected(conn):
    attempt_id, actor = _attempt(conn)
    original_at = _fill_manual(conn, attempt_id, actor)
    new_at = original_at + timedelta(minutes=1)

    conn.execute(
        text(
            "INSERT INTO answer_history "
            "(id, attempt_id, answer_md, filled_by, filled_at, obtained_at, "
            "platform_refused, overwritten_by, overwritten_at) "
            "VALUES (:id, :attempt_id, 'wrong old text', :actor, :old, :old, "
            "false, :actor, :new)"
        ),
        {
            "id": uuid.uuid4(),
            "attempt_id": attempt_id,
            "actor": actor,
            "old": original_at,
            "new": new_at,
        },
    )

    sqlstate = sqlstate_penolakan(
        conn,
        "UPDATE attempts SET answer_text = 'new', filled_at = :new WHERE id = :id",
        {"id": attempt_id, "new": new_at},
    )

    assert sqlstate == INSUFFICIENT_PRIVILEGE


def test_manual_overwrites_keep_each_previous_version(conn):
    attempt_id, actor = _attempt(conn)
    original_at = _fill_manual(conn, attempt_id, actor)

    _archive_and_overwrite(
        conn,
        attempt_id,
        actor,
        new_answer="jawaban kedua",
        new_time=original_at + timedelta(minutes=1),
    )

    _archive_and_overwrite(
        conn,
        attempt_id,
        actor,
        new_answer="jawaban ketiga",
        new_time=original_at + timedelta(minutes=2),
    )

    rows = conn.execute(
        text(
            "SELECT answer_md, overwritten_by FROM answer_history "
            "WHERE attempt_id = :id ORDER BY overwritten_at"
        ),
        {"id": attempt_id},
    ).all()

    assert [row.answer_md for row in rows] == [
        "**jawaban asli**",
        "jawaban kedua",
    ]
    assert all(row.overwritten_by == actor for row in rows)

    assert (
        conn.scalar(
            text("SELECT answer_text FROM attempts WHERE id = :id"),
            {"id": attempt_id},
        )
        == "jawaban ketiga"
    )


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE answer_history SET answer_md = 'tampered' WHERE attempt_id = :id",
        "DELETE FROM answer_history WHERE attempt_id = :id",
        "TRUNCATE answer_history",
    ],
    ids=["update", "delete", "truncate"],
)
def test_answer_history_is_append_only(conn, sql):
    attempt_id, actor = _attempt(conn)
    original_at = _fill_manual(conn, attempt_id, actor)

    _archive_and_overwrite(
        conn,
        attempt_id,
        actor,
        new_answer="baru",
        new_time=original_at + timedelta(minutes=1),
    )

    assert (
        sqlstate_penolakan(
            conn,
            sql,
            {"id": attempt_id},
        )
        == INSUFFICIENT_PRIVILEGE
    )


def test_automatic_answer_remains_immutable(conn):
    attempt_id, _ = _attempt(conn, source="automatic")

    conn.execute(
        text("UPDATE attempts SET status = 'running' WHERE id = :id"),
        {"id": attempt_id},
    )

    conn.execute(
        text(
            "UPDATE attempts SET answer_text = 'automatic result', "
            "status = 'succeeded' WHERE id = :id"
        ),
        {"id": attempt_id},
    )

    assert (
        sqlstate_penolakan(
            conn,
            "UPDATE attempts SET answer_text = 'changed' WHERE id = :id",
            {"id": attempt_id},
        )
        == INSUFFICIENT_PRIVILEGE
    )


def test_attempt_source_cannot_change(conn):
    attempt_id, _ = _attempt(conn, source="manual")

    assert (
        sqlstate_penolakan(
            conn,
            "UPDATE attempts SET source = 'automatic' WHERE id = :id",
            {"id": attempt_id},
        )
        == INSUFFICIENT_PRIVILEGE
    )
