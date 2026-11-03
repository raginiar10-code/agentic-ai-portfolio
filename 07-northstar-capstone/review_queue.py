from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone

from db import get_connection


@dataclass
class CriticReview:
    """One Safety Critic call, read back out of `critic_reviews` -- the minimal HITL record.

    SCOPE REMINDER (see db.py's critic_reviews comment for the full version): every function in
    this module takes/filters by `parent_id`, same as every other table in this project. This is
    a SELF-review queue -- a parent sees what got filtered from their own conversations -- not a
    staff/clinician review queue across every account. That would need a separate reviewer role,
    which does not exist yet; documented as deferred future work, not silently assumed.
    """

    id: int
    chat_message_id: int
    verdict: str
    checks: dict[str, bool]
    reason: str | None
    flagged_response_text: str | None
    reviewed_at: str | None
    created_at: str


def log_critic_review(
    parent_id: int,
    chat_message_id: int,
    verdict: str,
    checks: dict[str, bool],
    reason: str | None,
    flagged_response_text: str | None = None,
) -> int:
    """Insert one row into `critic_reviews`. Returns the new row's id.

    Called for EVERY Safety Critic call, pass or flag (see db.py's critic_reviews comment for
    why) -- app.py's _log_turn() is the one caller, right after it logs the assistant's
    chat_messages row, so chat_message_id always refers to a row that already exists.

    `flagged_response_text` should only ever be passed (non-None) when verdict == "flag" -- it's
    what the agent actually generated before the Critic suppressed it (main.py's
    `flagged_original_response`), kept here because the corresponding chat_messages row only has
    the generic safe-fallback text the parent actually saw, not the original. See db.py's
    critic_reviews comment for the full reasoning.
    """
    connection = get_connection()
    try:
        cursor = connection.execute(
            "INSERT INTO critic_reviews "
            "(parent_id, chat_message_id, verdict, checks, reason, flagged_response_text, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                parent_id,
                chat_message_id,
                verdict,
                json.dumps(checks),
                reason,
                flagged_response_text,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        connection.commit()
        return cursor.lastrowid
    finally:
        connection.close()


def get_flagged_reviews(parent_id: int, include_reviewed: bool = False) -> list[CriticReview]:
    """Return `parent_id`'s flagged (verdict='flag') critic_reviews rows, most recent first.

    `include_reviewed=False` (the default) is what the sidebar queue actually wants: "what still
    needs my attention," not a permanent audit log of everything ever flagged -- an
    already-acknowledged item staying in the default view forever would make the queue grow
    without bound and bury the things that are actually new. Pass True for a "show me
    everything, including what I've already looked at" view instead.
    """
    connection = get_connection()
    try:
        if include_reviewed:
            rows = connection.execute(
                "SELECT * FROM critic_reviews WHERE parent_id = ? AND verdict = 'flag' ORDER BY id DESC",
                (parent_id,),
            ).fetchall()
        else:
            rows = connection.execute(
                "SELECT * FROM critic_reviews WHERE parent_id = ? AND verdict = 'flag' AND reviewed_at IS NULL "
                "ORDER BY id DESC",
                (parent_id,),
            ).fetchall()
    finally:
        connection.close()
    return [
        CriticReview(
            id=r["id"],
            chat_message_id=r["chat_message_id"],
            verdict=r["verdict"],
            checks=json.loads(r["checks"]),
            reason=r["reason"],
            flagged_response_text=r["flagged_response_text"],
            reviewed_at=r["reviewed_at"],
            created_at=r["created_at"],
        )
        for r in rows
    ]


def mark_reviewed(review_id: int) -> None:
    """Set `reviewed_at` on one critic_reviews row -- the one write action the minimal review
    queue UI offers: "I've seen this." No `parent_id` check here deliberately matches every
    other single-row-by-id write in this project (e.g. auth.py has no equivalent either) --
    review_id values are only ever sourced from a CriticReview this same parent's own
    get_flagged_reviews() call just returned, never from untrusted external input.
    """
    connection = get_connection()
    try:
        connection.execute(
            "UPDATE critic_reviews SET reviewed_at = ? WHERE id = ?",
            (datetime.now(timezone.utc).isoformat(), review_id),
        )
        connection.commit()
    finally:
        connection.close()


# Manual sanity check, same pattern as the other modules: logs a pass and a flag, confirms
# get_flagged_reviews() returns only the flag (not the pass), confirms mark_reviewed() removes
# it from the default (unreviewed-only) view while still finding it with include_reviewed=True.
if __name__ == "__main__":
    import secrets

    from auth import sign_up
    from chat_log import log_message

    test_account = sign_up(f"test_review_{secrets.token_hex(4)}", "irrelevant-password")
    msg_id = log_message(test_account.parent_id, workflow="ask_northstar", role="assistant", content="...", intent="info")

    log_critic_review(test_account.parent_id, msg_id, verdict="pass", checks={"no_diagnosis": True}, reason=None)
    flagged_id = log_critic_review(
        test_account.parent_id,
        msg_id,
        verdict="flag",
        checks={"no_diagnosis": True, "is_correct": False},
        reason="Example flag reason.",
        flagged_response_text="This is what the agent actually generated before being suppressed.",
    )

    unreviewed = get_flagged_reviews(test_account.parent_id)
    assert len(unreviewed) == 1 and unreviewed[0].id == flagged_id, "BUG: should return exactly the one flagged row, not the pass"
    print(f"Correctly returned only the flagged review: {unreviewed[0]}")

    mark_reviewed(flagged_id)
    unreviewed_after = get_flagged_reviews(test_account.parent_id)
    assert unreviewed_after == [], "BUG: a reviewed item should drop out of the default (unreviewed-only) view"
    print("Correctly dropped out of the unreviewed-only view after mark_reviewed()")

    all_reviews = get_flagged_reviews(test_account.parent_id, include_reviewed=True)
    assert len(all_reviews) == 1 and all_reviews[0].reviewed_at is not None, "BUG: include_reviewed=True should still find it"
    print(f"Still findable with include_reviewed=True: {all_reviews[0]}")
