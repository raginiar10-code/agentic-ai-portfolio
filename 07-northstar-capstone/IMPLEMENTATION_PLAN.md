# NorthStar — Day-by-Day Implementation Plan

**Capstone Exercise 3** · 7-day build plan with clear scope per day, daily deliverables, and risk flags.

---

## Working principles for the week

1. **Ship a working thin slice daily.** Every day ends with something runnable, not just half-built scaffolding. Course demo value compounds — a working v0.1 on day 2 is better than a perfect-plan-on-paper that never ships.
2. **KISS — Keep It Simple Stupid.** Resist adding features. The MVP is the leanest shape that hits every rubric item.
3. **Guardrails before polish.** The Safety Critic goes in early. Later days can polish UI; they cannot retrofit safety.
4. **Observability from day 1.** LangSmith wired on the first LLM call, not at the end.
5. **Honest framing.** Where AI assistance helps me build, I say so in the final README — same discipline as the rest of my portfolio series.

---

## Day 1 — Foundation + Knowledge Bases

**Goal:** Project scaffolded, environment working, KBs populated, LangSmith wired.

### Tasks
- Create `07-northstar-capstone/` folder in portfolio repo
- Set up `uv` env, `pyproject.toml`, `.env.example`, `.gitignore`
- Install: `langchain`, `langchain-openai`, `langsmith`, `deepeval`, `streamlit`, `openai`, `python-dotenv`, `pydantic`
- Build **KB 1 (Conditions + Terminology)** — 5–6 conditions as structured JSON entries. Content curated from public references; include my son's rare syndrome.
- Build **KB 2 (Therapy)** — 5 therapy categories as structured JSON
- Build **KB 3 (Activities)** — 15–20 activity entries as structured JSON
- Build **therapists.json** — 15–20 fictional entries across Bangalore / Mumbai / Delhi / Chennai
- Wire up basic embedding + retrieval helper (`retrieve.py`) using OpenAI embeddings over the three KBs
- Set up LangSmith env vars and verify a single test LLM call shows up in the LangSmith dashboard

### Deliverable
A working `python -c "from retrieve import retrieve_from_conditions; print(retrieve_from_conditions('autism'))"` that returns the right chunks AND a visible trace in LangSmith.

### Risks
- Content for the rare syndrome KB entry might take extra time — write a shorter honest entry rather than skipping it
- LangSmith UI issue from Week 6 — if the default-project view still doesn't surface, use the API directly to verify traces

### Checkpoint questions at end of day
- Can I retrieve from all three KBs?
- Is at least one LLM call visible in LangSmith?

---

## Day 2 — Workflow 1 base: Router + Info Agent + Safety Critic

**Goal:** Information lookup path works end-to-end from Python (no UI yet).

### Tasks
- Define Pydantic schemas: `RouterOutput`, `InfoOutput`, `SafetyCriticOutput`
- Build **Router Agent** — GPT-4o-mini, classifies intent (info / pathway / out_of_scope), extracts entities (condition, city)
- Build **Info Agent** — GPT-4o-mini, retrieves from KB 1, produces grounded explanation with disclaimer
- Build **Safety Critic** — GPT-4o, scores output against the 4 non-negotiable checks
- Wire a `main.py` test script: input query → Router → Info Agent → Safety Critic → output
- Test with 5 sample queries, verify traces in LangSmith

### Deliverable
Running `python main.py "What is autism?"` returns a grounded, disclaimer-bearing response with Safety Critic verdict visible in logs and in LangSmith.

### Risks
- Router misclassifying — tighten the system prompt with examples
- Safety Critic too strict (flagging everything) — tune threshold and rubric with examples

### Checkpoint questions
- Does the Router pick the right intent on 5-of-5 test queries?
- Does the Safety Critic pass clean outputs and flag intentionally bad ones?

---

## Day 3 — Pathway Agent + directory_lookup tool

**Goal:** Workflow 1 complete — pathway queries including specialist directory lookups work.

### Tasks
- Build the `directory_lookup` Python function; wrap as a LangChain tool
- Define `PathwayOutput` schema
- Build **Pathway Agent** — GPT-4o, has `directory_lookup` tool bound, retrieves from KB 2 for therapy categories, calls the tool when `city` is present in the input
- Extend `main.py` to route pathway intent to Pathway Agent
- Test: *"My child has autism. What therapies, and specialists in Bangalore?"* — verify the Pathway Agent reasons about therapies, calls the tool with correct params, synthesizes the final response, passes Safety Critic
- Verify LangSmith trace shows the tool call clearly

### Deliverable
End-to-end Workflow 1 works for both info AND pathway queries from the Python CLI, with tool calls visible in LangSmith traces.

### Risks
- Pathway Agent might call the tool even when city isn't specified — tighten the prompt to require city before calling
- Tool call failures (bad params) — add input validation inside the tool

### Checkpoint questions
- Does the Pathway Agent correctly decide when to call the tool vs when to answer without it?
- Does the final response combine therapy reasoning + directory results coherently?

---

## Day 4 — Workflow 2: Intake + Activity + session state

**Goal:** Daily Support workflow works end-to-end. Both workflows functional from CLI.

### Tasks
- Define `IntakeOutput`, `ActivityOutput` schemas
- Build **Intake Agent** — GPT-4o-mini, reads session state (dict), identifies missing context, returns clarifying questions if incomplete, otherwise hands off to Activity Agent
- Build **Activity Agent** — GPT-4o-mini, retrieves from KB 3 based on condition + current_need, generates age/condition-appropriate activity
- Extend `main.py` to support Workflow 2 — simulate session state as a dict
- Test: with session state set → *"suggest a calming activity"* → returns grounded activity
- Test: with empty session state → *"suggest a calming activity"* → returns clarifying questions
- Verify Safety Critic reuses correctly across both workflows

### Deliverable
Both workflows functional from CLI. Session state logic works for Intake Agent's independent-action behavior.

### Risks
- Intake Agent loop logic — ensure it doesn't infinite-loop asking clarifying questions
- Activities KB might feel thin for some condition combos — accept a few "I don't have specific activities for this combination; here's a general approach" responses as honest fallback

### Checkpoint questions
- Does the Intake Agent correctly ask clarifying questions when context is missing?
- Does the Activity Agent produce grounded, age-appropriate suggestions?

---

## Day 5 — Streamlit UI + Feedback capture

**Goal:** Working UI with both workflows accessible via chat. Feedback mechanism live.

### Tasks
- Scaffold `app.py` Streamlit application
- Build the sidebar: child context panel (expander with condition/age/needs inputs) + observability section + quick-start query buttons + safety banner
- Build the main chat panel: message history, input box, session banner
- Wire the chat input to Router → appropriate workflow → response rendering
- Add "How did I arrive at this?" expandable under every AI response showing: agent name, retrieved chunks, tool calls if any, Safety Critic verdict
- Add 👍/👎 feedback buttons under every response + optional comment field (expands on 👎)
- On feedback click: post to LangSmith feedback API attached to the trace ID + append to local `feedback.jsonl`
- Render therapist results as cards (not plain bullets)
- Add loading states with descriptive text ("Looking up therapy options for autism...")
- Apply custom CSS for warm, calm visual tone (reuse pattern from Week 2 if possible)

### Deliverable
Running `streamlit run app.py` opens a usable UI. A parent can set child context, ask a question in either workflow, see a response with transparency panel, and leave 👍/👎 feedback.

### Risks
- Streamlit session state can be fiddly — allocate time to debug
- "How did I arrive at this?" sections consuming screen real estate — collapse by default
- Styling can eat time — set a 2-hour cap on CSS polish

### Checkpoint questions
- Can a user complete a full parent query → answer → feedback cycle in the UI?
- Does the UI feel calm/warm (not clinical/alarming)?

---

## Day 6 — Evaluation harness + v1 baseline

**Goal:** Golden dataset built, three evaluation methods running, v1 baseline scores captured.

### Tasks
- Write the 20-query golden dataset as `golden_set.jsonl`:
  - 6 info queries (one per condition)
  - 6 pathway queries (various condition + city combos)
  - 4 activity queries
  - 4 adversarial (out of scope, diagnostic-seeking, overpromise-seeking, PII)
  - Each entry: query, expected intent, expected agent, expected elements in response (e.g., `must_contain_disclaimer: true`), reference answer for GEval
- Upload golden set to LangSmith
- Write `eval.py` script that:
  - Runs all 20 queries through the full pipeline
  - Scores Router intent with `ExactMatchMetric`
  - Scores responses with `GEval` (rubric: grounded, non-diagnostic, has disclaimer, concise)
  - Prints aggregate scores + per-query breakdown
- Set up LangSmith online eval rule — LLM-as-judge checking "has disclaimer" and "no diagnostic language" on every production trace
- Run `eval.py`, capture v1 baseline scores, save output to `eval_results_v1.json`

### Deliverable
`eval_results_v1.json` with scores for ExactMatch (Router accuracy %), GEval average (0-1), per-query results. LangSmith online eval visibly scoring new traces.

### Risks
- GEval may be slow (one LLM call per query × many metrics) — budget time and parallelize where safe
- Golden set adversarial examples may crash agents if not handled gracefully — expected; part of the test

### Checkpoint questions
- Do I have numeric baseline scores for all three evaluation methods?
- Is online eval firing on new production traces?

---

## Day 7 — Improvement cycle + final polish + capstone deliverables

**Goal:** Demonstrate one measurable improvement cycle. Finalize all documentation. Record demo.

### Tasks

**Morning — improvement cycle:**
- Review feedback captured during Day 5–6 testing (👎s + comments) + eval v1 failures
- Identify ONE specific failure pattern (e.g., "Pathway Agent omits disclaimer when directory results are included")
- Make ONE targeted change (e.g., tighten Pathway Agent prompt, or add missing KB content)
- Re-run `eval.py` → capture v2 scores → save `eval_results_v2.json`
- Document the delta: which score moved, by how much, what change caused it

**Afternoon — polish and documentation:**
- Final pass on `README.md` for the `07-northstar-capstone/` folder: overview, architecture summary, how-to-run, screenshots, improvement-cycle delta
- Capture screenshots of the UI (chat with transparency panel open, feedback click, observability sidebar, LangSmith trace, eval results)
- Record a short demo video or GIF showing: parent sets context → asks pathway query → sees response with disclaimer + specialists → leaves 👍 feedback → opens "How did I arrive at this?" view
- Finalize `PRESS_RELEASE_AND_FAQ.md`, `ARCHITECTURE.md`, `IMPLEMENTATION_PLAN.md` — update any sections based on what was learned during build
- Sanitize code: no API keys, no personal data in commits — same sanitization discipline as the rest of the portfolio
- Push to GitHub, verify public rendering

**Evening — submission:**
- Submit capstone with repo URL
- Prepare demo talking points (3-minute version + 10-minute version)

### Deliverable
Complete, publishable capstone project in `07-northstar-capstone/` folder with code, docs, screenshots, demo video, and v1 → v2 improvement delta visibly documented.

### Risks
- The improvement cycle might not produce a clean delta — if so, pick a different failure to target. The point is demonstrating the loop, not a specific score.
- Last-minute scope creep ("I could add one more agent...") — resist. Lock scope on day 6 and polish on day 7.
- Video recording can eat time — a 60-second screen GIF is enough

### Checkpoint questions
- Can I show a reviewer a numeric v1 → v2 improvement that I drove with a specific change?
- Is everything that's shipped in the repo honest, grounded, and demonstrably working?

---

## Risk register (whole-week view)

| Risk | Mitigation |
|---|---|
| LangSmith UI issue from Week 6 recurs | Verify traces via API directly, defer dashboard polish |
| KB content takes longer than expected to curate | Write shorter, honest entries rather than skipping. "I don't have information on X" is a feature, not a bug. |
| Streamlit session state is fiddly | Allocate buffer on Day 5; have a backup plan to pass state via function args |
| Safety Critic is too strict or too lenient | Spend Day 2 getting it right; it's the quality gate for everything |
| Scope creep | Future-state items go in `ARCHITECTURE.md` Section 14, not into Day 5–7 work |
| Demo doesn't run on submission day | Record the demo on Day 6 as a backup; don't rely on live run during submission |

---

## Daily end-of-day habit

Every day, before stopping:
1. Commit code with a descriptive message
2. Update a short `BUILD_LOG.md` with: what shipped today, what's deferred, any surprises
3. Verify LangSmith is still capturing traces from the day's work
4. Note tomorrow's first task so you start without re-planning

The `BUILD_LOG.md` doubles as a credible, timestamped artifact of weekly progress — useful for the capstone submission and for any future interview conversation.

---

## Honest framing for the final README (per handoff doc rules)

The final `07-northstar-capstone/README.md` will apply the same honesty principles as the rest of the portfolio series:

- **"I designed the architecture end-to-end"** — ADLC-style ownership language (you did design it)
- **"I curated the knowledge bases myself"** — accurate
- **"I built the implementation using LangChain / Streamlit / DeepEval / LangSmith"** — accurate (these are frameworks, not substitutions for implementation)
- **"Where AI assisted the code generation, it was directed by me"** — honest, consistent with ATE Journal Analyser framing

Do NOT write "I built an agentic AI system from scratch" if parts were AI-assisted. The ADLC architecture is your original work and can be claimed in full. The individual agent code implementations will likely be AI-assisted and should be framed accordingly.

---

## Submission checklist (end of Day 7)

- [x] All code in `07-northstar-capstone/` folder
- [x] `README.md` with overview, architecture summary, how to run, screenshots (screenshots still pending — see below)
- [x] `PRESS_RELEASE_AND_FAQ.md` (Exercise 1 deliverable)
- [x] `ARCHITECTURE.md` (Exercise 2 deliverable)
- [x] `IMPLEMENTATION_PLAN.md` (this doc, as the build journal)
- [x] `BUILD_LOG.md` (daily notes from the week)
- [x] `app.py` (Streamlit UI)
- [x] Agents in `agents/` subfolder with clear naming
- [x] `tools/directory_lookup.py`
- [x] `kb/` folder with the three knowledge base JSONs + `therapists.json`
- [x] `eval.py` + `golden_set.jsonl` + `eval_results_v1.json` + `eval_results_v2.json`
- [x] `feedback.jsonl` with sample captured feedback
- [ ] Screenshots: UI chat, observability panel, feedback click, LangSmith trace, eval delta
- [ ] Short demo video / GIF
- [x] `.gitignore` (excludes `.env`, logs, `.venv`)
- [x] `.env.example` with placeholder keys
- [ ] All API keys, PII, and personal paths sanitized (final pass still pending — see BUILD_LOG.md)
