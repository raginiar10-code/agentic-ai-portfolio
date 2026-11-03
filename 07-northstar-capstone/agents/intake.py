from __future__ import annotations

import logging
from typing import Literal

from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

_MODEL = "gpt-4o-mini"

# {session_state} gets filled with whatever's already known from earlier in the session (passed
# in as a plain dict — see check_intake() below). The model's job is to MERGE that with anything
# new in the current message, not to start from scratch each time.
_SYSTEM_PROMPT = """You are the Intake Agent for NorthStar, a navigator — not a clinician — for \
parents of neurodivergent children. Workflow 2 (Daily Support) needs three pieces of context \
before the Activity Agent can suggest anything: condition, age_range, and current_need \
(engagement = wants to play/connect; regulation = needs to calm down or re-center; other = \
anything else).

Known session context so far: {session_state}

Your job, given the parent's current message below:
1. CRITICAL — preserve what's already known: for EACH of condition, age_range, and \
current_need, if "Known session context" already holds a real value for it (anything other \
than the placeholder "(not yet known)"), you MUST carry that exact value forward unchanged, \
UNLESS the parent's new message explicitly gives a different value for that SAME field. A \
message that only answers one or two fields does NOT mean the others should be re-guessed —
copy them forward as-is. This was a real bug: a reply that only answered age/condition once \
wrongly reset an already-known current_need from "regulation" to "other" — never do that.
2. Fill in whatever is still missing using the parent's current message. If something is \
genuinely still unknown after that, use the exact placeholder "(not yet known)" for \
condition/age_range, or "other" for current_need (its placeholder doubles as a real category, \
so lean on clarifying_questions + ready_for_activity_agent below to signal it's not confirmed) \
— never leave a field blank — and list that exact thing as still missing in clarifying_questions.
3. If anything is still "(not yet known)" or unconfirmed after merging, set \
ready_for_activity_agent to false and write 1-2 short, warm clarifying questions covering ONLY \
the missing pieces — don't re-ask about anything already known.
4. If condition, age_range, AND current_need are all clearly known after merging, set \
ready_for_activity_agent to true and clarifying_questions to an empty list.

Never diagnose. Never recommend a specific therapy here — that's Workflow 1's job, not yours."""


class IntakeOutput(BaseModel):
    """Session context (condition/age/need) merged from prior state + the current message, plus
    whether enough is known for the Activity Agent to run.
    """

    condition: str
    age_range: str
    # Literal restricts this to exactly these three values — even when the model is genuinely
    # unsure, it still has to pick one (see rule 2 in the prompt above: use "other" rather than
    # leaving it blank). Whether that guess is safe to act on is what ready_for_activity_agent
    # and clarifying_questions are for, not this field.
    current_need: Literal["engagement", "regulation", "other"]
    clarifying_questions: list[str] = Field(
        description="1-2 short questions for whatever is still missing. Empty list if ready_for_activity_agent is true."
    )
    ready_for_activity_agent: bool


# No method="function_calling" here, unlike router.py/safety_critic.py — IntakeOutput has no
# open-ended dict field, so it doesn't hit the strict-schema limitation that forced those two
# into the more lenient mode. Using OpenAI's default STRICT mode here instead is actually the
# safer choice: strict mode guarantees every field is present in every response. (Day 4 found
# this out the hard way — function_calling mode let the model omit ready_for_activity_agent
# entirely once, crashing Pydantic validation. Strict mode can't do that.)
_llm = ChatOpenAI(model=_MODEL, temperature=0).with_structured_output(IntakeOutput)


def check_intake(query: str, session_state: dict) -> IntakeOutput:
    """Merge session_state with the parent's current message and decide if there's enough
    context for the Activity Agent to run.

    session_state is a plain dict simulating what Streamlit's st.session_state will hold once
    the real UI exists (Day 5) — e.g. {"condition": "autism", "age_range": "5-7"} with
    current_need still missing. Passing {} simulates a brand-new session with nothing set yet.
    This is the "Agent -> Independent Action" pattern from ARCHITECTURE.md section 7: when
    context is incomplete, this function hands control back to the UI (via clarifying_questions)
    instead of guessing its way into the Activity Agent.
    """
    logger.info(f"Intake Agent: query={query!r} session_state={session_state}")
    # str(session_state) turns the dict into Python's own repr, e.g. "{'condition': 'autism'}" —
    # readable enough for the model to parse as context, no need for a stricter format here.
    prompt = _SYSTEM_PROMPT.format(session_state=session_state or "none set yet")
    result = _llm.invoke([("system", prompt), ("human", query)])
    logger.info(
        f"Intake Agent: ready={result.ready_for_activity_agent} condition={result.condition!r} "
        f"age_range={result.age_range!r} need={result.current_need}"
    )
    return result
