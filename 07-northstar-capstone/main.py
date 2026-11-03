from __future__ import annotations

import logging
import sys

from dotenv import load_dotenv

# load_dotenv() reads .env (OPENAI_API_KEY, LANGSMITH_API_KEY, etc.) into environment variables.
# This MUST run before the `from agents... import ...` lines below, not just before run()/route()
# are called. Python executes a file's import statements top-to-bottom as it loads the file —
# and agents/router.py, info.py, safety_critic.py (and kb/retrieve.py underneath info.py) all
# build their OpenAI client at module level, i.e. the instant they're imported, not lazily inside
# a function. So if load_dotenv() appeared after these imports, the client construction would
# already have failed (missing API key) before load_dotenv() ever got a chance to run.
load_dotenv()

from agents.activity import suggest_activity  # noqa: E402
from agents.info import explain  # noqa: E402
from agents.intake import check_intake  # noqa: E402
from agents.pathway import format_specialists, get_pathway  # noqa: E402
from agents.router import route  # noqa: E402
from agents.safety_critic import review  # noqa: E402

# basicConfig() belongs here, in the application entry point — not inside agents/router.py or
# the other library-style modules. A library should just call logger.info(...)/logger.debug(...)
# and let whoever actually RUNS the program (this file) decide how/where those messages get
# displayed. level=logging.INFO means DEBUG-level messages (like retrieve.py's per-chunk match
# scores) are suppressed by default; change to logging.DEBUG here to see them.
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")


def _safety_fallback_response() -> str:
    """Shown INSTEAD OF a flagged response, so a parent never sees content the Safety Critic
    rejected as the primary answer. This is new as of a real bug found via live testing: the
    Critic was already correctly flagging bad outputs (e.g. a fabricated activity with no KB
    backing), but nothing acted on that flag — the flagged content still reached the chat UI
    unchanged, visible as the main response, with the flag itself only visible if you opened
    the collapsed transparency panel. ARCHITECTURE.md's diagrams show a full revision loop
    (flag -> agent retries with the reason -> re-check); this is the simpler version actually
    built given the sprint timeline — suppress and show a safe message, rather than retry. The
    original flagged content isn't discarded: each branch below that uses this also sets
    "flagged_original_response" in the result dict, so the transparency panel can still show it
    for demo/debugging purposes.
    """
    return (
        "I wasn't able to generate a response that met NorthStar's safety standards for this "
        "request. Please try rephrasing your question, or consult a qualified specialist directly."
    )


def _out_of_scope_response() -> str:
    """Fixed reply for out_of_scope intent — no LLM call needed, so no Safety Critic review needed either."""
    return (
        "I don't have information to help with that here. NorthStar is built to help with "
        "understanding your child's condition, exploring therapy categories, and finding "
        "specialists — for anything else, including diagnosis, please consult a qualified "
        "specialist."
    )


def _format_pathway_response(condition: str, categories: list[dict], specialists: list[dict] | None) -> str:
    """Flatten a Pathway result into one plain-text block — this is what the Safety Critic
    checks AND what a chat UI would actually display, so both main.py and app.py (Day 5) want
    the exact same text, not two slightly different renderings of the same data.
    """
    categories_text = "; ".join(f"{c['category']} ({c['rationale']})" for c in categories)
    if specialists:
        # Found via Chunk 8 testing: this used to omit s["specialization"] entirely, while
        # app.py's rich therapist cards (_render_therapist_cards) already show it. A parent
        # reading just this flattened text had no way to tell WHY a given specialist was
        # relevant — and the Safety Critic's is_correct check (correctly) started flagging that
        # same gap once it existed to check for it. Including specialization here brings this
        # flattened text up to the same completeness as the card UI already has.
        specialists_text = "; ".join(f"{s['name']} ({s['specialization']}, {s['area']}, {s['city']})" for s in specialists)
        # LEAD with specialists, not categories, when real matches exist — also found via
        # Chunk 8 testing. A follow-up like "find me therapists in bangalore" is asking
        # specifically for this; the categories-first phrasing below buries the actual answer
        # under an explanation the parent didn't ask for THIS turn, which the Critic's
        # is_correct check correctly reads as not directly addressing the question. This
        # specific code path (a follow-up resolving to a real specialist match) literally
        # couldn't happen before Chunk 8's memory fix, so this gap was never reachable until now.
        return (
            f"Specialists found for {condition}: {specialists_text}. "
            f"For context, commonly explored therapy categories: {categories_text}."
        )
    elif specialists == []:
        specialists_text = "No matching specialists found."
    else:
        specialists_text = "No city was given, so no specialist search was performed."
    return f"For {condition}, commonly explored therapy categories: {categories_text}. Specialists: {specialists_text}"


def _format_activity_response(activity_name: str, duration_minutes: int, materials: list[str], steps: list[str], why_it_helps: str) -> str:
    """Flatten an Activity result into one plain-text block — same reasoning as _format_pathway_response above."""
    return (
        f"Suggested activity: {activity_name} ({duration_minutes} min). "
        f"Materials: {', '.join(materials)}. "
        f"Steps: {' '.join(steps)} "
        f"Why it helps: {why_it_helps}"
    )


def run(query: str, history: list[dict] | None = None) -> dict:
    """Run one query through Router -> (Info Agent | Pathway Agent) -> Safety Critic.

    Returns a plain dict instead of printing, on purpose: this exact function is reused by
    BOTH the CLI (__main__ below, which prints the dict) AND Day 5's Streamlit app.py (which
    renders it as chat UI) — one pipeline, two different ways of showing the result, rather
    than app.py reimplementing this routing logic a second time.

    `history` (optional, defaults to None) is recent prior turns from this parent's Ask NorthStar
    conversation — see chat_log.get_recent_messages(). It's threaded straight through to
    route() and on into explain()/get_pathway() as a resolved condition_hint; see those
    functions' own docstrings for what problem this actually fixes. The CLI below and eval.py's
    golden-set runner both call run(query) with no history argument at all, which behaves
    identically to how this function worked before this parameter existed.

    Every returned dict has "intent" plus keys specific to that intent:
    - out_of_scope: {"intent", "response"}
    - info: {"intent", "response", "disclaimer", "retrieved_chunks", "sources", "critic"}
    - pathway: {"intent", "response", "disclaimer", "retrieved_chunks", "specialists", "critic"}
    """
    router_result = route(query, history=history)

    # Branch on the Router's classification. This if/elif/else chain IS the "hub-and-spoke"
    # pattern from ARCHITECTURE.md section 6 — the Router is the hub, each branch below is a spoke.
    if router_result.intent == "out_of_scope":
        return {"intent": "out_of_scope", "response": _out_of_scope_response()}

    if router_result.intent == "pathway":
        # Pass the city AND condition the Router already extracted straight through — see
        # agents/pathway.py for why we don't make the Pathway Agent re-derive either of these
        # itself from the raw query (condition especially: a follow-up like "find me therapists
        # in bangalore" may not mention the condition at all — see router.py's history-awareness).
        city = router_result.extracted_entities.get("city")
        condition_hint = router_result.extracted_entities.get("condition")
        pathway_result = get_pathway(query, city=city, condition_hint=condition_hint)
        response_text = _format_pathway_response(
            pathway_result.condition, pathway_result.recommended_therapy_categories, pathway_result.specialists_in_city
        )
        # context = everything that actually backs the response: the retrieved therapy KB
        # chunks AND the raw directory_lookup results (via the same format_specialists() helper
        # pathway.py itself uses internally). Passing this lets the Critic's in_scope check tell
        # "a real specialist from a real tool call" apart from "a name the model invented" — see
        # agents/safety_critic.py's review() docstring for the bug this fixes.
        context = (
            "\n\n".join(pathway_result.retrieved_chunks)
            + "\n\nSpecialist search results:\n"
            + format_specialists(pathway_result.specialists_in_city)
        )
        critic_result = review(query, response_text, pathway_result.disclaimer, context)
        if critic_result.verdict == "flag":
            return {
                "intent": "pathway",
                "response": _safety_fallback_response(),
                "disclaimer": None,
                "retrieved_chunks": pathway_result.retrieved_chunks,
                "specialists": None,
                "critic": critic_result,
                "flagged_original_response": response_text,
            }
        return {
            "intent": "pathway",
            "response": response_text,
            "disclaimer": pathway_result.disclaimer,
            "retrieved_chunks": pathway_result.retrieved_chunks,
            "specialists": pathway_result.specialists_in_city,
            "critic": critic_result,
        }

    # Only remaining possibility is intent == "info", since RouterOutput.intent is a Literal
    # restricted to exactly these three values — no other branch can occur here.
    condition_hint = router_result.extracted_entities.get("condition")
    info_result = explain(query, condition_hint=condition_hint)

    # The Critic gets response_text (what the parent actually reads: explanation + disclaimer)
    # SEPARATELY from context (the retrieved KB chunks that backed it) — response_text is what
    # gets checked, context is what it's checked against. See agents/safety_critic.py's
    # review() docstring for why context is required, not optional.
    context = "\n\n".join(info_result.retrieved_chunks)
    critic_result = review(query, info_result.explanation, info_result.disclaimer, context)
    if critic_result.verdict == "flag":
        return {
            "intent": "info",
            "response": _safety_fallback_response(),
            "disclaimer": None,
            "retrieved_chunks": info_result.retrieved_chunks,
            "sources": info_result.source_ids,
            "critic": critic_result,
            "flagged_original_response": info_result.explanation,
        }
    return {
        "intent": "info",
        "response": info_result.explanation,
        "disclaimer": info_result.disclaimer,
        "retrieved_chunks": info_result.retrieved_chunks,
        "sources": info_result.source_ids,
        "critic": critic_result,
    }


def run_daily_support(query: str, session_state: dict) -> tuple[dict, dict]:
    """Run one Workflow 2 (Daily Support) turn: Intake -> (Activity Agent -> Safety Critic, if ready).

    Unlike run() above, this does NOT go through the Router — ARCHITECTURE.md's diagram shows
    "Chat --> Intake" as a separate path from "Chat --> Router", since Daily Support isn't a
    question to classify, it's a standing request against whatever child context is already set.

    Returns (result, updated_session_state) — a tuple, not just the result dict, because the
    CALLER (app.py, eventually) needs to persist updated_session_state across turns itself
    (e.g. in st.session_state); this function has no memory of its own between calls. The
    result dict's "intent" is either "daily_support_clarify" (not enough context yet) or
    "daily_support_activity" (a grounded suggestion was produced).
    """
    intake_result = check_intake(query, session_state)
    updated_state = {
        "condition": intake_result.condition,
        "age_range": intake_result.age_range,
        "current_need": intake_result.current_need,
    }

    # This is the ARCHITECTURE.md section 7 "Agent -> Independent Action" pattern: when context
    # is incomplete, Intake hands control back here (clarifying_questions) instead of guessing
    # its way into running the Activity Agent on unreliable context.
    if not intake_result.ready_for_activity_agent:
        return {"intent": "daily_support_clarify", "clarifying_questions": intake_result.clarifying_questions}, updated_state

    activity_result = suggest_activity(
        condition=intake_result.condition,
        age_range=intake_result.age_range,
        current_need=intake_result.current_need,
    )
    response_text = _format_activity_response(
        activity_result.activity_name,
        activity_result.duration_minutes,
        activity_result.materials_needed,
        activity_result.steps,
        activity_result.why_it_helps,
    )
    context = "\n\n".join(activity_result.retrieved_chunks)
    critic_result = review(query, response_text, activity_result.disclaimer, context)
    if critic_result.verdict == "flag":
        result = {
            "intent": "daily_support_activity",
            "response": _safety_fallback_response(),
            "disclaimer": None,
            "retrieved_chunks": activity_result.retrieved_chunks,
            "current_need": intake_result.current_need,
            "activity_name": None,
            "duration_minutes": None,
            "materials_needed": None,
            "steps": None,
            "why_it_helps": None,
            "critic": critic_result,
            "flagged_original_response": response_text,
        }
        return result, updated_state
    result = {
        "intent": "daily_support_activity",
        "response": response_text,
        "disclaimer": activity_result.disclaimer,
        "retrieved_chunks": activity_result.retrieved_chunks,
        # current_need/materials_needed/steps/why_it_helps weren't in this dict before this
        # chunk -- app.py had no need for them beyond what _format_activity_response() already
        # flattens into "response" above. They're added now specifically so app.py's new chat
        # logging (see chat_log.py) can write the STRUCTURED version of this same outcome into
        # activity_log, not just the flattened display text into chat_messages -- see
        # db.py's activity_log comment for why that structure is worth keeping separately.
        "current_need": intake_result.current_need,
        "activity_name": activity_result.activity_name,
        "duration_minutes": activity_result.duration_minutes,
        "materials_needed": activity_result.materials_needed,
        "steps": activity_result.steps,
        "why_it_helps": activity_result.why_it_helps,
        "critic": critic_result,
    }
    return result, updated_state


def _print_result(query: str, result: dict) -> None:
    """CLI-only rendering of a run()/run_daily_support() result dict — kept separate from those
    functions themselves so app.py can render the exact same dicts completely differently
    (as chat bubbles, not print statements) without this function getting in the way.
    """
    print(f"\n--- Query: {query!r} ---")
    print(f"Intent: {result['intent']}")

    if result["intent"] == "daily_support_clarify":
        print("Clarifying questions needed:")
        for question in result["clarifying_questions"]:
            print(f"  - {question}")
        return

    print(f"Response: {result['response']}")
    if result.get("disclaimer"):
        print(f"Disclaimer: {result['disclaimer']}")
    critic_result = result.get("critic")
    if critic_result:
        print(f"Safety Critic verdict: {critic_result.verdict} | checks: {critic_result.checks}")
        if critic_result.verdict == "flag":
            print(f"Flag reason: {critic_result.reason}")
            print(f"Flagged original (suppressed): {result.get('flagged_original_response')}")


# This CLI entry point only drives Workflow 1 (run()) — Workflow 2 (run_daily_support()) is
# tested directly via Python calls instead (see the Day 4 BUILD_LOG entry for example calls).
# Not worth building multi-workflow CLI argument parsing for a code path that Day 5's Streamlit
# app replaces entirely in two days — that's effort spent on throwaway scaffolding.
#
# This block only runs when the file is executed directly (`python main.py ...`), not when
# it's imported by something else (e.g. app.py importing `run`/`run_daily_support`). sys.argv
# is the list of command-line arguments; sys.argv[0] is always the script name itself, so the
# actual query the user typed is sys.argv[1].
if __name__ == "__main__":
    if len(sys.argv) < 2:
        print('Usage: python main.py "your question here"')
        sys.exit(1)
    _print_result(sys.argv[1], run(sys.argv[1]))
