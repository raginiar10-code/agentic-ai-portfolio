from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

# Same import-order rule as main.py: load_dotenv() must run before anything that constructs an
# OpenAI/LangSmith client at module level. `from main import ...` below would already trigger
# main.py's own load_dotenv() call as a side effect of importing it — but we call it explicitly
# here too, rather than silently depending on another module's internal behavior.
load_dotenv()

from langchain_core.tracers.context import collect_runs  # noqa: E402
from langsmith import Client as LangSmithClient  # noqa: E402

from auth import log_in, record_consent, sign_up  # noqa: E402
from chat_log import get_recent_messages, log_activity, log_message  # noqa: E402
from child_profile import get_child_profile, save_child_profile  # noqa: E402
from db import init_db  # noqa: E402
from review_queue import get_flagged_reviews, log_critic_review, mark_reviewed  # noqa: E402
from main import run, run_daily_support  # noqa: E402

# Safe to call on every app start/rerun -- CREATE TABLE IF NOT EXISTS makes this a no-op once
# the tables already exist (see db.py). There's no separate "first run" setup step to remember.
init_db()

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

_FEEDBACK_LOG_PATH = Path(__file__).parent / "feedback.jsonl"
# Rows (not turns) of Ask NorthStar history fetched for Router follow-up resolution -- 6 rows is
# the last 3 back-and-forth exchanges (log_message() writes one row per side of a turn). See
# _run_ask_northstar()'s docstring for why this is a fixed, deliberately small window rather than
# "all of this parent's history."
_HISTORY_WINDOW = 6
_langsmith_client = LangSmithClient()

st.set_page_config(page_title="NorthStar", page_icon="🧭", layout="wide")

# Warm, calm palette — deliberately NOT clinical white-and-blue. CAPSTONE_HANDOFF.md §8 flags
# "letting the UI regress to clinical tone" as a failure mode to actively guard against.
st.markdown(
    """
    <style>
    .stApp { background-color: #FAF7F2; }
    [data-testid="stSidebar"] { background-color: #F0EBE3; }
    .stChatMessage { border-radius: 16px; }
    .safety-banner {
        background-color: #E8F0E9;
        border-left: 4px solid #7FA88C;
        padding: 0.75rem 1rem;
        border-radius: 8px;
        font-size: 0.9rem;
        margin-bottom: 1rem;
    }
    .therapist-card {
        background-color: #FFFFFF;
        border: 1px solid #E5DED2;
        border-radius: 12px;
        padding: 0.75rem 1rem;
        margin-bottom: 0.5rem;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


def _init_session_state() -> None:
    """Initialize everything this app keeps across reruns.

    Streamlit reruns the ENTIRE script top-to-bottom on every interaction (a button click, a
    chat submission) — st.session_state is the one dict that survives those reruns. Anything
    that needs to persist (chat history, saved child context, which feedback was already given)
    must live here, not in a plain module-level variable, which would reset to its initial
    value on every single rerun.
    """
    if "auth" not in st.session_state:
        # None until a parent successfully logs in or signs up THIS browser session -- set by
        # _render_login_gate() below, which is also what blocks the rest of the app from ever
        # rendering while this stays None (via st.stop()). Holds the AuthResult dataclass from
        # auth.py (parent_id, username, consent_given_at) once set.
        st.session_state.auth = None
    if "messages" not in st.session_state:
        st.session_state.messages = []
    if "daily_support_state" not in st.session_state:
        # condition/age_range persist here once saved; current_need is NOT stored here — it's
        # set fresh on each Daily Support button click below, since "what my child needs right
        # now" is a per-request thing, not a standing fact like condition/age.
        st.session_state.daily_support_state = {}
    if "feedback_given" not in st.session_state:
        st.session_state.feedback_given = {}  # message_id -> "up" | "down"
    if "show_comment_for" not in st.session_state:
        st.session_state.show_comment_for = None  # message_id currently showing its comment box
    if "awaiting_daily_support_reply" not in st.session_state:
        # True right after Intake asks a clarifying question and is waiting on the parent's
        # answer. The main chat box is shared between both workflows, so without this flag the
        # next thing the parent types has no way to be routed back into Daily Support — it
        # would just go to Ask NorthStar's Router instead, which has no idea a clarification
        # was pending (this was a real bug: a reply like "he is 9.5 years, has BLBS2" got
        # classified as an info-intent question instead of completing the activity request).
        st.session_state.awaiting_daily_support_reply = False


def _render_login_gate() -> None:
    """Render a login/signup form and HALT this script run until a parent is authenticated.

    Streamlit reruns this ENTIRE file top to bottom on every interaction (see
    _init_session_state's own docstring above). `st.stop()` at the end of this function exploits
    that: it halts the CURRENT rerun immediately, so nothing below this point in the file --
    the sidebar, the chat history, the chat input -- ever executes while logged out. That means
    there's no separate "is someone logged in" check scattered through every other function;
    this one gate, called once, is the only place that matters.

    Nothing from this form is sent anywhere outside this app -- auth.log_in()/auth.sign_up() only
    talk to the local northstar.db file (see auth.py, db.py).
    """
    # st.columns([1, 2, 1]) splits the page into three side-by-side regions sized 1:2:1 --
    # i.e. the middle one is exactly half the page width, with a quarter-width margin on each
    # side. Putting everything inside `with _center:` renders it ONLY in that middle region,
    # which is what actually centers the form and keeps it from stretching edge to edge -- the
    # two outer columns are never given any content, so they just sit empty as the margins.
    _left, _center, _right = st.columns([1, 2, 1])
    with _center:
        st.title("🧭 NorthStar")
        st.caption("Sign in to continue. Your account only exists on this machine's local database.")

        # st.tabs gives two clearly separate forms (login vs. signup) in the same space, rather
        # than one form with a mode toggle -- less chance of, say, a signup accidentally
        # submitting into the login button's handler.
        login_tab, signup_tab = st.tabs(["Log in", "Sign up"])

        with login_tab:
            # .strip() on the way in guards against the common slip of a trailing space
            # typed/pasted into the username box, which would otherwise silently fail to match
            # the stored username and look like a wrong-password error instead of what it
            # actually is.
            username = st.text_input("Username", key="login_username").strip()
            password = st.text_input("Password", type="password", key="login_password")
            if st.button("Log in", key="login_submit"):
                try:
                    st.session_state.auth = log_in(username, password)
                    st.rerun()
                except ValueError as exc:
                    # auth.log_in() raises the SAME message for "no such username" and "wrong
                    # password" on purpose (see auth.py) -- st.error() here just displays
                    # whatever that message is, it doesn't add any extra detail.
                    st.error(str(exc))

        with signup_tab:
            new_username = st.text_input("Choose a username", key="signup_username").strip()
            new_password = st.text_input(
                "Choose a password", type="password", key="signup_password"
            )
            if st.button("Sign up", key="signup_submit"):
                if not new_username or not new_password:
                    st.error("Username and password are both required.")
                else:
                    try:
                        st.session_state.auth = sign_up(new_username, new_password)
                        st.rerun()
                    except ValueError as exc:
                        # Raised by auth.sign_up() when the username is already taken (db.py's
                        # own UNIQUE constraint on parents.username is what actually catches this).
                        st.error(str(exc))

    st.stop()


def _render_consent_gate() -> None:
    """Render the one-time consent screen and HALT (st.stop()) until the parent explicitly
    agrees. Same st.stop()-based gate mechanism as _render_login_gate() above, checked right
    after it -- a parent can be logged in (auth is not None) and STILL be blocked here if
    consent_given_at is still None, which is exactly db.py's documented rule: nothing may be
    written to `children`/`chat_messages`/`activity_log` for an account that hasn't consented.
    Enforcing it as a full-app gate (rather than a check scattered before every future write to
    those tables) means that rule holds by construction -- every later chunk that writes to
    them runs strictly after this point, so it can never run for an unconsented account.
    """
    # Same centering trick as _render_login_gate() above -- middle column of a 1:2:1 split is
    # half the page width, centered.
    _left, _center, _right = st.columns([1, 2, 1])
    with _center:
        st.title("🧭 NorthStar")
        st.subheader("Before we continue")
        st.markdown(
            "To give your child relevant, remembered support instead of asking the same "
            "questions every time, NorthStar would like to save the following to a **local "
            "database file on this machine** (`northstar.db` — nothing is sent anywhere beyond "
            "what already happens today to generate a response, such as the calls to "
            "OpenAI/LangSmith documented in `ARCHITECTURE.md`):\n\n"
            "- Your child's **condition and age range**, so you don't have to re-enter it every session\n"
            "- Your **conversation history** in Ask NorthStar, so you can look back on what was discussed\n"
            "- **Which activities were suggested** in Daily Support, so NorthStar can vary them over time\n\n"
            "You can decline and log out instead — nothing will be saved, and you can sign up "
            "again later if you change your mind."
        )
        col_agree, col_decline = st.columns([1, 1])
        with col_agree:
            if st.button("I agree — continue", type="primary"):
                consent_given_at = record_consent(st.session_state.auth.parent_id)
                # Update the AuthResult ALREADY sitting in session_state in place, rather than
                # re-querying the database for it -- record_consent() just wrote this exact
                # value, so re-reading it back would be redundant work for the same answer.
                st.session_state.auth.consent_given_at = consent_given_at
                st.rerun()
        with col_decline:
            if st.button("Decline and log out"):
                st.session_state.clear()
                st.rerun()

    st.stop()


_init_session_state()

# The gate: everything below this point in the file (every function call at module level, all
# the way down to _render_sidebar()/_render_main_panel() at the bottom) is simply never reached
# on a rerun where either check below is True, because both gate functions end in st.stop().
# Order matters: login is checked first (can't consent without an identity to attach it to),
# consent second -- a parent can be logged in and STILL be stopped here until they agree.
if st.session_state.auth is None:
    _render_login_gate()
elif st.session_state.auth.consent_given_at is None:
    _render_consent_gate()

# Everything below this point only ever runs for a logged-in, consented parent -- both gates
# above have already st.stop()'d otherwise. "child_profile_loaded" not yet being in
# session_state means this is the first rerun since login (it's wiped by Log out's
# session_state.clear()), so this is the ONE time per login we go read the database for the
# saved profile, rather than re-querying it on every single rerun (every button click, every
# chat message) for a value that isn't going to have changed underneath us mid-session.
if "child_profile_loaded" not in st.session_state:
    _saved_profile = get_child_profile(st.session_state.auth.parent_id)
    if _saved_profile is not None:
        st.session_state.daily_support_state["condition"] = _saved_profile.condition
        st.session_state.daily_support_state["age_range"] = _saved_profile.age_range
    st.session_state.child_profile_loaded = True


def _post_feedback(run_ids: list[str], query: str, response: str, score: float, comment: str = "") -> None:
    """Post feedback to LangSmith (attached to every run from that turn) AND append a backup
    line to feedback.jsonl — IMPLEMENTATION_PLAN.md's Day 5 task calls for both: LangSmith for
    the live online-eval dashboard, feedback.jsonl as a plain-text record that doesn't depend
    on LangSmith being reachable to exist at all.
    """
    for run_id in run_ids:
        try:
            _langsmith_client.create_feedback(run_id=run_id, key="user_rating", score=score, comment=comment or None)
        except Exception as exc:
            # Broad except is deliberate here, not a swallowed error: this is a side-channel
            # call to an EXTERNAL service (LangSmith's API). A transient network blip there must
            # never crash the parent's chat session — log it loudly (not silently) and still
            # write the local feedback.jsonl line below regardless of whether this succeeded.
            logger.warning(f"Failed to post feedback to LangSmith for run {run_id}: {exc}")

    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "query": query,
        "response": response,
        "score": score,
        "comment": comment,
        "run_ids": run_ids,
    }
    with _FEEDBACK_LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def _run_ask_northstar(query: str) -> dict:
    """Call main.run() wrapped in collect_runs(), to capture the LangSmith run IDs generated
    during this turn. collect_runs() is a context manager from langchain_core that records
    every run traced while code runs inside it — afterward, cb.traced_runs holds them. Without
    this, the feedback buttons would have nothing to actually attach 👍/👎 to on LangSmith's side.

    Fetches this parent's recent Ask NorthStar history from the database and passes it into
    run(), which threads it on into the Router for follow-up resolution (see
    agents/router.py's route() docstring). _HISTORY_WINDOW rows (3 back-and-forth exchanges) is
    a deliberately small, fixed window, not "everything this parent has ever asked" — every row
    included here is tokens spent on every single Router call from now on, so this stays a
    real, stated cost/usefulness tradeoff rather than growing unboundedly with a long-lived
    account.
    """
    history = get_recent_messages(st.session_state.auth.parent_id, workflow="ask_northstar", limit=_HISTORY_WINDOW)
    with collect_runs() as cb:
        result = run(query, history=history)
    result["run_ids"] = [str(r.id) for r in cb.traced_runs]
    return result


def _run_daily_support_turn(query: str) -> dict:
    """Same collect_runs() reasoning as _run_ask_northstar above, for Workflow 2."""
    with collect_runs() as cb:
        result, updated_state = run_daily_support(query, st.session_state.daily_support_state)
    st.session_state.daily_support_state = updated_state
    result["run_ids"] = [str(r.id) for r in cb.traced_runs]
    return result


def _result_to_message(result: dict) -> tuple[str, dict]:
    """Normalize a run()/run_daily_support() result dict — whose shape varies by intent — into
    one consistent (display_text, meta_for_transparency_panel) pair, so the chat renderer below
    doesn't need to know which agent actually produced it.
    """
    intent = result["intent"]

    if intent == "daily_support_clarify":
        questions = "\n".join(f"- {q}" for q in result["clarifying_questions"])
        content = f"I need a bit more context before I can suggest an activity:\n{questions}"
        # agent="intake" is checked later to skip feedback buttons/transparency panel for this
        # message type — there's nothing meaningful to rate about a clarifying question.
        return content, {"agent": "intake"}

    critic = result.get("critic")
    # SafetyCriticOutput is a Pydantic object; convert to a plain dict here since
    # st.session_state.messages holds plain data for the renderer to read back later, not
    # Pydantic objects whose shape the renderer would need to know about separately.
    critic_dict = {"verdict": critic.verdict, "checks": critic.checks, "reason": critic.reason} if critic else None
    agent_name = {
        "out_of_scope": "router",
        "info": "info",
        "pathway": "pathway",
        "daily_support_activity": "activity",
    }[intent]

    return result["response"], {
        "agent": agent_name,
        "disclaimer": result.get("disclaimer"),
        "retrieved_chunks": result.get("retrieved_chunks"),
        "specialists": result.get("specialists"),
        "critic": critic_dict,
        "run_ids": result.get("run_ids", []),
        # Present only when the Critic flagged this turn (see main.py's _safety_fallback_response
        # docstring) — the actual content that got suppressed, kept here so the transparency
        # panel can still show it instead of it just vanishing with no trace.
        "flagged_original_response": result.get("flagged_original_response"),
    }


def _append_turn(query: str, content: str, meta: dict) -> None:
    """Append one user message + one assistant message to history. message_id is generated
    once here (not derived from content) because two different turns could coincidentally
    produce identical response text, and Streamlit widget keys (used for feedback buttons
    below) need to be guaranteed-unique, not just usually-unique.
    """
    st.session_state.messages.append({"role": "user", "content": query, "message_id": str(uuid.uuid4())})
    st.session_state.messages.append(
        {"role": "assistant", "content": content, "query": query, "meta": meta, "message_id": str(uuid.uuid4())}
    )


def _log_turn(workflow: str, query: str, content: str, result: dict) -> None:
    """Persist this turn to `chat_messages` (the parent's message AND NorthStar's reply, as two
    separate rows -- see chat_log.py's log_message() docstring for why), plus a matching
    `activity_log` row when this turn actually produced a Daily Support recommendation, plus a
    `critic_reviews` row whenever a Safety Critic call happened at all (the minimal HITL record
    — see db.py's critic_reviews comment).

    This is the DATABASE-backed twin of _append_turn() above, not a replacement for it:
    _append_turn() drives what's rendered THIS session (st.session_state.messages, which still
    resets on logout); this function writes the version that survives a log-out, for Chunk 8's
    memory feature to read back later and for a parent's own history to persist.

    `result.get(...)` throughout, not `result[...]`, because this one function has to handle
    every intent's differently-shaped dict (out_of_scope has no "critic" key at all;
    daily_support_clarify has no "activity_name") without needing a separate branch per intent.
    """
    parent_id = st.session_state.auth.parent_id
    intent = result.get("intent")
    log_message(parent_id, workflow=workflow, role="user", content=query)
    assistant_message_id = log_message(
        parent_id, workflow=workflow, role="assistant", content=content, intent=intent
    )

    # Logged for EVERY intent that actually went through the Critic (info, pathway,
    # daily_support_activity) -- NOT scoped to daily_support_activity like the activity_log
    # block below, since the review queue is meant to surface a flag from ANY workflow, not just
    # Daily Support. out_of_scope and daily_support_clarify have no "critic" key at all (see
    # main.py), so result.get("critic") is simply None for those and nothing gets logged here.
    critic = result.get("critic")
    if critic:
        log_critic_review(
            parent_id,
            chat_message_id=assistant_message_id,
            verdict=critic.verdict,
            checks=critic.checks,
            reason=critic.reason,
            # Only present (non-None) when verdict == "flag" -- see main.py's
            # _safety_fallback_response() docstring for where this comes from.
            flagged_response_text=result.get("flagged_original_response"),
        )

    if intent == "daily_support_activity":
        log_activity(
            parent_id,
            chat_message_id=assistant_message_id,
            current_need=result.get("current_need", ""),
            activity_name=result.get("activity_name"),
            duration_minutes=result.get("duration_minutes"),
            materials_needed=result.get("materials_needed"),
            steps=result.get("steps"),
            why_it_helps=result.get("why_it_helps"),
            # Defaults to "pass" only in the never-expected case of a daily_support_activity
            # result with no critic attached at all -- every real code path in main.py sets one.
            critic_verdict=critic.verdict if critic else "pass",
        )


def _submit_ask_northstar(query: str) -> None:
    with st.spinner(f'Looking into "{query[:60]}"...'):
        result = _run_ask_northstar(query)
    content, meta = _result_to_message(result)
    _append_turn(query, content, meta)
    _log_turn("ask_northstar", query, content, result)


def _submit_daily_support(query: str) -> None:
    with st.spinner("Thinking of something that could help right now..."):
        result = _run_daily_support_turn(query)
    content, meta = _result_to_message(result)
    _append_turn(query, content, meta)
    _log_turn("daily_support", query, content, result)
    # Set True if Intake is still waiting on more info (so the NEXT chat_input message routes
    # back here instead of to Ask NorthStar), False once an activity was actually produced —
    # clearing it is just as important as setting it, otherwise every later chat message would
    # stay stuck being routed to Daily Support forever.
    st.session_state.awaiting_daily_support_reply = result["intent"] == "daily_support_clarify"


def _render_therapist_cards(specialists: list[dict]) -> None:
    """Render specialists as cards, not plain bullets — IMPLEMENTATION_PLAN.md's Day 5 task
    explicitly asks for this. Streamlit has no built-in "card" widget, so this is raw HTML
    inside st.markdown with unsafe_allow_html=True.
    """
    for s in specialists:
        st.markdown(
            f"""
            <div class="therapist-card">
                <strong>{s['name']}</strong><br/>
                {s['specialization'].replace('-', ' ').title()} · {s['area']}, {s['city']}<br/>
                <span style="color:#8A8275;font-size:0.85rem;">{s['note']}</span>
            </div>
            """,
            unsafe_allow_html=True,
        )


def _render_assistant_message(msg: dict) -> None:
    """Render one assistant turn: response text, disclaimer, specialist cards (if any), the
    "How did I arrive at this?" transparency panel, and feedback buttons.
    """
    meta = msg["meta"]
    st.markdown(msg["content"])

    if meta.get("disclaimer"):
        st.caption(f"⚠️ {meta['disclaimer']}")

    if meta.get("specialists"):
        _render_therapist_cards(meta["specialists"])

    # A clarifying-question message has nothing to show transparency or feedback about — it's
    # just the Intake Agent asking something back, not a grounded answer to rate.
    if meta.get("agent") == "intake":
        return

    # Collapsed by default — IMPLEMENTATION_PLAN.md's own Day 5 risk note calls this out
    # explicitly ("consuming screen real estate — collapse by default").
    with st.expander("How did I arrive at this?"):
        st.markdown(f"**Agent:** {meta.get('agent', 'n/a')}")
        critic = meta.get("critic")
        if critic:
            st.markdown(f"**Safety Critic verdict:** {critic['verdict']}")
            st.json(critic["checks"])
            if critic.get("reason"):
                st.markdown(f"**Flag reason:** {critic['reason']}")
            if meta.get("flagged_original_response"):
                st.markdown("**Suppressed response (what the agent actually generated):**")
                st.markdown(f"> {meta['flagged_original_response']}")
        chunks = meta.get("retrieved_chunks") or []
        if chunks:
            st.markdown("**Retrieved knowledge base chunks:**")
            for chunk in chunks:
                st.markdown(f"- {chunk}")
        if meta.get("specialists") is not None:
            st.markdown(f"**Tool call:** `directory_lookup` → {len(meta['specialists'])} result(s)")

    # message_id makes every widget key below unique across reruns. Without it, two assistant
    # messages would both try to register a button with the literal key "up", and Streamlit
    # raises a DuplicateWidgetID error the moment that happens.
    message_id = msg["message_id"]
    already_given = st.session_state.feedback_given.get(message_id)

    if already_given:
        st.caption(f"Feedback recorded: {'👍' if already_given == 'up' else '👎'} — thank you.")
        return

    col_up, col_down, _ = st.columns([1, 1, 8])
    with col_up:
        if st.button("👍", key=f"up_{message_id}"):
            _post_feedback(meta.get("run_ids", []), msg.get("query", ""), msg["content"], 1.0)
            st.session_state.feedback_given[message_id] = "up"
            st.rerun()
    with col_down:
        if st.button("👎", key=f"down_{message_id}"):
            # Only reveal the comment box — don't post feedback yet. Posting happens on
            # explicit "Submit feedback" below, so a 👎 click isn't itself the final feedback
            # (the parent might want to add a reason first).
            st.session_state.show_comment_for = message_id
            st.rerun()

    if st.session_state.show_comment_for == message_id:
        comment = st.text_input("What went wrong? (optional)", key=f"comment_{message_id}")
        if st.button("Submit feedback", key=f"submit_{message_id}"):
            _post_feedback(meta.get("run_ids", []), msg.get("query", ""), msg["content"], 0.0, comment)
            st.session_state.feedback_given[message_id] = "down"
            st.session_state.show_comment_for = None
            st.rerun()


def _render_sidebar() -> None:
    with st.sidebar:
        st.markdown(f"Logged in as **{st.session_state.auth.username}**")
        if st.button("Log out"):
            # .clear() wipes EVERY key, not just "auth" -- deliberately, so the next person to
            # use this browser tab (or this same parent logging back in) starts from a completely
            # blank session instead of still seeing the previous account's chat history or child
            # context sitting in st.session_state for even one rerun. _init_session_state() at
            # the top of the next run from scratch (auth included) once this fires st.rerun().
            st.session_state.clear()
            st.rerun()

        st.markdown("## 🧭 NorthStar")
        st.markdown(
            '<div class="safety-banner">NorthStar is a <strong>navigator, not a clinician</strong>. '
            "It never diagnoses or prescribes treatment — always consult a qualified specialist "
            "for decisions about your child.</div>",
            unsafe_allow_html=True,
        )

        # "Child profile," not "Child context" -- as of this chunk these two fields are no
        # longer session-only (the loading block right after the consent gate above already
        # pre-filled daily_support_state from the database for this login), so in practice a
        # parent only touches this form once, the first time, or when something actually
        # changes (a new diagnosis, a birthday) -- not every single session like before.
        with st.expander("👤 Child profile", expanded=True):
            # saved_* is the LAST KNOWN SAVED state (loaded from DB at login, or updated right
            # after a successful save below) -- comparing the text boxes' current values against
            # this is how "has anything actually changed since the last save" gets decided, not
            # a separate flag to keep in sync by hand.
            saved_condition = st.session_state.daily_support_state.get("condition", "")
            saved_age_range = st.session_state.daily_support_state.get("age_range", "")
            has_saved_profile = bool(saved_condition or saved_age_range)

            condition = st.text_input("Condition", value=saved_condition)
            age_range = st.text_input("Age range", value=saved_age_range)
            is_unchanged = condition == saved_condition and age_range == saved_age_range

            # Disabled exactly when there's nothing new TO save -- a profile already exists and
            # the boxes still match it. The moment a parent edits either field, is_unchanged goes
            # False and the button re-enables on its own; nothing has to explicitly "unlock" it.
            if st.button("Save profile", disabled=has_saved_profile and is_unchanged):
                # Write to the database FIRST, session_state SECOND -- if save_child_profile()
                # were to raise, we don't want session_state claiming a save happened that the
                # database doesn't actually have.
                save_child_profile(st.session_state.auth.parent_id, condition, age_range)
                st.session_state.daily_support_state["condition"] = condition
                st.session_state.daily_support_state["age_range"] = age_range
                # Rerun instead of a one-off st.success() toast -- the persistent caption below
                # (driven by has_saved_profile/is_unchanged, now both true) takes over that job
                # and keeps showing it on every later rerun too, not just this one.
                st.rerun()

            if has_saved_profile and is_unchanged:
                st.caption("✅ Profile saved — this will be remembered next time you log in.")
            elif has_saved_profile and not is_unchanged:
                st.caption("You have unsaved changes.")

        st.markdown("### 🎯 Daily support")
        st.caption("Uses the child context above, if you've set it.")
        # Each button sends a templated query carrying the implied current_need — the Intake
        # Agent (agents/intake.py) still parses it for condition/age confirmation, but the need
        # itself is unambiguous here because the parent picked it directly, rather than NorthStar
        # having to infer "calm down" == regulation from free text the way the CLI tests did.
        need_queries = {
            "🧘 Help them calm down": "My child needs help calming down and regulating right now.",
            "🎲 Something fun to do": "My child wants to play and engage with something fun right now.",
            "💡 General idea": "Suggest an activity for my child right now.",
        }
        for label, templated_query in need_queries.items():
            if st.button(label, key=f"daily_{label}"):
                _submit_daily_support(templated_query)

        st.markdown("### ⚡ Quick questions")
        quick_queries = [
            "What is autism?",
            "What therapies help with ADHD, and are there specialists in Mumbai?",
            "What does sensory processing disorder mean?",
        ]
        for q in quick_queries:
            if st.button(q, key=f"quick_{q}"):
                # Explicitly clicking a Quick Question cancels any pending Daily Support
                # clarification — otherwise the NEXT chat_input message would still get routed
                # back to Daily Support even though the parent has clearly moved on.
                st.session_state.awaiting_daily_support_reply = False
                _submit_ask_northstar(q)

        # Minimal HITL review queue — a parent can see what the Safety Critic suppressed from
        # THEIR OWN conversations and why. This is a self-review queue, not a staff/clinician
        # review tool across every account (that needs a separate reviewer role, which doesn't
        # exist yet — see db.py's critic_reviews comment and BUILD_LOG.md for the deferred gap).
        with st.expander("🚩 Review queue"):
            flagged = get_flagged_reviews(st.session_state.auth.parent_id)
            if not flagged:
                st.caption("Nothing waiting on review.")
            for review in flagged:
                st.markdown(f"**Flagged at {review.created_at}**")
                st.caption(review.reason or "No reason given.")
                if review.flagged_response_text:
                    st.markdown(f"> {review.flagged_response_text}")
                st.json(review.checks)
                if st.button("Mark reviewed", key=f"mark_reviewed_{review.id}"):
                    mark_reviewed(review.id)
                    st.rerun()
                st.divider()

        with st.expander("🔍 Observability"):
            st.caption(f"{len(st.session_state.messages)} messages this session.")
            st.caption("Full per-agent traces: see the 'northstar' project in LangSmith.")


def _render_main_panel() -> None:
    st.title("🧭 NorthStar")
    st.caption("A navigator for parents of neurodivergent children — not a clinician.")

    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            if msg["role"] == "assistant":
                _render_assistant_message(msg)
            else:
                st.markdown(msg["content"])

    # Placeholder text changes as a visible cue for which workflow the next message goes to —
    # otherwise there's no way to tell, since it's the same input box either way.
    placeholder = (
        "Answer the question above..."
        if st.session_state.awaiting_daily_support_reply
        else "Ask NorthStar a question..."
    )
    query = st.chat_input(placeholder)
    if query:
        if st.session_state.awaiting_daily_support_reply:
            _submit_daily_support(query)
        else:
            _submit_ask_northstar(query)
        # Explicit rerun: chat_input is called AFTER the history loop above in this same
        # script pass, so appending here is too late for that loop to have shown it already.
        # Streamlit would naturally rerun on the next interaction anyway, but calling it here
        # directly shows the new messages immediately instead of waiting on that.
        st.rerun()


# Sidebar renders FIRST so that a quick-question or daily-support button click (handled inside
# _render_sidebar) has already appended its messages before _render_main_panel's history loop
# runs in this SAME script pass — giving immediate same-pass visual feedback for those buttons,
# the same way the st.rerun() call above does explicitly for chat_input.
_render_sidebar()
_render_main_panel()
