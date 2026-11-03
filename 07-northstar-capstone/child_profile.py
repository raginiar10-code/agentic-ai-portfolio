from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from db import get_connection


@dataclass
class ChildProfile:
    """The persisted version of what the sidebar's Condition/Age-range fields used to hold only
    in st.session_state (gone the moment the tab closed). Deliberately just these two fields --
    `current_need` is NOT part of this, on purpose: it's a per-request thing the parent picks
    fresh every time (calm down / something fun / general idea), not a standing fact about the
    child the way condition and age range are. See db.py's `children` table comment for the
    same reasoning spelled out at the schema level.
    """

    condition: str
    age_range: str


def get_child_profile(parent_id: int) -> ChildProfile | None:
    """Return `parent_id`'s saved child profile, or None if they've never saved one yet (a
    brand-new account, or one that skipped straight past the sidebar without filling it in).
    `None` here is a real, valid state -- callers must handle it, not assume a row always exists.
    """
    connection = get_connection()
    try:
        row = connection.execute(
            "SELECT condition, age_range FROM children WHERE parent_id = ?", (parent_id,)
        ).fetchone()
    finally:
        connection.close()
    if row is None:
        return None
    return ChildProfile(condition=row["condition"], age_range=row["age_range"])


def save_child_profile(parent_id: int, condition: str, age_range: str) -> ChildProfile:
    """Create or update `parent_id`'s child profile -- a single function for both cases, not two
    separate create/update functions, because the caller (app.py's sidebar form) never knows in
    advance which one applies: first save for a new account vs. an edit to an existing one look
    identical from the UI's side (same form, same "Save" button).

    This works by SQLite's "upsert" syntax: `ON CONFLICT(parent_id) DO UPDATE ...`. db.py's
    `children` table has `parent_id INTEGER NOT NULL UNIQUE` -- that UNIQUE constraint is what
    "ON CONFLICT(parent_id)" is reacting to. In plain terms: "try to INSERT a new row; if that
    would violate the UNIQUE constraint (a row for this parent_id already exists), UPDATE that
    existing row instead." `excluded.condition` refers to the value that WOULD have been
    inserted (the new one) -- it's SQLite's name for "the row that conflicted," not the row
    already in the table. Doing this as one atomic statement, rather than "SELECT to check if a
    row exists, then INSERT or UPDATE accordingly" in Python, avoids a race where two near-
    simultaneous saves could both see "no row yet" and both try to INSERT, with the second one
    failing.
    """
    now = datetime.now(timezone.utc).isoformat()
    connection = get_connection()
    try:
        connection.execute(
            """
            INSERT INTO children (parent_id, condition, age_range, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(parent_id) DO UPDATE SET
                condition = excluded.condition,
                age_range = excluded.age_range,
                updated_at = excluded.updated_at
            """,
            (parent_id, condition, age_range, now, now),
        )
        connection.commit()
    finally:
        connection.close()
    return ChildProfile(condition=condition, age_range=age_range)


# Manual sanity check, same pattern as db.py/auth.py: `uv run python child_profile.py` creates a
# throwaway parent account (reusing auth.sign_up so this file doesn't need its own duplicate
# account-creation logic), saves a profile, re-reads it back, saves again with DIFFERENT values
# to confirm the upsert UPDATES rather than creating a second row, and confirms a never-saved
# account correctly comes back as None.
if __name__ == "__main__":
    import secrets

    from auth import sign_up

    test_account = sign_up(f"test_profile_{secrets.token_hex(4)}", "irrelevant-password")

    assert get_child_profile(test_account.parent_id) is None, "BUG: brand-new account should have no profile yet"
    print("Correctly None before any save")

    saved = save_child_profile(test_account.parent_id, condition="Autism", age_range="4-6")
    reloaded = get_child_profile(test_account.parent_id)
    assert reloaded == saved, "BUG: what was saved doesn't match what was read back"
    print(f"Saved and reloaded: {reloaded}")

    save_child_profile(test_account.parent_id, condition="ADHD", age_range="7-9")
    updated = get_child_profile(test_account.parent_id)
    assert updated.condition == "ADHD" and updated.age_range == "7-9", "BUG: second save should UPDATE, not add a second row"
    print(f"Second save correctly updated the same row: {updated}")
