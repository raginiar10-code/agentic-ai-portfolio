# NorthStar — Press Release & FAQ

**Capstone Exercise 1** · Amazon-style "working backwards" press release and FAQ, written from the perspective of launch day.

---

## PRESS RELEASE

**FOR IMMEDIATE RELEASE**

# Introducing NorthStar — a thoughtful, grounded navigator for parents of neurodivergent children.

### A multi-agent AI companion that helps parents understand their child's condition, explore evidence-based therapy categories, and find qualified specialists — always deferring medical decisions to professionals.

**Bangalore, India** — Today we're launching **NorthStar**, a navigator built specifically for parents of neurodivergent children. Whether your child has been diagnosed with autism, ADHD, apraxia, or a rare neurodevelopmental condition, NorthStar helps you move from *"I don't even know what to ask"* to *"I know what to explore next and who to talk to."*

### The problem we're solving

Parents of neurodivergent children face a brutal information gap in the days and months after diagnosis. They don't know the terminology. They don't know which therapies are commonly explored for their child's specific condition. They don't know which therapists in their city specialize in what. They don't know how to help their child engage or regulate in the hard moments of daily life. The information exists — scattered across clinical papers, support forums, therapist directories, and well-meaning but inconsistent social media groups — but no one has pulled it together into a single, grounded, honest navigator.

Worse, parents searching online are often served either clinical jargon they can't parse or confidently wrong advice from AI chatbots that don't know what they don't know.

### What NorthStar does

NorthStar is a multi-agent AI system that answers parent questions along three dimensions:

1. **Understanding** — "What is sensory processing disorder?" "What does *oral-motor* mean?" NorthStar's Information agent retrieves grounded answers from a curated knowledge base of conditions and terminology.
2. **Pathways** — "My child has autism — what therapies are commonly considered, and are there specialists in Bangalore?" NorthStar's Pathway agent reasons over the knowledge base to suggest therapy *categories* (never prescribing), then looks up specialists via its directory tool.
3. **Daily support** — Once a parent sets their child's context for the session, NorthStar's Activity agent suggests age- and condition-appropriate engagement activities, or helps when a child is dysregulated in the moment.

Every response is gated by a Safety Critic agent that enforces three non-negotiable rules: **no diagnosis, no treatment prescription, always refer to qualified specialists.**

### Why we built it this way

We built NorthStar as a navigator, not a clinician. It is grounded in a verified knowledge base — not model memory — and every therapy suggestion comes with an explicit reminder to consult a developmental pediatrician before acting. The system is engineered for a specific type of honesty: it refuses questions outside its scope, admits when it doesn't have information, and never presents inference as fact.

### Quote from leadership

> *"When my own child was diagnosed, I spent weeks searching for a single, trustworthy place that treated me like an intelligent adult trying to help my child, without either overwhelming me with jargon or oversimplifying the stakes. NorthStar is what I needed and couldn't find."*
> — **Ragini AR**, Creator

### Quote from a parent (illustrative)

> *"NorthStar didn't try to be my child's doctor. It helped me understand what the doctor was saying, pointed me to the right kinds of therapists in my city, and gave me five calming activities I could actually use at 7pm on a Tuesday. That's what I needed."*
> — **Illustrative parent persona**

### How to get started

Visit the NorthStar interface, set your child's basic context (condition, age) in the sidebar, then start asking questions — or tell NorthStar what your child needs right now (calm down, something fun, a general idea) for a Daily Support activity suggestion. No account required. Your session context stays local to your session.

NorthStar is free, open-source (MIT), and runs locally for full privacy. All information in the system is grounded in curated public references, and the therapist directory in the launch version is illustrative — contributions of verified listings welcome from the community.

### Our commitments

- **Always a navigator, never a clinician.** NorthStar will never diagnose, prescribe, or tell you to stop seeing a specialist.
- **Grounded answers, with sources.** Every answer is based on retrieved content you can inspect, not model inference.
- **Transparent reasoning.** Every response has a "How did I arrive at this?" view that shows which agent handled it, what was retrieved, and what the Safety Critic verdict was.
- **Honest limits.** When NorthStar doesn't have information, it says so. It will not guess.

---

## FAQ

### For parents

**Q: Is NorthStar going to tell me what therapy my child needs?**
No. NorthStar will tell you what therapy *categories* are commonly discussed for your child's condition, based on its knowledge base. The specific therapy decisions must come from a qualified developmental pediatrician or specialist who has met and evaluated your child. Every pathway response from NorthStar ends with this reminder.

**Q: Can NorthStar diagnose my child?**
No. NorthStar has hard guardrails against diagnostic language. If you describe symptoms and ask "what does my child have," NorthStar will refuse to speculate and will encourage you to seek a professional evaluation.

**Q: How accurate is the information?**
Every response is grounded in a curated knowledge base — not model guesswork. You can see exactly which chunks were retrieved to answer each question via the "How did I arrive at this?" view. The knowledge base at launch covers 5–6 common conditions in depth. We're transparent about scope; if you ask about something outside the current KB, NorthStar will say so.

**Q: Is the therapist directory in the launch real?**
The therapist directory in the launch version is **illustrative** — fictional entries used to demonstrate the pathway from "I know what therapy category I need" to "here's who provides it in my city." A verified real-world directory is on the roadmap, built in partnership with professional organizations.

**Q: How does NorthStar handle my child's information?**
Child context (condition, age, main needs) is set per session and stays local. There is no account, no login, no cloud-stored child data. When your session ends, that context is gone unless you choose to export it.

**Q: Can I use NorthStar on my phone?**
The launch version is desktop-browser-first. Mobile optimization is on the roadmap.

**Q: My child has a rare condition that isn't in the launch KB. What should I do?**
Please consult a developmental pediatrician — your child's needs are too specific for a general knowledge base. NorthStar will explicitly tell you when it doesn't have information on a condition rather than guess. Expanding KB coverage based on community feedback is a core part of our improvement loop.

**Q: What if NorthStar gives me bad advice?**
NorthStar has three safeguards: (1) retrieval grounding — every answer is based on the KB, not model inference; (2) a Safety Critic agent that reviews every output for diagnostic language, missing disclaimers, overpromising, and unsupported claims — if anything fails, that response is blocked from reaching you and replaced with a safe message, not just flagged after the fact; (3) a 👍/👎 feedback system where you can flag unhelpful or wrong responses. Flagged responses feed directly into our improvement loop.

### For course reviewers and technical readers

**Q: What agent architecture does NorthStar use?**
Hub-and-Spoke for the information/pathway workflow (Router → Info / Pathway → Safety Critic), Sequential for the daily support workflow (Intake → Activity → Safety Critic). Five specialist agents plus a shared Safety Critic. One external tool (`directory_lookup`). Full architecture in `ARCHITECTURE.md`.

**Q: What evaluation methods are in place?**
Three: (1) DeepEval GEval rubric scoring on response quality (grounded, non-diagnostic, disclaimer-bearing, concise); (2) ExactMatch on Router Agent classification correctness against a golden set of 20 queries; (3) user feedback rate (👍/👎 ratio) tracked via LangSmith's feedback API as an online eval signal.

**Q: What's the improvement loop?**
Feedback → golden-set expansion → prompt tuning / KB gap fill → re-run offline eval → compare v1 vs v2 scores. The capstone demonstrates this cycle once end-to-end with a measurable score delta.

**Q: Why isn't the directory lookup a separate agent?**
It's a tool, not an agent. Tools do deterministic retrieval; agents make judgments. Making the directory an agent would have violated the "one job per agent" principle and would have been architecturally dishonest. Keeping it as a tool makes the "Agent → Tool" rubric requirement land cleanly.

**Q: What are the top guardrails?**
Four, enforced by the Safety Critic: (1) no diagnostic language; (2) mandatory professional-referral disclaimer on every therapy-adjacent response; (3) refusal to answer out-of-scope questions; (4) PII protection — the system does not log or echo child personal details.

**Q: Why Streamlit and not a production web framework?**
Capstone timeline is one week. Streamlit lets us ship a working, polished UI with observability built in — the right trade for the demo window. Production would use a different stack.

---

## About

NorthStar is a capstone project built as part of an applied agentic AI course. It is open-source (MIT), runs locally, and welcomes community contributions — especially expanded condition coverage, verified therapist listings, and translation to Indian languages.

Repository: *to be published alongside final capstone submission*
