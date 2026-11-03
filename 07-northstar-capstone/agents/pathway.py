from __future__ import annotations

import logging

from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

from kb.retrieve import retrieve_from_therapies
from tools.directory_lookup import directory_lookup

logger = logging.getLogger(__name__)

# GPT-4o, not mini — per ARCHITECTURE.md, Pathway does multi-step reasoning + tool
# orchestration, which the model-routing table flags as needing the stronger model.
_MODEL = "gpt-4o"
_TOP_K = 3

# Phase 1 prompt: deciding WHETHER to call the directory_lookup tool. We already know from the
# Router whether a city was mentioned (city param below) — rather than making the model re-derive
# that from the raw query itself, we tell it directly. This is the fix for the exact risk
# IMPLEMENTATION_PLAN.md flagged for Day 3: "Pathway Agent might call the tool even when city
# isn't specified — tighten the prompt to require city before calling."
_TOOL_DECISION_PROMPT = """You are the Pathway Agent for NorthStar, a navigator — not a clinician \
— for parents of neurodivergent children. You help parents understand which therapy categories \
are commonly explored for their child's condition, and optionally find specialists in their city.

Therapy KB excerpts:
{context}

City mentioned by the parent: {city}

Condition under discussion, if known from earlier in this conversation (helps interpret a \
follow-up like "find me therapists in bangalore" that doesn't repeat the condition — ignore \
this if it conflicts with what the CURRENT message is actually asking): {condition_hint}

If (and only if) a real city is given above, call the directory_lookup tool with the therapy \
category name(s) most relevant to the parent's question and that exact city. If no city is \
given, do NOT call any tool — just reason about therapy categories instead."""

# Phase 2 prompt: synthesizing the final structured answer, now that we know (from phase 1)
# whether specialists were found. This is a SEPARATE call because with_structured_output's
# function_calling method works by forcing the model to call one specific "tool" (the output
# schema) — that's incompatible with also offering directory_lookup as an optional real tool
# in the same call. Hence two calls instead of one.
_SYNTHESIS_PROMPT = """You are the Pathway Agent for NorthStar, a navigator — not a clinician — \
for parents of neurodivergent children. Using ONLY the information below, produce:
- condition: the condition being discussed.
- recommended_therapy_categories: therapy categories relevant to this condition, each with a \
one-sentence rationale. Suggest CATEGORIES only — never a specific treatment plan or dosage.
- disclaimer: a mandatory reminder that these are categories commonly explored, not a treatment \
plan, and that a qualified specialist should be consulted before acting on any of this.

Never use diagnostic language. Never claim a therapy "will cure", is "guaranteed", or similar.

Condition under discussion, if known from earlier in this conversation: {condition_hint}

Therapy KB excerpts:
{context}

Specialist search results: {specialist_results}"""


class _CategoryRationale(BaseModel):
    """One therapy category with why it's relevant — the LLM-facing shape of each list item."""

    category: str
    rationale: str


class _PathwayLLMOutput(BaseModel):
    """What the LLM produces in the synthesis phase; specialists_in_city is added afterward in code."""

    condition: str
    recommended_therapy_categories: list[_CategoryRationale]
    disclaimer: str


class PathwayOutput(BaseModel):
    """Therapy categories + (optional) specialist matches for a condition, with retrieval provenance."""

    condition: str
    recommended_therapy_categories: list[dict]
    specialists_in_city: list[dict] | None
    retrieved_chunks: list[str]
    disclaimer: str


# Two separate clients, because the two phases need incompatible configurations on the model:
# _llm_with_tool offers directory_lookup as something the model MAY choose to call (or not).
# _llm_structured forces a specific output shape and has no knowledge of any tool at all.
# _llm_structured uses the stricter DEFAULT mode (no method="function_calling") since
# _PathwayLLMOutput — unlike RouterOutput/SafetyCriticOutput — has no open-ended dict field
# (recommended_therapy_categories is a list of a fully-typed nested model). Strict mode
# guarantees every field is present every time; see agents/intake.py for the bug this avoids.
_llm_with_tool = ChatOpenAI(model=_MODEL, temperature=0).bind_tools([directory_lookup])
_llm_structured = ChatOpenAI(model=_MODEL, temperature=0).with_structured_output(_PathwayLLMOutput)


def format_specialists(specialists: list[dict] | None) -> str:
    """Render tool-call results as plain text — shared by this module's own synthesis prompt
    AND by main.py, which needs the exact same text to build the Safety Critic's grounding
    context. Not prefixed with "_" (unlike this file's other helpers) because it's meant to be
    imported elsewhere, not just used internally.

    Three distinct cases, not two: None means "the tool was never called" (no city given),
    which is different from [] meaning "the tool was called but found nobody" — conflating
    them would make a real "no matches in this city" case look identical to "we didn't even
    try," which matters for an honest response.
    """
    if specialists is None:
        return "No specialist search was performed (no city was provided)."
    if not specialists:
        return "No matching specialists were found for that category/city combination."
    # Must include every field the FINAL response text can reference, not just some of them.
    # This was a real bug, found via Chunk 8 testing (a follow-up query that successfully found
    # a real specialist got flagged anyway): main.py's _format_pathway_response() puts s['area']
    # (e.g. "HSR Layout") into what the parent actually sees and what the Safety Critic checks,
    # but this function — which builds the Critic's GROUNDING context for that same check — was
    # only including s['specialization'], never s['area']. So a 100% real, tool-returned area
    # had nothing in the grounding context to verify it against, and the Critic's in_scope check
    # occasionally (LLM judgment, not deterministic) flagged genuinely-grounded data as
    # unverifiable. Including area here closes that gap.
    return "\n".join(f"{s['name']} ({s['specialization']}, {s['area']}, {s['city']})" for s in specialists)


def _format_entry(entry: dict) -> str:
    """Render a therapies.json entry as plain text for LLM context and transparency display."""
    explored = "; ".join(f"{c['condition']} ({c['rationale']})" for c in entry.get("commonly_explored_for", []))
    return (
        f"{entry['name']}. Addresses: {entry['addresses']} "
        f"Commonly explored for: {explored}. "
        f"Typical session format: {entry.get('typical_session_format', '')} "
        f"Honest limits: {entry.get('honest_limits', '')}"
    )


def get_pathway(query: str, city: str | None = None, condition_hint: str | None = None) -> PathwayOutput:
    """Answer a parent's pathway-intent question: therapy categories, plus specialists if a city was given.

    `condition_hint` (optional) is the condition the Router resolved for this turn, possibly
    carried forward from earlier in the conversation rather than the current message (see
    agents/router.py's history-awareness). This is specifically the fix for a real, documented
    bug (ARCHITECTURE.md Phase 2, found via Day 5 testing): "find me therapists in bangalore"
    right after discussing ADHD used to retrieve on the raw query text alone — which doesn't
    contain the word "ADHD" anywhere — so KB retrieval had nothing to actually match against.
    """
    logger.info(f"Pathway Agent: answering query: {query!r} city={city!r} condition_hint={condition_hint!r}")

    # Same reasoning as agents/info.py's explain(): retrieval_query is used ONLY for the
    # embedding search below, never sent to the LLM in place of the parent's actual question.
    retrieval_query = f"{condition_hint} {query}" if condition_hint else query
    entries = retrieve_from_therapies(retrieval_query, top_k=_TOP_K)
    retrieved_chunks = [_format_entry(entry) for entry in entries]
    context = "\n\n".join(retrieved_chunks)

    # --- Phase 1: let the model decide whether to call directory_lookup ---
    city_hint = city if city else "None provided — do not call the tool."
    decision_prompt = _TOOL_DECISION_PROMPT.format(
        context=context, city=city_hint, condition_hint=condition_hint or "(none)"
    )
    ai_msg = _llm_with_tool.invoke([("system", decision_prompt), ("human", query)])

    # ai_msg.tool_calls is a list (empty if the model chose not to call anything). Each entry
    # is a dict like {"name": "directory_lookup", "args": {...}, "id": "..."}. We only expect
    # at most one call in this flow, so we just take the first if present.
    specialists: list[dict] | None = None
    if ai_msg.tool_calls:
        call = ai_msg.tool_calls[0]
        logger.info(f"Pathway Agent: model requested tool call: {call['name']}({call['args']})")
        try:
            specialists = directory_lookup.invoke(call["args"])
        except ValueError as exc:
            # The model is the one constructing these arguments, so a malformed call (e.g.
            # missing city despite our instructions) is a realistic failure mode — log it
            # loudly and degrade to "no specialists found" rather than crashing the whole
            # pipeline over one bad tool call.
            logger.warning(f"Pathway Agent: tool call failed validation: {exc}")
            specialists = []

    # --- Phase 2: synthesize the final structured answer, now knowing the specialist results ---
    specialist_results_text = format_specialists(specialists)
    synthesis_prompt = _SYNTHESIS_PROMPT.format(
        context=context, specialist_results=specialist_results_text, condition_hint=condition_hint or "(none)"
    )
    result = _llm_structured.invoke([("system", synthesis_prompt), ("human", query)])

    logger.info(f"Pathway Agent: condition={result.condition} specialists_found={len(specialists or [])}")
    return PathwayOutput(
        condition=result.condition,
        # [c.model_dump() for c in ...] converts each _CategoryRationale Pydantic object back
        # into a plain dict ({"category": ..., "rationale": ...}), matching ARCHITECTURE.md's
        # `list[dict]` shape for the public PathwayOutput.
        recommended_therapy_categories=[c.model_dump() for c in result.recommended_therapy_categories],
        specialists_in_city=specialists,
        retrieved_chunks=retrieved_chunks,
        disclaimer=result.disclaimer,
    )
