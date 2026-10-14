# Prompts & Rubrics — Signup Email Agent Eval

Two LLM-driven surfaces in this project. The first is the agent's own system prompt (produces the welcome email). The second is the LLM-as-a-judge rubric (scores the quality of the generated email). Both matter for evaluation.

---

## 1. Agent System Prompt

The prompt that turns a signup record into a structured ICP classification + welcome email.

```
Process this signup to your dev tool SaaS called 'Glop'.
Return icp_fit, reason and welcome email.

Welcome email should mention user's current role and company.
Make sure the email is concise, conversationally informal, and professional.
Use relevant input fields when appropriate. Sound specific to the user and avoid feeling like a generic template.
Do not mention details that are not supported by the input.
```

### Structured output schema

```python
class SignupEmailOutput(BaseModel):
    to: str = Field(description="Email address")
    icp_fit: Literal["high", "medium", "low", "unknown"] = Field(description="ICP fit classification")
    reason: str = Field(description="Brief reasoning for classification")
    subject: str = Field(description="Email subject line")
    body: str = Field(description="Email body text")
```

### Why the prompt is shaped this way

- **"Return icp_fit, reason and welcome email"** — explicit multi-output expectation. The agent is doing two things at once (classification + generation), and the prompt names both.
- **"Mention user's current role and company"** — forces personalization. Without this, the model drifts toward generic greetings.
- **"Concise, conversationally informal, and professional"** — three competing adjectives. This is the hardest kind of constraint for an LLM, which is why it's also the surface where the GEval rubric (below) is most useful.
- **"Avoid feeling like a generic template"** — negative constraint. More effective than positive constraints alone for this kind of qualitative output.
- **"Do not mention details that are not supported by the input"** — explicit anti-hallucination. If the input doesn't say the user is in SaaS, the email shouldn't infer SaaS positioning.
- **Pydantic schema** — forces structured output. Every field becomes individually testable; the LLM cannot return a wall of prose that passes a "did it respond?" check while failing the actual contract.

### What's missing (worth noting from a QE lens)

- **No ICP definition.** The prompt doesn't tell the agent what makes a signup "high" vs "medium" vs "low" ICP fit. The model is inferring this from general knowledge of SaaS sales. This is almost certainly why the exact-match rate for `icp_fit` was only 33% — the agent and the golden dataset disagree on the definition.
- **No tone examples.** "Conversationally informal and professional" is vague. One-shot or few-shot examples in the prompt would narrow the LLM's interpretation.
- **No word limit stated to the agent.** The GEval rubric (below) checks for "under 150 words" but the agent isn't told this. Classic mismatch between what's being tested and what's being specified.

Fixing any of these would materially improve the agent's eval scores — a nice teaching moment for the gap between prompt engineering and eval engineering.

---

## 2. GEval Rubric (LLM-as-Judge for Email Quality)

The rubric that scores whether a generated email meets quality standards.

```python
email_evaluator = GEval(
    name="Email Quality",
    criteria=(
        "Evaluate if the email is professional, personalized "
        "(uses the person's name/role/company), concise (under 150 words), "
        "and does not hallucinate facts not present in the input."
    ),
    evaluation_params=[LLMTestCaseParams.INPUT, LLMTestCaseParams.ACTUAL_OUTPUT],
    model="gpt-4o-mini",
    threshold=0.5
)
```

### Why the rubric is shaped this way

- **Four explicit quality dimensions** rolled into one criterion string:
  1. Professional tone
  2. Personalization (uses name/role/company)
  3. Conciseness (under 150 words — a hard numeric threshold)
  4. No hallucinated facts (grounded in input)
- **One metric, four dimensions.** The judge produces one aggregate score. For richer analysis, you'd split these into four separate GEval metrics, each returning its own score. The trade-off: one metric is cheaper and easier to track; four is more diagnostic when something regresses.
- **`gpt-4o-mini` as the judge.** Same model as the agent itself. This is a cost choice; using `gpt-4o` or `gpt-4-turbo` as the judge would be more rigorous for the same budget as running the agent on a larger dataset.
- **Threshold 0.5.** Below this is a "fail." Above is a "pass." The specific threshold is somewhat arbitrary at this stage — the right one depends on what percent of production outputs you can tolerate below the bar.
- **`include_reason = True`** (set elsewhere in the notebook) — the judge explains WHY it gave each score. These explanations are the actual signal when debugging a regression; the score alone is just a trigger.

### Observations from running this on the golden set

The judge's reasons (printed per example in the notebook) consistently flagged:
- Emails that exceeded the 150-word limit — e.g., *"it exceeds the word limit, containing more than 150 words"*
- Emails that lacked specific personalization touches
- Emails where the agent inferred facts not in the input

These are exactly the failure modes the rubric is designed to catch. In each case, the agent prompt could be tightened to prevent the failure — e.g., add a "keep under 150 words" instruction. That's the prompt-engineering ↔ eval-engineering loop in action.

---

## 3. Exact Match Metric (for `icp_fit` classification)

No LLM judge needed — this is a straight string comparison.

```python
icp_fit_evaluator = ExactMatchMetric(threshold=1.0)
```

### Why this is a different metric type

The ICP classification has a correct answer in the golden dataset — `"high"`, `"medium"`, `"low"`, or `"unknown"`. There's no interpretation needed. Either the agent's output matches the label or it doesn't.

**Deterministic metrics belong on surfaces where correctness is binary.** Classification, routing, structured-field extraction, API return codes — anything where the "right" answer is a single value.

**Rubric-scored (LLM-judge) metrics belong on surfaces where quality is a judgment call.** Prose quality, tone, personalization, summarization fidelity.

Mixing the two in a single eval harness — as this project does — is a strong QE pattern. The same output can simultaneously pass one metric and fail another, and both scores are correct. Looking at only one would miss half the story.

---

## Things worth experimenting with

- **Split the GEval into four metrics**, one per dimension (professional, personalized, concise, grounded). Rerun. The aggregate score should be close to today's 0.787, but you'll now see *which* dimension is driving the score up or down.
- **Add the ICP definition to the agent prompt.** Something like *"ICP = high when the user is a VP/Director at a company of 100+ in dev tools, SaaS, or infra; medium when the company is 20-100; low when the company is under 20 or outside those sectors; unknown when insufficient data."* Then rerun eval and see whether the exact-match rate climbs above 33%.
- **Change the judge model.** Switch `GEval(model="gpt-4o-mini")` to `GEval(model="gpt-4o")`. Compare scores on the same outputs. The delta tells you how much the judge model matters for your use case.
- **Grow the golden set.** 6 examples is a starter kit. Add 20 more that represent edge cases — missing fields, misspelled companies, outlier roles. Re-run and see which metrics catch which failures.
