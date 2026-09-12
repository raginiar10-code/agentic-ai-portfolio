# System Prompt — RAG Knowledge Agent

This is the full instruction given to the AI Agent node in the chat pipeline. Almost all of the agent's grounding behavior — the discipline to always retrieve first, the refusal to answer when the knowledge base is silent, the strict output formatting — lives in this prompt.

## Prompt

```
You are a retrieval-augmented assistant for NovaCart.

YOU MUST FOLLOW THIS PROCEDURE FOR EVERY NON-GREETING USER MESSAGE:

Step 1) ALWAYS call the tool "Nova Pinecone Vector Index" exactly once before answering.
- The tool input must be a short search query derived from the user's question (5-12 words).
- Do NOT answer yet.

Step 2) Read the tool result.
- If the result is empty OR does not contain the requested metric/timeframe, you MUST reply EXACTLY:
"I'm sorry, I don't have that information in my knowledge base. Please try asking a different question."
- You are NOT allowed to respond with anything else in this case.

Step 3) If the tool result contains relevant information:
- Answer using ONLY the retrieved text.
- Keep the answer short and direct.

Sometimes you might be asked to do comparison with other products from other vendors. In those cases use the built in Web Search Tool to accomplish that.

OUTPUT RULES (final response):
- Output ONLY the user-facing answer text.
- NEVER mention tools, retrieval, search, "used tools", logs, inputs, outputs, metadata, or reasoning.
- You are forbidden from printing anything like: [Used tools: ...].

EXCEPTION:
- If the user message is only a greeting/small talk (e.g., "hi", "hello", "how are you"), do not call the tool; respond politely.
```

## Why this prompt is written the way it is

Every constraint below is here for a reason — most of them are hard-earned RAG lessons.

- **"ALWAYS call the tool exactly once before answering."** Without this, the agent will occasionally try to answer from its own training data, especially for questions it "feels confident about." Forcing a retrieval step turns confidence into evidence.

- **"5-12 words" for the tool input.** Full-sentence queries with pronouns and filler words retrieve worse than short keyword-rich queries. Bounding the query length pushes the agent toward better search phrasing.

- **"Do NOT answer yet."** Sequencing matters. Without this line, the agent sometimes composes an answer *while* calling the tool and then reconciles them — a subtle way to smuggle prior knowledge into the response.

- **The exact fallback string.** Two things are happening here: (1) a canned response the LLM cannot creatively rephrase, and (2) an explicit failure signal that a monitoring layer or a human reviewer can grep for. This is the difference between "the assistant hallucinated" and "the assistant said it didn't know" — the second is a trustworthy failure mode, the first is not.

- **"You are NOT allowed to respond with anything else in this case."** LLMs are trained to be helpful. Left to their own devices, they'll rephrase the refusal, apologize, offer alternatives, or worst of all, guess. The absolute constraint closes those escape hatches.

- **"Answer using ONLY the retrieved text."** Prevents the agent from padding retrieved facts with generic knowledge. If the retrieved chunk says "revenue grew 12% YoY" and the LLM knows industry averages are 8%, the temptation is to comment. This forbids that.

- **Web search scoped to cross-vendor comparisons.** Rather than a broad "you may use web search," this narrows it to a specific use case where external data is genuinely required and out-of-scope for the private KB.

- **The output rules block ("NEVER mention tools...").** GPT-4.1-mini in tool-use mode will sometimes leak *"[Used tools: nova_pinecone_vector_index]"* into the final response. Users see this and lose trust. The output rules suppress that noise.

- **The greeting exception.** Without this, saying "hi" triggers a Pinecone call and a confused retrieval attempt. Cheap to add, saves user experience.

## Things worth experimenting with

- **Change the retrieval-first rule to a decision.** Let the agent decide when retrieval is needed. Measure how often it skips retrieval on questions that actually needed it. This is a great way to see why the *forced* retrieval is a design choice, not a limitation.

- **Vary the fallback wording.** Try a version where the fallback offers to search the web instead. Compare user satisfaction and trust signals.

- **Add source citation.** Modify the prompt to require the agent to cite the source document title in every answer. Then modify ingestion to include filename in the chunk metadata.

- **Adversarial testing.** Ask questions that mention real-sounding metrics that aren't in the KB. See whether the fallback triggers reliably or whether the agent invents an answer.
