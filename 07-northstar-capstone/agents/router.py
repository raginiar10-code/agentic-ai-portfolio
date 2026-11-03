from __future__ import annotations

import logging
from typing import Literal

from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# Cheapest model — classification is a simple task, no need for gpt-4o here.
_MODEL = "gpt-4o-mini"

# Few-shot examples are baked directly into the prompt (not loaded from a file) because the
# Router is the one agent where misclassification derails everything downstream — info vs.
# pathway vs. out_of_scope decides which agent runs next. IMPLEMENTATION_PLAN.md flagged
# "Router misclassifying" as a Day 2 risk, so examples are here specifically to reduce that.
_SYSTEM_PROMPT = """You are the Router Agent for NorthStar, a navigator — not a clinician — for \
parents of neurodivergent children. Your only job is to classify the parent's message and \
extract entities. You never answer the question yourself.

Classify the message into exactly one intent:
- "info": asking to understand a condition, term, or concept.
  Example: "What is autism?" -> info, entities: {"condition": "autism"}
  Example: "What does oral-motor mean?" -> info, entities: {}
- "pathway": asking about therapy categories and/or specialists for a specific condition.
  Example: "My child has ADHD. What therapies should I look into, and are there specialists \
in Mumbai?" -> pathway, entities: {"condition": "adhd", "city": "Mumbai"}
  Example: "What kind of therapy helps with apraxia?" -> pathway, entities: {"condition": "apraxia"}
- "out_of_scope": anything a navigator should not answer directly — asking for a diagnosis \
("does my child have autism based on these symptoms"), asking for a specific treatment plan \
or medication dosing, crisis/self-harm content, or anything unrelated to child neurodevelopment.
  Example: "Does my child have autism? Here are the symptoms..." -> out_of_scope
  Example: "What's a good stock to invest in?" -> out_of_scope

Extract only entities actually mentioned (condition, city). Omit keys that weren't mentioned \
rather than guessing. Never diagnose or suggest a diagnosis — classify only.

If the CURRENT message is a follow-up that doesn't repeat a condition already established \
earlier in the conversation below (e.g. "find me therapists in bangalore" right after \
discussing ADHD), still extract that earlier condition into extracted_entities — a follow-up \
continuing the same topic should carry it forward, not lose it just because this one message \
doesn't repeat it. Only do this when the follow-up is CLEARLY continuing the same topic; a \
genuinely new, unrelated question should not inherit an old condition just because one was \
mentioned earlier."""


class RouterOutput(BaseModel):
    """Classifies a parent's message as info, pathway, or out_of_scope, with extracted entities."""

    # Literal (not plain str) means Pydantic will reject any value that isn't exactly one of
    # these three strings — if the model ever returns a typo'd intent, this catches it immediately
    # instead of letting a bad value silently flow into main.py's routing logic.
    intent: Literal["info", "pathway", "out_of_scope"]
    reasoning: str = Field(description="One sentence explaining the classification.")

    # dict[str, str] rather than a plain dict: OpenAI's structured-output feature needs a
    # concrete shape to validate against, and a bare "dict" has no defined shape. This still
    # matches ARCHITECTURE.md's intent (condition/city key-value pairs), just typed concretely.
    # default_factory=dict: a safety net for function_calling mode's leniency (it can let the
    # model omit a field outright — see agents/intake.py for a concrete case of this happening).
    # If that happens here, Pydantic falls back to an empty dict instead of crashing the whole
    # call — an empty entities dict is recoverable (downstream agents just get no city/condition
    # hint), unlike a hard crash.
    extracted_entities: dict[str, str] = Field(
        default_factory=dict,
        description="Entities mentioned in the message, e.g. {'condition': 'autism', 'city': 'Bangalore'}. "
        "Empty object if none were mentioned.",
    )


# .with_structured_output(...) makes the model return a validated RouterOutput object instead
# of free text. method="function_calling" (rather than the newer default "strict json_schema"
# mode) is required here because that default mode can't represent an open-ended dict type at
# all — only "function_calling" tolerates it. temperature=0 keeps classification deterministic:
# the same query should get the same intent every time, not creative variation.
# This client is built once at import time (not inside route()) since construction is relatively
# expensive and every call to route() should reuse the same configured client.
_llm = ChatOpenAI(model=_MODEL, temperature=0).with_structured_output(
    RouterOutput, method="function_calling"
)


def _format_history(history: list[dict] | None) -> str:
    """Render recent chat_messages rows (see chat_log.get_recent_messages) as plain "Parent: ..."
    / "NorthStar: ..." lines, oldest first, for the prompt. An explicit "(none...)" string for
    the empty case, rather than blank text, so the model isn't left to guess whether an empty
    section means "this really is the first message" or "history failed to load silently."
    """
    if not history:
        return "(none — this is the first message in the conversation)"
    role_labels = {"user": "Parent", "assistant": "NorthStar"}
    return "\n".join(f"{role_labels.get(turn['role'], turn['role'])}: {turn['content']}" for turn in history)


def route(query: str, history: list[dict] | None = None) -> RouterOutput:
    """Classify a parent's query into an intent (info/pathway/out_of_scope) with extracted entities.

    `history` (optional, defaults to None/no history) is recent prior turns from THIS parent's
    Ask NorthStar conversation — see chat_log.get_recent_messages(). It exists specifically to
    fix a real, documented gap (ARCHITECTURE.md Phase 2, found via Day 5 testing): a follow-up
    like "find me therapists in bangalore" right after discussing ADHD used to lose that
    condition entirely, because the Router classified each message with zero awareness of what
    came before it. Every OTHER caller of route() (the CLI, eval.py's golden-set runner) keeps
    calling it with no history argument at all, which behaves identically to before this change —
    this is additive, not a breaking change to the one-shot classification path.
    """
    logger.info(f"Router: classifying query: {query!r}")
    history_text = _format_history(history)
    # Concatenation here, NOT _SYSTEM_PROMPT.format(...) -- the few-shot examples above contain
    # literal curly braces (e.g. {"condition": "autism"}), which str.format() would try to parse
    # as placeholders and crash on. Appending the history as a separate string sidesteps that
    # entirely, instead of having to escape every brace in the examples as {{ }}.
    prompt = (
        f"{_SYSTEM_PROMPT}\n\n"
        "Conversation so far in this session, oldest first (use it ONLY to resolve what a "
        "follow-up implicitly refers to — never to override what the CURRENT message is "
        "actually asking):\n"
        f"{history_text}"
    )
    # Two messages: "system" sets the agent's instructions/rules (now including history), "human"
    # is the actual parent message being classified. invoke() blocks until OpenAI responds.
    result = _llm.invoke([("system", prompt), ("human", query)])
    logger.info(f"Router: intent={result.intent} entities={result.extracted_entities}")
    return result
