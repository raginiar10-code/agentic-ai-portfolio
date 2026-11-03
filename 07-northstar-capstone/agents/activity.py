from __future__ import annotations

import logging

from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

from kb.retrieve import retrieve_from_activities

logger = logging.getLogger(__name__)

_MODEL = "gpt-4o-mini"
_TOP_K = 2

_SYSTEM_PROMPT = """You are the Activity Agent for NorthStar, a navigator — not a clinician — for \
parents of neurodivergent children. Below are candidate activities already matched to the \
current need. Your ONLY job is to pick the single best-fitting one for this specific child — \
NOT to rewrite its materials, steps, or rationale, which are already correct as given.

Child's condition: {condition}
Child's age range: {age_range}
Current need: {current_need}

Rules:
- Return activity_name EXACTLY as it appears below (copied verbatim, same spelling and \
capitalization) — do not paraphrase it, shorten it, or invent a new one.
- If NONE of the candidates below are actually a good fit for this condition/age/need, return \
the literal string "NONE_FIT" instead of a name, rather than forcing a bad match — this is a \
real possibility the Activities KB may not cover every combination.
- Never use diagnostic language.
- Always include a brief disclaimer that this is a general suggestion, not therapy, and that a \
qualified specialist can help tailor activities further.

Candidate activities:
{context}"""


class _ActivityLLMOutput(BaseModel):
    """What the LLM produces: a SELECTION, not a re-generation of content that's already correct.

    Earlier this field also made the model re-generate materials_needed/steps/why_it_helps/
    duration_minutes from scratch, copying them out of the flattened prose context into
    structured fields. That turned out to be genuinely unreliable in practice — found via live
    testing: for a "Slow Swing Time" suggestion with "Li Bhoj syndrome" as the condition, the
    model correctly picked the activity but returned materials_needed=[] even though "Materials:
    Swing (playground or indoor sensory swing)" was right there in its own context. Confirmed
    reproducible and NOT specific to that one condition name (varying the condition name with the
    exact same context sometimes dropped the field, sometimes didn't) -- genuine small-model
    unreliability on a multi-field extraction task, not a deterministic logic bug a prompt tweak
    would reliably fix. The actual fix is architectural: those fields are already present,
    correct, and structured in kb/activities.json itself (see suggest_activity() below) -- there
    was never a need to ask an LLM to retype them from prose and risk it getting one wrong.
    """

    activity_name: str = Field(
        description="EXACTLY the name of the single best-fitting candidate above, copied "
        "verbatim, or the literal string 'NONE_FIT' if none of them are a good match."
    )
    disclaimer: str


class ActivityOutput(BaseModel):
    """A grounded activity suggestion, with retrieval provenance for transparency."""

    activity_name: str
    materials_needed: list[str]
    steps: list[str]
    why_it_helps: str
    duration_minutes: int
    retrieved_chunks: list[str] = Field(description="Human-readable text of the KB entries used as context.")
    disclaimer: str


# No method="function_calling" here — _ActivityLLMOutput has no open-ended dict field, so the
# stricter default mode (which guarantees every field is present) applies safely. See
# agents/intake.py's identical comment for why this matters: function_calling mode let a
# required boolean field get silently dropped once during Day 4 testing.
_llm = ChatOpenAI(model=_MODEL, temperature=0).with_structured_output(_ActivityLLMOutput)


def _format_entry(entry: dict) -> str:
    """Render an activities.json entry as plain text for LLM context and transparency display."""
    return (
        f"{entry['name']} (age {entry['age_range']}, need: {entry['need']}). "
        f"Materials: {', '.join(entry.get('materials_needed', []))}. "
        f"Steps: {' '.join(entry.get('steps', []))} "
        f"Why it helps: {entry.get('why_it_helps', '')} "
        f"Duration: {entry.get('duration_minutes', '')} minutes."
    )


def _no_fit_fallback(current_need: str, retrieved_chunks: list[str], disclaimer: str) -> ActivityOutput:
    """The honest, deterministic answer for "nothing in the KB actually fits" -- shared by both
    cases that need it: zero entries retrieved at all, and the model judging (via the NONE_FIT
    sentinel) that the retrieved candidates exist but aren't a good match. Factored out so the
    exact same honest wording is used either way, rather than drifting apart over time.
    """
    return ActivityOutput(
        activity_name="No specific activity found",
        materials_needed=[],
        steps=[],
        why_it_helps=(
            f"We don't have a specific activity in our knowledge base for the "
            f"'{current_need}' need yet. Try asking for something calming (regulation) or "
            f"something playful (engagement) instead, or consult an occupational therapist "
            f"for a suggestion tailored to your child."
        ),
        duration_minutes=0,
        retrieved_chunks=retrieved_chunks,
        disclaimer=disclaimer,
    )


def suggest_activity(condition: str, age_range: str, current_need: str) -> ActivityOutput:
    """Suggest one grounded activity, given completed intake context (condition/age/need)."""
    logger.info(f"Activity Agent: condition={condition!r} age_range={age_range!r} need={current_need!r}")

    # Build a synthetic query from the known fields — unlike Info/Pathway, this agent doesn't
    # see the parent's raw free-text message directly (Intake already distilled it down to
    # condition/age_range/current_need), so we construct a query that captures the same intent
    # for semantic retrieval. need=current_need applies the Day 4 filter added to
    # kb/retrieve.py: only activities tagged with this exact need are even considered, before
    # any similarity ranking by condition happens.
    query = f"{current_need} activity for a child with {condition}, age {age_range}"
    entries = retrieve_from_activities(query, top_k=_TOP_K, need=current_need)
    retrieved_chunks = [_format_entry(entry) for entry in entries]

    if not entries:
        # Deterministic honest fallback — skip the LLM call entirely rather than trusting it to
        # "be honest" when handed zero context. Real bug found via live testing: given nothing
        # to ground on, the model fabricated a whole activity ("Creative Storytelling") instead
        # of following its own "if nothing fits, say so" instruction below. With truly zero
        # retrieved entries there's no judgment call left to make — this can currently happen
        # for current_need="other" specifically, since no activities.json entry is tagged
        # "other" (every entry is engagement or regulation) — so Python states the honest
        # answer directly rather than hoping the model declines gracefully on its own.
        logger.warning(f"Activity Agent: no KB entries matched need={current_need!r} condition={condition!r}")
        return _no_fit_fallback(
            current_need,
            [],
            "This is general guidance, not therapy. A qualified specialist can help tailor activities for your child.",
        )

    context = "\n\n".join(retrieved_chunks)
    prompt = _SYSTEM_PROMPT.format(condition=condition, age_range=age_range, current_need=current_need, context=context)
    result = _llm.invoke([("system", prompt)])

    if result.activity_name == "NONE_FIT":
        logger.info(f"Activity Agent: model judged no candidate a good fit for condition={condition!r} need={current_need!r}")
        return _no_fit_fallback(current_need, retrieved_chunks, result.disclaimer)

    # The model's job was SELECTION only (see _ActivityLLMOutput's docstring for why) -- the
    # actual materials/steps/why_it_helps/duration come straight from the matched KB entry
    # itself, never regenerated. `entries` holds the raw kb/activities.json dicts this was
    # retrieved from, so this is an exact-name lookup against real data, not a guess.
    matched_entry = next((e for e in entries if e["name"] == result.activity_name), None)
    if matched_entry is None:
        # The model didn't return NONE_FIT but also didn't return an exact candidate name
        # (a paraphrase, a typo, an invented name) -- fail closed to the top-ranked retrieved
        # candidate rather than either crashing or inventing content to match an unrecognized
        # name. Logged loudly since this means the model didn't follow the "copy verbatim"
        # instruction and is worth noticing if it happens often.
        logger.warning(f"Activity Agent: model returned unrecognized activity_name {result.activity_name!r}; using top candidate instead")
        matched_entry = entries[0]

    logger.info(f"Activity Agent: suggested {matched_entry['name']!r}")
    return ActivityOutput(
        activity_name=matched_entry["name"],
        materials_needed=matched_entry.get("materials_needed", []),
        steps=matched_entry.get("steps", []),
        why_it_helps=matched_entry.get("why_it_helps", ""),
        duration_minutes=matched_entry.get("duration_minutes", 0),
        retrieved_chunks=retrieved_chunks,
        disclaimer=result.disclaimer,
    )
