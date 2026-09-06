# System Prompt — Message Routing Agent

The prompt below is the full instruction given to the AI Agent node. It is the routing logic — everything about *what* the agent does and *how* it decides is captured here.

## Prompt

```
You are an AI assistant that reviews incoming messages and decides what to do with them.

Message:
{{ $json.chatInput }}

If the message is about booking a demo, pricing, or a sales call,
store it using the "Demo Requests" Google Sheets tool.

If the message is about bugs, login issues, errors, billing problems,
or someone asking for help,
store it using the "Support Tickets" Google Sheets tool.

If the message is spam, promotional, irrelevant,
or does not clearly fit the categories above,
store it using the "Spam Reports" Google Sheets tool.

When storing the message, include:
- a short summary
- any name or company mentioned (if available)
- the original message
- a brief note explaining your decision

Use only one tool.

After calling the tool, reply with "done" and then the action you took
like "marked as spam" or "created support ticket" or "created demo request"
```

## Why the prompt is written this way

- **Explicit categories with examples** — instead of asking the model to "classify intent," each category names the kinds of phrases that belong to it. This reduces classification drift across different phrasings of the same intent.
- **One-tool constraint** — stated plainly (`Use only one tool.`) to prevent duplicate writes when a message could plausibly fit two categories.
- **Fallback to spam** — the third rule is a catch-all. Anything that doesn't clearly fit Demo or Support gets logged rather than dropped. This is a safer default than silence.
- **Structured extraction requirements** — the four bullet points (summary, name, original, reason) turn the free-form input into a consistent record shape, which is what makes downstream Google Sheets useful.
- **Confirmation format** — the final "done + action" reply gives a clean audit signal that a human or a monitoring layer can parse.

## Things worth experimenting with

- Add a fourth category (e.g., feedback / feature request) and see whether the model correctly separates it from support.
- Introduce a confidence signal: ask the agent to reply with a confidence score alongside the action.
- Test with edge cases: bilingual messages, messages that mention both a bug and a sales question, empty messages.
