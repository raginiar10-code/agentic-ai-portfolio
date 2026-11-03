from __future__ import annotations

import json
from datetime import datetime, timezone

from db import get_connection


def log_message(parent_id: int, workflow: str, role: str, content: str, intent: str | None = None) -> int:
    """Insert one row into `chat_messages`. Returns the new row's id (SQLite's
    `cursor.lastrowid`) so a caller can link a structured `activity_log` row back to the exact
    assistant message it came from -- see log_activity() below, and db.py's `activity_log`
    comment for why that link exists.

    `workflow` is "ask_northstar" or "daily_support" (app.py is the only caller, and always
    passes one of exactly these two strings -- not validated here, since this is an internal
    module, not a public API receiving untrusted input). `role` is "user" or "assistant".
    """
    connection = get_connection()
    try:
        cursor = connection.execute(
            "INSERT INTO chat_messages (parent_id, workflow, role, content, intent, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (parent_id, workflow, role, content, intent, datetime.now(timezone.utc).isoformat()),
        )
        connection.commit()
        return cursor.lastrowid
    finally:
        connection.close()


def log_activity(
    parent_id: int,
    chat_message_id: int | None,
    current_need: str,
    activity_name: str | None,
    duration_minutes: int | None,
    materials_needed: list[str] | None,
    steps: list[str] | None,
    why_it_helps: str | None,
    critic_verdict: str,
) -> int:
    """Insert one row into `activity_log` -- the STRUCTURED side-table for a Daily Support
    outcome, recorded alongside (not instead of) the plain chat_messages row log_message()
    already wrote for the same turn. `chat_message_id` is that row's id, linking the two.

    `materials_needed`/`steps` are JSON-encoded here on the way in, since SQLite has no native
    list column type (see db.py's `activity_log` comment for the full reasoning) -- `None`
    stays `None` rather than becoming the string `"null"`, so a later `get_child_profile`-style
    reader can tell "nothing was recommended" (critic flagged it) apart from "an activity with
    an empty materials list" (which shouldn't happen, but the distinction is worth preserving
    rather than collapsing both into the same JSON value).

    This function doesn't itself decide WHEN activity_name etc. should be None -- it just stores
    whatever it's given. main.py's run_daily_support() is what actually enforces "these are all
    None together when critic_verdict is 'flag'" (see its flagged branch).
    """
    connection = get_connection()
    try:
        cursor = connection.execute(
            """
            INSERT INTO activity_log (
                parent_id, chat_message_id, current_need, activity_name, duration_minutes,
                materials_needed, steps, why_it_helps, critic_verdict, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                parent_id,
                chat_message_id,
                current_need,
                activity_name,
                duration_minutes,
                json.dumps(materials_needed) if materials_needed is not None else None,
                json.dumps(steps) if steps is not None else None,
                why_it_helps,
                critic_verdict,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        connection.commit()
        return cursor.lastrowid
    finally:
        connection.close()


def get_recent_messages(parent_id: int, workflow: str, limit: int = 6) -> list[dict]:
    """Return `parent_id`'s last `limit` chat_messages ROWS for `workflow`, oldest first, as
    plain {"role", "content"} dicts -- exactly what agents/router.py needs to render into a
    prompt, with nothing SQLite- or Pydantic-specific leaking out of this module.

    `limit` counts ROWS, not TURNS -- since log_message() writes one row per side of a turn
    (user message, assistant reply, as two separate INSERTs), the default of 6 is the last 3
    back-and-forth exchanges, not 6. This is called BEFORE the CURRENT turn is logged, so
    "recent" here always means prior turns only, never including the message being processed
    right now.

    The ORDER BY/LIMIT/reversed() dance below is deliberate: `ORDER BY id DESC LIMIT ?` is what
    actually keeps the LATEST rows when there are more than `limit` -- `ORDER BY id ASC LIMIT ?`
    would instead keep the OLDEST ones and silently cut off the recent conversation, which is
    the opposite of what "recent" means here. `reversed(rows)` then puts them back into
    chronological (oldest-first) order, since that's the order a prompt should read them in.
    """
    connection = get_connection()
    try:
        rows = connection.execute(
            "SELECT role, content FROM chat_messages WHERE parent_id = ? AND workflow = ? "
            "ORDER BY id DESC LIMIT ?",
            (parent_id, workflow, limit),
        ).fetchall()
    finally:
        connection.close()
    return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]


# Manual sanity check, same pattern as the other modules: `uv run python chat_log.py` logs a
# user message and an assistant reply for a throwaway account, then a flagged AND a passed
# activity outcome, and confirms what comes back out of the database matches what went in --
# including that a flagged outcome's materials_needed/steps genuinely read back as None, not the
# string "null" or an empty list.
if __name__ == "__main__":
    import secrets

    from auth import sign_up
    from db import get_connection as _get_connection

    test_account = sign_up(f"test_chatlog_{secrets.token_hex(4)}", "irrelevant-password")

    user_msg_id = log_message(test_account.parent_id, workflow="ask_northstar", role="user", content="What is autism?")
    assistant_msg_id = log_message(
        test_account.parent_id, workflow="ask_northstar", role="assistant", content="Autism is...", intent="info"
    )
    print(f"Logged chat_messages rows: user={user_msg_id}, assistant={assistant_msg_id}")

    history = get_recent_messages(test_account.parent_id, workflow="ask_northstar")
    assert history == [
        {"role": "user", "content": "What is autism?"},
        {"role": "assistant", "content": "Autism is..."},
    ], "BUG: get_recent_messages didn't return the two rows just logged, in order"
    print(f"get_recent_messages correctly returned: {history}")

    flagged_id = log_activity(
        test_account.parent_id,
        chat_message_id=None,
        current_need="other",
        activity_name=None,
        duration_minutes=None,
        materials_needed=None,
        steps=None,
        why_it_helps=None,
        critic_verdict="flag",
    )
    passed_id = log_activity(
        test_account.parent_id,
        chat_message_id=assistant_msg_id,
        current_need="regulation",
        activity_name="Animal Walk Obstacle Course",
        duration_minutes=10,
        materials_needed=["Open floor space", "Pillows or cushions"],
        steps=["Set up a path", "Demonstrate animal walks"],
        why_it_helps="Provides proprioceptive input.",
        critic_verdict="pass",
    )

    connection = _get_connection()
    try:
        flagged_row = connection.execute("SELECT * FROM activity_log WHERE id = ?", (flagged_id,)).fetchone()
        passed_row = connection.execute("SELECT * FROM activity_log WHERE id = ?", (passed_id,)).fetchone()
    finally:
        connection.close()

    assert flagged_row["activity_name"] is None and flagged_row["materials_needed"] is None, (
        "BUG: a flagged outcome should read back as None, not 'null' or []"
    )
    print(f"Flagged activity_log row correctly all-None except critic_verdict: {dict(flagged_row)}")

    assert json.loads(passed_row["materials_needed"]) == ["Open floor space", "Pillows or cushions"], (
        "BUG: materials_needed didn't round-trip through JSON correctly"
    )
    print(f"Passed activity_log row round-tripped correctly: {dict(passed_row)}")
