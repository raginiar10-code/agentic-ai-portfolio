from __future__ import annotations

import sqlite3
from pathlib import Path

# The SQLite file lives next to this module, inside the project root -- NOT inside kb/ (that
# folder is for the read-only, version-controlled knowledge base JSON files) and NOT committed to
# git (see .gitignore). This file will hold real parent accounts and real child profile data
# created while someone actually uses the running app -- that is exactly the kind of generated,
# personal data a repo should never carry, unlike kb/*.json which is fictional seed content we
# WANT checked in. Path(__file__).parent (not a bare relative string) means this works no matter
# what directory you happen to run `streamlit run app.py` or `python db.py` from.
_DB_PATH = Path(__file__).parent / "northstar.db"


def get_connection() -> sqlite3.Connection:
    """Open a fresh connection to the NorthStar SQLite database.

    Deliberately a NEW connection on every call, rather than one shared global connection kept
    open for the app's whole lifetime: sqlite3 connections aren't safe to share across Streamlit's
    rerun-on-every-interaction model (each interaction can effectively be a different thread)
    without extra locking code. Opening a cheap new connection per call sidesteps that entire
    class of bug -- SQLite connections are fast to open and this app's traffic is nowhere near
    the volume where that would matter.

    `connection.row_factory = sqlite3.Row` is what lets every caller read a result row by column
    NAME (e.g. `row["username"]`) instead of by position (`row[1]`) -- plain sqlite3 rows are
    just tuples by default, and indexing a tuple by position silently breaks (wrong value, no
    error) if a column ever gets added, removed, or reordered in the CREATE TABLE statement
    below. Row-by-name costs nothing and removes that whole failure mode.
    """
    connection = sqlite3.connect(_DB_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def init_db() -> None:
    """Create the `parents`, `children`, `chat_messages`, `activity_log`, and `critic_reviews`
    tables if they don't already exist.

    `CREATE TABLE IF NOT EXISTS` is a no-op on every call after the first, so this is safe to run
    every time the app starts -- there's no separate "first-time setup" step for anyone (a
    reviewer included) to remember or forget. Called once, early, from app.py once that's wired
    up in a later chunk.
    """
    connection = get_connection()
    try:
        # `parents` is the login/auth table: one row per registered parent account.
        # `consent_given_at` starts NULL and is only ever set once, the moment a parent explicitly
        # agrees to NorthStar storing their child's information (added in a later chunk). It is
        # nullable ON PURPOSE -- NULL IS the "no consent yet" state, not a placeholder to clean up
        # later. SQLite has no built-in way to enforce "no row may exist in `children` for a
        # parent whose consent_given_at is still NULL" as a schema-level constraint (that would
        # need a CHECK that reads a DIFFERENT table, which SQLite doesn't support) -- so that
        # rule has to be enforced in the application code that writes to `children`, not here.
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS parents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                password_salt TEXT NOT NULL,
                created_at TEXT NOT NULL,
                consent_given_at TEXT
            )
            """
        )
        # `children` is the persisted version of the sidebar's "Condition" / "Age range" fields,
        # which today only live in Streamlit's session state and vanish the moment the tab
        # closes. `parent_id INTEGER NOT NULL UNIQUE REFERENCES parents(id)` is a deliberate MVP
        # narrowing, not an oversight: UNIQUE on parent_id means at most ONE child row per parent
        # account, matching the app's current single Condition/Age-range pair exactly. Real
        # families with more than one child are real future-state work (tracked in
        # ARCHITECTURE.md's Phase 2 roadmap already) -- supporting that later means dropping this
        # UNIQUE constraint and adding a child-picker to the UI, not a schema rewrite.
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS children (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                parent_id INTEGER NOT NULL UNIQUE REFERENCES parents(id),
                condition TEXT,
                age_range TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        # `chat_messages` is one row per message, EITHER side of the conversation (one row for
        # what the parent typed, a separate row for what NorthStar replied) -- not one row per
        # turn-pair -- because that's the natural unit for two different later uses:
        #   (1) showing a parent their own history back, in order, exactly as it happened; and
        #   (2) Chunk 8's actual memory feature, which will read back the last few rows and feed
        #       them into the Router/Info/Pathway prompts as "here's what was just discussed."
        # `workflow` separates Workflow 1 ("ask_northstar") from Workflow 2 ("daily_support") --
        # they're conceptually different conversations (one is Q&A, the other is a standing
        # context-gathering dialogue), so memory built in Chunk 8 should only ever pull prior
        # turns from the SAME workflow, never mix the two. `intent` is nullable because a user
        # row has no intent of its own (only the Router's classification of it, which is only
        # produced for the row that follows) -- NULL here means "not applicable to this row,"
        # same pattern as `consent_given_at` above meaning "not yet," not "forgot to fill in."
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS chat_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                parent_id INTEGER NOT NULL REFERENCES parents(id),
                workflow TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                intent TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        # Every read of this table in later chunks will be "give me this one parent's recent
        # messages" (never "give me everyone's") -- an index on parent_id is what keeps that
        # lookup fast as the table grows, instead of SQLite scanning every row every time.
        # Trivial at this app's scale, but free to add now and it's the textbook-correct index
        # for this exact access pattern.
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_chat_messages_parent_id ON chat_messages(parent_id)"
        )
        # `activity_log` holds the STRUCTURED version of a Daily Support outcome, separate from
        # the flattened display paragraph `chat_messages.content` stores. Where `chat_messages`
        # answers "what did the conversation look like," this table answers "what, specifically,
        # got recommended" -- with real typed columns (duration_minutes as a number, not buried
        # inside a sentence) that can actually be filtered/counted/compared later, e.g. "has this
        # child been given the same activity twice this week."
        #   - `chat_message_id` links each row back to the exact chat_messages row that displayed
        #     it -- the two tables describe the SAME event from two angles, and this is how you
        #     trace one to the other instead of them being disconnected records.
        #   - `materials_needed` and `steps` are each a LIST in Python (see ActivityOutput in
        #     agents/activity.py), but SQLite has no list column type -- they're stored as JSON
        #     text (via json.dumps when writing, json.loads when reading back) rather than
        #     comma-joined, since a material or step could itself contain a comma.
        #   - `activity_name`/`duration_minutes`/`materials_needed`/`steps`/`why_it_helps` are all
        #     nullable and left NULL when `critic_verdict='flag'`: if the Safety Critic suppressed
        #     the response, the parent never saw a real activity, so there is nothing true to
        #     record as "recommended" -- writing the rejected content in here would misrepresent
        #     what actually happened.
        #   - `critic_verdict` is NOT nullable and is recorded on every attempt, pass or flag, on
        #     purpose: if a flagged attempt left no row at all, "how often does the Critic have to
        #     step in" would be invisible in our own data -- only the successes would ever show up.
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS activity_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                parent_id INTEGER NOT NULL REFERENCES parents(id),
                chat_message_id INTEGER REFERENCES chat_messages(id),
                current_need TEXT NOT NULL,
                activity_name TEXT,
                duration_minutes INTEGER,
                materials_needed TEXT,
                steps TEXT,
                why_it_helps TEXT,
                critic_verdict TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        # Same reasoning as idx_chat_messages_parent_id above -- every read of this table will
        # also be scoped to one parent.
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_activity_log_parent_id ON activity_log(parent_id)"
        )
        # `critic_reviews` is the minimal human-in-the-loop (HITL) table: one row per Safety
        # Critic call, pass or flag, linked back to the exact chat_messages row it reviewed —
        # same chat_message_id-linking pattern as activity_log above, same reasoning (verdict is
        # NOT nullable and recorded on every attempt, not just flags, so "how often does the
        # Critic actually have to step in" stays answerable from our own data, not just the
        # suppressed cases).
        #
        # IMPORTANT SCOPE NOTE, read before building the UI on top of this table: this is a
        # SELF-review queue, not a staff/clinician review queue. Every read of this table (see
        # review_queue.py) is scoped to one parent_id, same as every other table here — a parent
        # can see what got filtered from THEIR OWN conversations and why, not a cross-account
        # quality-review tool. A genuine "a human reviewer checks flagged content across every
        # family" workflow needs a SEPARATE reviewer role (nothing in `parents` distinguishes a
        # reviewer from a parent today) — that's real, deliberately deferred future work, not
        # something this table pretends to already support.
        #   - `reviewed_at` starts NULL (not yet looked at) and is set once, the moment the
        #     account this flag belongs to marks it acknowledged — same NULL-means-"not yet"
        #     pattern as `consent_given_at` on `parents`.
        #   - `flagged_response_text` is nullable and set ONLY when verdict='flag'. When a
        #     response is flagged, the corresponding chat_messages row's `content` is the generic
        #     safe-fallback message the parent actually saw (see main.py's
        #     _safety_fallback_response()), NOT what the agent originally generated — so without
        #     this column, a review queue built on this table would have no way to show a human
        #     reviewer the actual thing that got suppressed, only that SOMETHING did. A pass
        #     needs no separate copy here: chat_messages.content already IS what was shown.
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS critic_reviews (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                parent_id INTEGER NOT NULL REFERENCES parents(id),
                chat_message_id INTEGER NOT NULL REFERENCES chat_messages(id),
                verdict TEXT NOT NULL,
                checks TEXT NOT NULL,
                reason TEXT,
                flagged_response_text TEXT,
                reviewed_at TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        # Same reasoning as the other parent_id indexes above.
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_critic_reviews_parent_id ON critic_reviews(parent_id)"
        )
        # Nothing is written to disk until commit() -- without this line, every CREATE TABLE /
        # CREATE INDEX statement above would be silently rolled back the moment
        # `connection.close()` runs.
        connection.commit()
    finally:
        # `finally` guarantees the connection closes even if one of the execute() calls above
        # raised (e.g. a malformed statement) -- we don't want to leak an open file handle to
        # northstar.db just because table/index creation failed.
        connection.close()


# This block only runs when the file is executed directly (`uv run python db.py`), never when
# something else imports `get_connection`/`init_db` from it -- same __main__ guard pattern as
# main.py. It exists purely as a manual sanity check: a one-command way to confirm the schema
# actually landed on disk, without needing a separate SQLite browser tool installed.
if __name__ == "__main__":
    init_db()
    connection = get_connection()
    tables = connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    print(f"northstar.db ready at: {_DB_PATH}")
    print("Tables found:", [row["name"] for row in tables])
    connection.close()
