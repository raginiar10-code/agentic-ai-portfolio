# NorthStar

**A navigator — not a clinician — for parents of neurodivergent children.**

NorthStar is a multi-agent AI system that helps parents of children with autism, ADHD, apraxia, and other neurodevelopmental conditions understand what they're facing, explore therapy categories, find specialists, and get daily activity support — without ever diagnosing, prescribing, or guessing. It was built as the capstone project for applied agentic AI course.

**[Demo video](https://drive.google.com/file/d/1KSxrxj2WXw-xuPySGRgvOnTfTl3LHSPM/view?usp=sharing)**

Full product vision: [`PRESS_RELEASE_AND_FAQ.md`](PRESS_RELEASE_AND_FAQ.md) · Full technical design: [`ARCHITECTURE.md`](ARCHITECTURE.md) · Day-by-day build log: [`BUILD_LOG.md`](BUILD_LOG.md)

---

## Authorship — how this was built

I designed the architecture end-to-end: the 4-pillar system (two workflows + a shared Safety Critic + a feedback loop), the non-negotiable "navigator, not clinician" design principle, the guardrail specification, and the evaluation plan. I also directed the knowledge base scope and content — which conditions to cover, including a rare genetic syndrome I specifically wanted represented, and the overall structure of each knowledge base.

The implementation — the Python code for every agent, the LangChain/Streamlit scaffolding, the actual prose in the knowledge base entries, and the prompt-engineering iteration — was built using AI-assisted development: I directed the engineering and content decisions, the AI (Claude) wrote the code and drafted the content under that direction. Where I corrected or redirected the approach during the build, that's documented day-by-day in `BUILD_LOG.md`, including real bugs found through my own testing of the live app and how they were diagnosed and fixed.

The same split holds for the Phase 2 work (persistence, auth, consent, memory, HITL): I directed it from a real instructor review session's feedback, made the real design calls along the way (e.g. that `activity_log` needed to be a separate table holding structured data, not a duplicate of `chat_messages`; that the self-review-queue scope was the right line to draw rather than building a reviewer role; catching that the `is_correct` check needed to judge meaning, not wording), and reviewed every change in small chunks before moving to the next. Claude implemented it under that direction.

---

## What it does

**Ask NorthStar** (Workflow 1) — a chat interface for understanding conditions/terminology and exploring therapy categories + specialists:

- _"What is sensory processing disorder?"_ → grounded, plain-language explanation
- _"What therapies help with ADHD, and are there specialists in Bangalore?"_ → therapy categories with rationale, plus real directory matches if a city is given

**Daily Support** (Workflow 2) — contextual activity suggestions once a parent sets basic child context:

- _"My child needs help calming down right now"_ → one grounded, age-appropriate activity with materials, steps, and why it helps

Every response — from either workflow — passes through a shared Safety Critic before reaching the parent. If it fails any of five checks (no diagnostic language, has a disclaimer, no overpromising, stays grounded in what was actually retrieved, and — added after an instructor review — is a _correct_, meaning-accurate reading of that grounding), **the response is suppressed and replaced with a safe fallback message** — not just flagged and shown anyway.

---

## Phase 2 — built in response to instructor review feedback

A review session surfaced real gaps for anything beyond a one-off demo: no memory of a parent across visits, no login, no consent before storing anything, no correctness judging on the live response path, and no way to review what the Safety Critic was catching. Built in small, individually-verified chunks (full day-by-day record, including bugs found and design corrections caught mid-build, in [`BUILD_LOG.md`](BUILD_LOG.md)):

- **Accounts, consent, and a persistent child profile** — login/signup (`auth.py`, hashed+salted passwords), an explicit one-time consent screen before anything is stored (`app.py`'s gates), and a child profile that auto-loads after login instead of being re-entered every session (`child_profile.py`).
- **Workflow 1 conversation memory** — a follow-up like _"find me therapists in bangalore"_ right after discussing a condition now correctly carries that condition forward, instead of losing it (`agents/router.py`, `chat_log.py`).
- **Live correctness judging** — the Safety Critic's 5th check (`is_correct`) runs on every real response, not only offline in `eval.py`.
- **A minimal human-in-the-loop review queue** — every Safety Critic verdict is persisted; a parent can see what got flagged from their own conversations and why (`review_queue.py`). Explicitly a _self_-review queue, not a cross-account staff/reviewer tool — that gap is documented, not hidden (see `ARCHITECTURE.md` §14).

Full detail, file-by-file: [`ARCHITECTURE.md`](ARCHITECTURE.md) §16–18.

---

## Architecture summary

![NorthStar four-pillar architecture: a Streamlit UI feeds two workflows (Ask NorthStar and Daily Support), both of which pass every response through a shared Safety Critic before it reaches the parent or gets suppressed, with a feedback loop closing the cycle](docs/architecture-overview.svg)

- **5 specialist agents** (Router, Info, Pathway, Intake, Activity) + **1 shared Safety Critic** + **1 external tool** (`directory_lookup`), all with typed Pydantic I/O schemas
- **3 local knowledge bases** (JSON, no vector DB) — Conditions+Terminology, Therapy, Activities — retrieved via OpenAI embeddings + cosine similarity
- **Model routing**: `gpt-4o-mini` for most agents, `gpt-4o` for the Pathway Agent (tool orchestration) and Safety Critic (must be rigorous)
- **Phase 2**: a persistence & trust layer wraps the above — SQLite-backed accounts/consent/child profile, Workflow 1 conversation memory, and a minimal HITL review queue (see "Phase 2" above and `ARCHITECTURE.md` §16–18)
- Full detail, including the Pydantic schemas, every agent's prompt rationale, and documented deviations from the original plan: [`ARCHITECTURE.md`](ARCHITECTURE.md)

---

## How to run

**Prerequisites:** Python 3.11+, [`uv`](https://docs.astral.sh/uv/), an OpenAI API key, and (optional but recommended) a LangSmith API key for tracing/feedback.

```bash
# from inside 07-northstar-capstone/
cp .env.example .env
# edit .env and fill in OPENAI_API_KEY (and LANGSMITH_API_KEY if you want tracing)

# CLI — Workflow 1 only
uv run python main.py "What is autism?"

# Full Streamlit app — both workflows, feedback, transparency panel
# First run: sign up (any username/password — this is a local demo), agree to the consent
# screen, then the app is fully usable. northstar.db is created automatically.
uv run streamlit run app.py

# Evaluation harness — runs the 20-query golden set, saves results
uv run python eval.py eval_results_v3.json
```

`uv run` bootstraps the virtual environment and installs dependencies automatically from `pyproject.toml`/`uv.lock` — no separate install step needed.

---

## Evaluation & the v1 → v2 improvement cycle

Three evaluation methods against a 20-query golden set (`golden_set.jsonl`): DeepEval `ExactMatchMetric` on Router intent classification, DeepEval `GEval` (gpt-4o judge) on response quality, and a live 👍/👎 feedback rate via LangSmith + `feedback.jsonl`.

|                   | v1 (`eval_results_v1.json`) | v2 (`eval_results_v2.json`) |
| ----------------- | --------------------------- | --------------------------- |
| Router ExactMatch | 93.75% (15/16)              | 93.75% (15/16)              |
| GEval average     | 0.746                       | 0.744                       |
| PII pass rate     | 100%                        | 100%                        |

**The targeted change:** a thumbs-down with the comment "too long" in `feedback.jsonl` flagged an Info Agent response. (That feedback was a test case authored while verifying the feedback mechanism during Day 5 QA, not an organic complaint from a real end user — this project has no real users yet, one week in.) Added one explicit conciseness rule to `agents/info.py`'s prompt. Measured result: all 6 info-category responses dropped from ~100-115 words to 51-73 words (3-4 sentences) — the actual complaint, directly verified as fixed.

**Why the GEval average didn't move, honestly explained:** the golden set's reference answers were written as thorough summaries, so GEval's "grounded in the reference" criterion rewards completeness — which directly works against the new conciseness instruction. The aggregate score net out roughly flat (within normal run-to-run noise) even though the real, specific problem was fixed. Full analysis in `BUILD_LOG.md`'s Day 7 entry, including per-query score deltas.

---

## Guardrails

Five non-negotiable checks, enforced by the Safety Critic on every single response:

1. **No diagnostic language** — never tells a parent what their child "has"
2. **No treatment prescription** — suggests therapy _categories_, never a specific plan
3. **Mandatory specialist-referral disclaimer** on every therapy-adjacent response
4. **Out-of-scope refusal** — if the knowledge base doesn't cover something, the system says so rather than guessing
5. **Correct** (`is_correct`) — the response must be a _meaning_-accurate reading of what was actually retrieved, not just one with no outright fabrication; added after an instructor review flagged that correctness judging needed to happen live, not just offline in `eval.py`

Plus PII protection (child names/specific details aren't surfaced in responses) and an honest content gap fallback (the Activity Agent returns a plain "we don't have a specific activity for this" rather than fabricating one when nothing in the KB matches).

---

## Known limitations (documented honestly, not hidden)

- **The Safety Critic suppresses flagged responses rather than revising them.** The original design planned a revision loop (flagged content sent back to the agent for a retry); what's actually built suppresses the response and shows a safe fallback instead, with the flagged original still visible in the UI's transparency panel. Simpler and safer by default, but recovers fewer responses than a true retry loop would. See `ARCHITECTURE.md` §9/§14.
- **The therapist directory is entirely fictional**, clearly marked as such (`"Fictional:"` name prefix + an illustrative-entry note on every card) across 4 Indian cities. A verified real-world directory is future-state work.
- **The HITL review queue is self-scoped, not a staff/reviewer tool.** A parent can see what got flagged from their own conversations; a genuine cross-account "clinician reviews everyone's flagged content" workflow needs a separate reviewer role, which doesn't exist yet (this app has exactly one role: parent). See `ARCHITECTURE.md` §18.
- **Crisis/self-harm content gets the same generic decline as any other out-of-scope question**, not dedicated crisis resources (e.g. a helpline). The Router already classifies it correctly as `out_of_scope`; a more specific, more helpful response for this one case is identified but not yet built. See `ARCHITECTURE.md` §9/§14.

---

## Project structure

```
07-northstar-capstone/
├── app.py                  # Streamlit UI — both workflows, auth/consent gates, feedback, transparency panel, review queue
├── main.py                 # CLI entry point + the actual pipeline logic app.py reuses
├── db.py                   # SQLite connection + full schema (parents, children, chat_messages, activity_log, critic_reviews)
├── auth.py                 # Password hashing, sign_up()/log_in()
├── child_profile.py        # Persisted child condition/age-range (DB-backed, auto-loads on login)
├── chat_log.py             # Per-turn conversation + Daily Support outcome logging; recent-history lookup for memory
├── review_queue.py         # Minimal HITL: persist/list/acknowledge Safety Critic verdicts
├── eval.py                 # Evaluation harness (ExactMatch + GEval + PII check)
├── golden_set.jsonl         # 20-query evaluation dataset
├── eval_results_v1.json     # Baseline eval scores
├── eval_results_v2.json     # Post-improvement-cycle eval scores
├── feedback.jsonl           # Captured 👍/👎 feedback
├── agents/
│   ├── router.py            # Intent classification (info / pathway / out_of_scope) + conversation-memory resolution
│   ├── info.py               # Grounded condition/terminology explanations
│   ├── pathway.py            # Therapy categories + specialist lookup (agent-calls-tool)
│   ├── intake.py             # Daily Support context gathering
│   ├── activity.py           # Grounded activity suggestions
│   └── safety_critic.py      # Shared quality gate — 5 guardrail checks
├── tools/
│   └── directory_lookup.py   # Fictional therapist directory lookup (LangChain tool)
├── kb/
│   ├── conditions.json       # Conditions + Terminology KB
│   ├── therapies.json        # Therapy categories KB
│   ├── activities.json       # Activities KB
│   ├── therapists.json       # Fictional therapist directory
│   └── retrieve.py           # OpenAI-embeddings retrieval helper
├── docs/
│   └── architecture-overview.svg  # Standalone architecture diagram (embedded in this file and ARCHITECTURE.md)
├── northstar.db             # SQLite database file — git-ignored, created on first run
├── ARCHITECTURE.md
├── IMPLEMENTATION_PLAN.md
├── PRESS_RELEASE_AND_FAQ.md
└── BUILD_LOG.md
```

---

## Screenshots

_To be added — chat with the transparency panel open, a Daily Support activity suggestion, the feedback flow, a LangSmith trace, and the eval delta._

---

## License

MIT.
