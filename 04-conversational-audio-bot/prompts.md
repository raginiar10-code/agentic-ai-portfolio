# System Prompt — RAG Audio Bot

The system prompt is composed dynamically at runtime depending on whether RAG is enabled and whether any context was retrieved.

## Base prompt (RAG disabled or empty retrieval)

```
You are a professional assistant. Be concise and helpful.
```

## Augmented prompt (RAG enabled, context retrieved)

```
You are a professional assistant. Be concise and helpful.

Use the following relevant context to inform your response:
- <retrieved chunk 1>
- <retrieved chunk 2>
- <retrieved chunk 3>

If the context doesn't help answer the question, respond based on your general knowledge.
```

## Why this prompt is written the way it is

- **"Be concise."** Voice output has a stricter length budget than text. A 400-word answer is unreadable when spoken aloud; a 60-word answer is natural. Explicit brevity is critical.

- **Dynamic context injection.** The retrieved chunks are inlined into the system prompt as bullet points, not appended to the user message. This keeps the user's actual question intact and gives the model a clear separation between "here is relevant context" and "here is what the user asked."

- **Soft grounding, not strict.** The phrase *"If the context doesn't help answer the question, respond based on your general knowledge"* is a deliberate trade-off. Strict grounding (like the Week 2 RAG agent's hard fallback) would say "refuse to answer anything the KB doesn't cover." This bot takes the opposite stance — treat retrieval as *augmentation*, not as the only source. For a general-purpose demo, this is more useful; for a mission-critical assistant, it's dangerous.

- **No persona.** The system prompt deliberately avoids giving the agent a name, personality, or character. For a voice bot that could become any character via TTS voice selection, the persona belongs at the voice layer, not the LLM layer.

## Where the real routing happens

Unlike the Week 3 multi-agent system (where tool descriptions carry the routing logic), this bot has no routing — a single pipeline handles everything. The decisions worth noticing are the architectural ones:

- Whether to call RAG at all (sidebar checkbox)
- How many chunks to retrieve (`k` slider)
- Which LLM to use (model selector)
- Grounding temperature (slider)

These are all exposed as UI controls, which turns the system prompt from a static artifact into a surface for *runtime experimentation*. That's a useful pattern for voice systems where every parameter has an audible cost.

## Things worth experimenting with

- **Swap TF-IDF for OpenAI embeddings.** The retrieval quality floor rises substantially, but you add network latency to every query. The quality/latency trade becomes measurable.
- **Add a reranker.** Same pattern as the Week 2 RAG agent — top-5 from TF-IDF, reranked to top-3 by a cross-encoder before the LLM sees them. Would improve grounding but add another audible pause.
- **Switch gTTS for ElevenLabs or OpenAI TTS.** gTTS is free and slow. ElevenLabs is paid and fast. The latency/cost/quality triangle shifts.
- **Introduce streaming at just one stage.** Try streaming the LLM response into the TTS as it generates, rather than waiting for the full response. This is the first step toward a production-grade pipeline.
