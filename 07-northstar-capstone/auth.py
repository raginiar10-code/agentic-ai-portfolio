from __future__ import annotations

import hashlib
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone

from db import get_connection

# PBKDF2-HMAC-SHA256 iteration count. OWASP's 2023 guidance for PBKDF2-SHA256 recommends
# 600,000+ iterations for a production login system. This is deliberately lower (200,000) --
# fast enough that a capstone demo doesn't notice the delay on every login, while still being
# meaningfully slower to brute-force than a single unsalted hash. Documented here explicitly so
# this is a stated tradeoff, not an oversight: this app is not handling real patients' real
# passwords, and that honesty matters more than pretending this is production-grade security.
_PBKDF2_ITERATIONS = 200_000


@dataclass
class AuthResult:
    """What sign_up()/log_in() hand back on success -- just enough to identify WHO is logged in
    and whether they've consented yet, nothing else. Deliberately NOT a Pydantic BaseModel like
    the agent I/O schemas elsewhere in this project (RouterOutput, SafetyCriticOutput, etc.) --
    those exist specifically to constrain what an LLM is allowed to return. This has nothing to
    do with an LLM; it's a plain internal data holder, so a plain @dataclass is the right-sized
    tool, not a reach for consistency with a different part of the codebase that solves a
    different problem.
    """

    parent_id: int
    username: str
    consent_given_at: str | None


def _hash_password(password: str, salt: str) -> str:
    """Derive a password hash from `password` + `salt` using PBKDF2-HMAC-SHA256.

    `salt` is mixed into every hash specifically so two parents who happen to pick the same
    password don't end up with the same password_hash in the `parents` table -- without a salt,
    anyone who ever saw the database could spot "these two accounts share a password" just by
    comparing hashes, before even trying to crack either one. `.encode()` turns the Python `str`
    into raw bytes, because hashlib's functions operate on bytes, not text -- `str` is Unicode
    characters; `bytes` is what actually gets hashed.
    """
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode(), salt.encode(), _PBKDF2_ITERATIONS
    ).hex()


def sign_up(username: str, password: str) -> AuthResult:
    """Create a new parent account. Raises ValueError if the username is already taken.

    `secrets.token_hex(16)` generates the per-account salt -- `secrets`, not `random`, because
    `random` is a predictable pseudo-random generator (fine for e.g. shuffling a quiz, NOT safe
    for anything security-related); `secrets` is Python's dedicated module for values that must
    actually be unguessable. `consent_given_at` is left out of the INSERT entirely, so it falls
    back to the column's default of NULL -- a brand-new account has, by definition, not consented
    to anything yet (that's a later chunk).
    """
    salt = secrets.token_hex(16)
    password_hash = _hash_password(password, salt)
    created_at = datetime.now(timezone.utc).isoformat()

    connection = get_connection()
    try:
        try:
            cursor = connection.execute(
                "INSERT INTO parents (username, password_hash, password_salt, created_at) "
                "VALUES (?, ?, ?, ?)",
                (username, password_hash, salt, created_at),
            )
        except sqlite3.IntegrityError as exc:
            # The `UNIQUE` constraint on parents.username (see db.py) is what actually raises
            # this -- SQLite enforces it at the database level, so there's no race between
            # "check if username exists" and "insert it" (two separate steps would leave a gap
            # where two signups for the same username could both pass the check). We catch the
            # low-level database error here and re-raise it as a plain ValueError instead, so
            # nothing outside this file ever needs to know SQLite specifically is involved.
            raise ValueError(f"Username {username!r} is already taken") from exc
        connection.commit()
        # cursor.lastrowid is the auto-incremented `id` SQLite just assigned to the row we
        # inserted above -- this is how we get the new account's id back without a second query.
        return AuthResult(parent_id=cursor.lastrowid, username=username, consent_given_at=None)
    finally:
        connection.close()


def log_in(username: str, password: str) -> AuthResult:
    """Verify a username/password pair against the `parents` table. Raises ValueError if the
    username doesn't exist OR the password doesn't match -- deliberately the SAME error message
    either way ("Invalid username or password"), rather than a different message per case. If
    "unknown username" and "wrong password" gave different errors, anyone could use that
    difference to discover which usernames are registered at all, one guess at a time, without
    ever needing a correct password. One message for both closes that off.
    """
    connection = get_connection()
    try:
        row = connection.execute(
            "SELECT id, username, password_hash, password_salt, consent_given_at "
            "FROM parents WHERE username = ?",
            (username,),
        ).fetchone()
    finally:
        connection.close()

    if row is None:
        raise ValueError("Invalid username or password")

    candidate_hash = _hash_password(password, row["password_salt"])
    # secrets.compare_digest, not `==`, to compare the two hashes. A plain `==` on strings
    # returns False the INSTANT it finds the first mismatched character, which means a wrong
    # password that differs at character 1 fails microscopically faster than one that differs at
    # character 40 -- in principle, measuring that tiny time difference many times over (a
    # "timing attack") could leak information about the correct hash. compare_digest() always
    # takes the same amount of time regardless of where the mismatch is, closing that leak. It
    # doesn't matter for a capstone demo's traffic level, but it's the textbook-correct function
    # for comparing secrets, and costs nothing extra to use correctly from the start.
    if not secrets.compare_digest(candidate_hash, row["password_hash"]):
        raise ValueError("Invalid username or password")

    return AuthResult(
        parent_id=row["id"], username=row["username"], consent_given_at=row["consent_given_at"]
    )


def record_consent(parent_id: int) -> str:
    """Mark `parent_id` as having explicitly consented to NorthStar persisting their child's
    information. Returns the UTC timestamp it was recorded, so the caller (app.py) can update
    the in-memory AuthResult it's already holding without a second round-trip query just to
    read back what was just written.

    This is called from exactly one place: the consent screen's "I agree" button in app.py,
    which only appears once (it's skipped on every later login once consent_given_at is set) --
    never called automatically, never implied by any other action, and never bundled into
    sign_up() itself. Consent has to be its own explicit step, not a side effect of something
    else a parent did for an unrelated reason.
    """
    consent_given_at = datetime.now(timezone.utc).isoformat()
    connection = get_connection()
    try:
        connection.execute(
            "UPDATE parents SET consent_given_at = ? WHERE id = ?", (consent_given_at, parent_id)
        )
        connection.commit()
    finally:
        connection.close()
    return consent_given_at


# Manual sanity check, same pattern as db.py's __main__ block: `uv run python auth.py` signs up
# a throwaway test account, confirms logging in with the right password works, confirms the
# WRONG password is rejected, and confirms signing up the same username twice is rejected too --
# without needing a separate test framework for a quick "does this actually work" check.
if __name__ == "__main__":
    test_username = f"test_user_{secrets.token_hex(4)}"
    result = sign_up(test_username, "correct-password-123")
    print(f"Signed up: {result}")

    logged_in = log_in(test_username, "correct-password-123")
    print(f"Logged in with correct password: {logged_in}")

    try:
        log_in(test_username, "wrong-password")
        print("BUG: wrong password was accepted")
    except ValueError as exc:
        print(f"Correctly rejected wrong password: {exc}")

    try:
        sign_up(test_username, "another-password")
        print("BUG: duplicate username was accepted")
    except ValueError as exc:
        print(f"Correctly rejected duplicate username: {exc}")

    assert logged_in.consent_given_at is None, "BUG: brand-new account should start unconsented"
    consent_timestamp = record_consent(logged_in.parent_id)
    reloaded = log_in(test_username, "correct-password-123")
    assert reloaded.consent_given_at == consent_timestamp, "BUG: consent didn't persist"
    print(f"Consent recorded and persisted: {reloaded.consent_given_at}")
