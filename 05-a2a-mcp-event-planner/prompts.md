# Prompts & Agent Cards — A2A + MCP Event Planner

The intelligent behavior of this system comes from three LLM-driven decisions inside the Coordinator and two self-describing agent cards exposed by the specialists. This file collects all of them with design commentary.

---

## 1. Coordinator — Query Parsing Prompt

Converts the user's natural-language query into structured event parameters.

**System:**
```
Extract event planning parameters from the user query. Return ONLY valid JSON with these fields:
  "city": string (city name),
  "state": string (2-letter state code),
  "date": string (the date or relative description),
  "event_type": string (type of event),
  "guest_count": integer or null,
  "time_preference": string ("morning","afternoon","evening") or ""
If the user does not specify a field, use reasonable defaults. Always include city and state.
```

**Why it's shaped this way:**
- **Strict JSON return format.** No prose, no explanation. Downstream code `json.loads()` the response directly. If the LLM wraps the JSON in markdown code fences, the coordinator strips them before parsing.
- **Explicit field types.** `"state"` must be a 2-letter code (because the MCP weather tool expects that format). The prompt pre-translates the user's "Texas" or "TX" into the format the tool needs.
- **"Always include city and state"** — these are hard requirements for both downstream agents. Making this explicit prevents the LLM from occasionally omitting them for queries like *"outdoor concert this weekend"*.
- **Enumerated time preferences.** Only `morning / afternoon / evening / ""` are valid. The specialist agents consume this as a filter key.

---

## 2. Coordinator — Conflict Detection Prompt

Decides whether the weather and venue responses have a mismatch worth re-negotiating.

**System:**
```
You are analyzing weather and venue data for an outdoor event. Determine if there are conflicts that require adjustments.

Return ONLY valid JSON:
  "has_conflicts": boolean,
  "concerns": [list of specific concern strings],
  "suggested_time_preference": "morning"|"afternoon"|"evening"|"",
  "require_covered": boolean,
  "require_indoor_backup": boolean,
  "explanation": string (brief explanation of reasoning)

Conflicts exist if:
- Temperature > 95F or < 40F for outdoor event
- Active weather alerts (warnings/watches)
- Rain/storm in forecast
- High winds > 25 mph

If weather is CAUTION or worse, suggest mitigations.
```

**Why it's shaped this way:**
- **Boolean + reason, not just boolean.** `has_conflicts` tells the coordinator whether to issue adjusted tasks. `concerns` + `explanation` are human-readable justifications that get printed to the terminal — they also appear in the final synthesized plan's "Negotiation Summary."
- **Explicit numeric thresholds.** `>95F`, `<40F`, `>25 mph` — these are the same thresholds encoded in the Weather Agent's rule-based logic. Keeping them aligned prevents the LLM from inventing different thresholds that disagree with the agent's own assessment.
- **Suggested outputs match tool inputs.** `suggested_time_preference`, `require_covered`, `require_indoor_backup` are the exact field names the Venue Agent's `adjust_venues` skill consumes. The prompt enforces output that's directly consumable by the next tool call.
- **"If weather is CAUTION or worse, suggest mitigations"** — the Coordinator can't just detect conflicts; it has to propose solutions. The prompt makes this explicit so the LLM doesn't just surface the problem.

---

## 3. Coordinator — Final Synthesis Prompt

Produces the human-readable event plan combining all rounds' data.

**System:**
```
You are an expert outdoor event planner. Based on the multi-agent negotiation data below, produce a final event plan.

Format your response as:
## Event Plan: [Event Type] in [City]
### Decision: GO / CAUTION / NO-GO
### Recommended Venue
  - Venue name and why it was selected
### Timing
  - Recommended time slot and reasoning
### Weather Summary
  - Key weather factors
### Preparation Checklist
  - Actionable items for the organizer
### Negotiation Summary
  - Brief explanation of how the agents negotiated (what changed between rounds)

Be specific and actionable. Reference actual venue names and data.
```

**Why it's shaped this way:**
- **Enforced structure.** The six section headings are non-negotiable, in order. Downstream users (or UI layers that might render this) know exactly what sections to expect.
- **"Reference actual venue names and data."** Prevents the LLM from generalizing — the plan must name The Driskill Courtyard, not "a nearby covered venue." Grounding the response in retrieved data, not model priors.
- **Negotiation Summary is required.** This section exists specifically to make the multi-round reasoning *legible to the user*. Without it, the plan looks like the agent "just knew" to pick the Driskill; with it, the user can see the Round 1 → Round 2 path that led there.
- **No tool-use leakage.** The format explicitly asks for user-facing content only. The internal protocol chatter (A2A task IDs, MCP tool calls, raw JSON responses) stays in the terminal logs; the plan is clean prose.

---

## 4. Weather Agent — Agent Card

Published at `GET /.well-known/agent.json` on `http://localhost:5001`.

```json
{
  "name": "Weather Agent",
  "description": "Assesses weather conditions for outdoor events using NWS data. Provides GO/CAUTION/NO-GO recommendations based on temperature, precipitation, wind, and active alerts.",
  "url": "http://localhost:5001",
  "version": "1.0",
  "capabilities": {
    "streaming": false,
    "push_notifications": false
  },
  "skills": [
    {
      "id": "assess_weather",
      "name": "Weather Assessment",
      "description": "Evaluate weather conditions for a location and provide a safety recommendation for outdoor events.",
      "tags": ["weather", "safety", "outdoor"],
      "examples": [
        "Assess weather for Austin TX this Saturday",
        "Is it safe to hold an outdoor concert in Miami?"
      ]
    }
  ]
}
```

**Why it's shaped this way:**
- **Description tells coordinators what the agent is *for*, not what APIs it exposes.** Discovery is about intent, not implementation.
- **One skill, deliberately scoped.** The Weather Agent doesn't advertise "general weather lookup." It advertises "safety recommendations for outdoor events" — the exact use case the Coordinator cares about. Narrow skills are easier for coordinators to route to confidently.
- **Examples field.** Natural-language example queries the coordinator LLM can use to decide whether a query belongs to this agent. This is prompt-engineering for routing, exposed via the card.
- **Capabilities = false everywhere.** This is a Phase-1 build. Streaming responses and push notifications are declared explicitly as `false` so coordinators know not to subscribe.

---

## 5. Venue Agent — Agent Card

Published at `GET /.well-known/agent.json` on `http://localhost:5002`.

```json
{
  "name": "Venue Agent",
  "description": "Discovers and recommends event venues based on location, capacity, weather constraints, and event type. Can adjust recommendations when given weather-related constraints like heat, rain, or wind.",
  "url": "http://localhost:5002",
  "version": "1.0",
  "capabilities": {
    "streaming": false,
    "push_notifications": false
  },
  "skills": [
    {
      "id": "find_venues",
      "name": "Venue Discovery",
      "description": "Find and filter venues by city, capacity, time slot, and weather mitigation features.",
      "tags": ["venue", "event", "planning"],
      "examples": [
        "Find outdoor venues in Austin TX",
        "Find covered evening venues in Miami for 100 guests"
      ]
    },
    {
      "id": "adjust_venues",
      "name": "Venue Adjustment",
      "description": "Re-filter venues with additional weather constraints (e.g. require cover, shift to evening, need indoor backup).",
      "tags": ["venue", "weather", "adjustment"],
      "examples": [
        "Re-evaluate Austin venues: avoid afternoon heat, prefer covered"
      ]
    }
  ]
}
```

**Why two skills instead of one:**
- `find_venues` is the Round-1 skill (unconstrained search).
- `adjust_venues` is the Round-2 skill (constrained re-search after a conflict).

Splitting them makes the Coordinator's intent explicit at each round — the LLM picks the right skill for the right phase, and the terminal logs make it obvious which round each call belongs to. Could have been one skill with an optional "constraints" parameter; splitting is clearer for both human readers and the LLM router.

---

## 6. MCP Server Prompts (Weather Skills)

The MCP weather server also exposes three *prompt templates* (MCP calls these "Prompts" or "Skills") that other LLMs can discover and use. These are not called by the Event Planner but demonstrate the full MCP primitive set:

- **`outdoor_event_readiness(location, date, event_type)`** — step-by-step prompt that walks an LLM through weather assessment for an outdoor event. Would be a direct alternative to this entire project if you wanted a single-LLM solution without A2A.
- **`severe_weather_briefing(state)`** — structured situation report format for emergency briefings.
- **`multi_day_trip_planner(destinations)`** — multi-city trip optimization using weather forecasts.

See `weather-skills/weather_server.py` for the full prompt text.

---

## Things worth experimenting with

- **Add a third specialist.** A Catering Agent, for example. The coordinator's negotiation loop should extend naturally — if catering requires outdoor cooking and weather says CAUTION, that's a new conflict dimension. Would reveal whether the current conflict-detection prompt generalizes or is quietly hardcoded to weather+venue.
- **Swap the Coordinator's model.** GPT-4o-mini is cheaper and faster. The parsing and synthesis prompts should still work; the conflict detection prompt is where quality might regress. Measure.
- **Add a termination guard.** The current negotiation is capped at 2 rounds by code structure. In theory, agents could re-negotiate indefinitely. Add an explicit `max_rounds` + consensus-detection to make the stopping condition visible.
- **Expose MCP resources to the Coordinator.** The MCP server exposes `weather://alert-severity-guide` as a resource. The Coordinator currently ignores this. Teaching the Coordinator to read MCP resources via the Weather Agent (another A2A ↔ MCP bridge) would be a stronger demo of protocol composition.
