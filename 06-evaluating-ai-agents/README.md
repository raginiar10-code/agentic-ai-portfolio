# Evaluating & Operationalizing an AI Agent

A hands-on walkthrough of what it actually looks like to evaluate an AI agent — the offline eval loop, the online eval loop, and the production feedback cycle that connects them. The subject agent is a **signup email generator** that takes a SaaS user's signup data and produces a personalized welcome email with an ICP (Ideal Customer Profile) fit classification.

> This is a course exercise I worked through to understand the evaluation and operationalization patterns. The agent architecture is a provided LangChain pattern; the value in the notebook (and in my reflections below) is the eval loop, not the agent itself.

## What the notebook demonstrates

The project uses three tools stacked together, each solving a different part of the evaluation story:

| Tool          | Role in the loop                                                                       |
| ------------- | -------------------------------------------------------------------------------------- |
| **LangChain** | Builds the agent itself — a reasoning-only agent with structured Pydantic output        |
| **LangSmith** | Tracing (every call captured), golden dataset storage, online eval rules in production  |
| **DeepEval**  | Offline evaluation metrics — exact match + LLM-as-a-judge rubric scoring                |

Four stages:

1. **Build a simple LangChain agent** that takes a signup record and returns `{to, icp_fit, reason, subject, body}` as structured output.
2. **Create a golden dataset** of labeled signup → expected-output examples, uploaded to LangSmith.
3. **Run offline evaluation with DeepEval** using two different metric types:
   - `ExactMatchMetric` for `icp_fit` (deterministic classification)
   - `GEval` (LLM-as-judge) for email quality — scored against a natural-language rubric
4. **Set up online evaluation** in the LangSmith UI — automatic scoring of every production trace in real time, with the feedback loop back to the golden dataset.

## My actual results

Running the offline eval against the 6-example golden dataset:

```
icp_fit exact match rate:   0.333
email avg score:            0.787
```

Two things worth pausing on:

- **The ICP classification was correct 33% of the time** (2 out of 6). On the same outputs, the LLM-judge rated the email quality at 78.7%. One metric says the agent is substantially broken; the other says it's good. Both are correct — they're measuring different things. More on this in the reflections below.
- **The 6-example golden set is small.** This is the beginning of a real test suite, not the whole thing. The feedback loop (Step 4 below) is how it grows.

## What I actually did vs what the full exercise covers

Honest framing:

| Step                                      | Status                                                                           |
| ----------------------------------------- | -------------------------------------------------------------------------------- |
| Build agent + structured output           | ✅ Done                                                                           |
| Trace to LangSmith (automatic)            | ⏸️ Deferred — the default-project view in the LangSmith UI didn't surface for me on first pass; left for a follow-up                                  |
| Upload golden dataset to LangSmith        | ✅ Done                                                                           |
| Fetch dataset locally in Python           | ✅ Done                                                                           |
| Define DeepEval metrics (ExactMatch + GEval) | ✅ Done                                                                        |
| Run offline eval across all examples      | ✅ Done — results above                                                           |
| Set up online eval rules in LangSmith UI  | ⏸️ Deferred — the default-project view in the LangSmith UI didn't surface for me on first pass; left for a follow-up |
| Close the feedback loop (production fails → golden set) | ⏸️ Deferred — depends on online eval being wired up                        |

The offline loop is complete and reproducible. The online loop is a next-pass item.

## Design decisions worth calling out

- **Two metric *types*, not two metrics.** `ExactMatchMetric` is deterministic — a classification either matches or it doesn't. `GEval` uses an LLM to judge against a natural-language rubric. These aren't competing; they test different things. A production eval suite needs both: deterministic metrics for the surfaces where correctness is binary (routing, classification, structured field extraction) and rubric-scored metrics for the surfaces where quality is a judgment call (tone, conciseness, personalization).
- **LLM-as-a-judge is itself non-deterministic.** The GEval metric uses `gpt-4o-mini` as the judge. The score is subject to the same non-determinism as the agent being judged. The right way to think about this is: the judge is a *rubric with a model attached*; the rubric is the stable part.
- **Golden datasets are versioned test suites.** The 6 examples here are the start. The feedback loop (online eval flags a bad trace → add it to the golden set → re-run offline eval) is the agent-era version of "add a regression test for every bug that reaches production."
- **Tracing is the observability layer.** LangSmith captures every call — latency, token usage, inputs, outputs, intermediate steps. For a QE lens, this is the equivalent of OpenTelemetry traces for a traditional distributed system. Without tracing, you cannot debug an agent that fails in production.
- **Structured output is a testability choice.** The agent returns a Pydantic `SignupEmailOutput` with named fields, not free-form text. This is deliberate: `output.icp_fit` is a value you can assert on; a wall of prose is not. Structured output is one of the cheapest wins for making an agent evaluable.

## The production feedback loop

The core LLMOps pattern this exercise teaches:

```
┌─────────────────────────────────────────────────┐
│                                                 │
│   Agent runs on new signups                     │
│        │                                        │
│        ▼                                        │
│   LangSmith traces + online eval scores         │
│        │                                        │
│        ▼                                        │
│   Failures flagged automatically                │
│        │                                        │
│        ▼                                        │
│   Add failing cases to golden dataset           │
│        │                                        │
│        ▼                                        │
│   Re-run offline eval (DeepEval) with           │
│   expanded golden set                           │
│        │                                        │
│        ▼                                        │
│   Fix prompt / agent → redeploy                 │
│        │                                        │
│        └──────────── loop ──────────────────────┘
│                                                 │
└─────────────────────────────────────────────────┘
```

**Online eval catches issues → golden dataset grows → offline eval prevents regressions → agent improves.** This closes a loop that most agent projects leave open.

## How to run the notebook

### Prerequisites

- Google Colab (or local Jupyter)
- OpenAI API key with GPT-4o-mini access
- LangSmith account + API key (free tier available at [smith.langchain.com](https://smith.langchain.com))
- The golden dataset JSONL — build your own (6+ labeled signup → expected-output pairs)

### Setup

1. Open [`signup_email_agent_eval.ipynb`](./signup_email_agent_eval.ipynb) in Colab.
2. In Colab, add two secrets via the key icon in the left sidebar:
   - `OPENAI_API_KEY`
   - `LANGSMITH_API_KEY`
3. In LangSmith UI: **Datasets & Experiments → + Dataset → Click to upload**. Name it `golden-dataset`. Upload your JSONL.
4. Run cells top to bottom. The agent runs, traces appear in LangSmith, DeepEval scores print to the notebook.

### For the online eval section (deferred in my run)

1. In LangSmith → **Tracing → default project → New → New Evaluator**
2. Create a prebuilt evaluator (LLM-as-judge rules on production traces)
3. New agent runs will be scored automatically
4. Use the Monitor tab to view aggregate quality metrics

## What I learned

Running my first real agent evaluation surfaced several things worth saying from a QE lens:

1. **Agent evaluation is where QE actually lives.** Everything I've been building for the last 5 weeks (routing agents, RAG, multi-agent systems, voice agents, A2A + MCP) was setup. *This* week — defining what "good" looks like for an agent's output and measuring it — is the actual QE problem.

2. **Two metrics on the same output can disagree completely, and both can be right.** My ICP classification exact-match was 33%. My email quality GEval score on the same outputs was 78.7%. If I'd only looked at one, I'd have missed half the story. QE for agents means picking metric *types* to match the surface being tested, not just picking "a metric."

3. **The judge is another non-deterministic surface.** LLM-as-judge scoring is powerful, but the judge itself is a model. The score of a specific run is a sample from a distribution, not a point estimate. Treat it accordingly — look at averages across a dataset, not individual scores.

4. **Golden datasets are the agent-era version of regression suites.** In traditional QE, every bug that reached production becomes a test case so it never happens again. The feedback loop in this exercise is the exact same pattern — online eval flags a failure, you add it to the golden dataset, next offline eval catches regressions.

5. **Tracing is non-negotiable.** You cannot debug what you cannot see. LangSmith's automatic trace capture is the agent equivalent of structured logs + distributed tracing in traditional systems. Any agent going to production without this is going to production blind.

## Stack

- **Agent framework**: LangChain (`create_agent`)
- **LLM**: OpenAI `gpt-4o-mini` (both as the agent and as the LLM-judge)
- **Structured output**: Pydantic models
- **Tracing + online eval**: LangSmith
- **Offline eval**: DeepEval (`ExactMatchMetric`, `GEval`)
- **Dataset**: 6-example golden set stored in LangSmith

## Files in this folder

- `signup_email_agent_eval.ipynb` — the full Jupyter notebook, runnable in Colab
- `prompts.md` — the agent's system prompt and the GEval rubric, with design commentary
- `README.md` — this file

## License

MIT.
