# A2A Multi-Agent Negotiation — Step-by-Step Guide

This project demonstrates the **A2A (Agent-to-Agent) protocol** for multi-agent negotiation, built on top of an MCP weather server. A2A handles the inter-agent communication; MCP handles each agent's tool access. The two protocols stack.

## What this demo covers

| Concept                | How It's Shown                                                 |
| ---------------------- | -------------------------------------------------------------- |
| **Agent Discovery**    | Coordinator fetches Agent Cards from `/.well-known/agent.json` |
| **Task Delegation**    | Coordinator sends structured tasks to specialized agents       |
| **Parallel Execution** | Weather + Venue agents queried simultaneously                  |
| **Conflict Detection** | LLM analyzes mismatches between agent responses                |
| **Negotiation**        | Coordinator sends adjusted tasks with new constraints          |
| **Consensus**          | Agents re-evaluate and coordinator synthesizes final plan      |
| **MCP + A2A Together** | Weather Agent uses MCP internally, A2A externally              |

## Architecture

```
User
  │
  ▼
Coordinator (CLI + OpenAI)
  │                          │
  │ A2A (HTTP :5001)         │ A2A (HTTP :5002)
  ▼                          ▼
Weather Agent              Venue Agent
  │                          │
  │ MCP (stdio)              │ local JSON
  ▼                          ▼
weather_server.py          venue_data.json
```

## Negotiation Flow (3 Rounds)

```
ROUND 1: Initial Assessment
  Coordinator → Weather Agent: "Assess weather for Austin, TX"
  Coordinator → Venue Agent:   "Find outdoor venues in Austin, TX"
  (parallel)

ROUND 2: Conflict Resolution
  Weather says: "CAUTION — 102F, heat advisory"
  Coordinator detects conflict → asks Venue Agent:
    "Re-evaluate: require covered venues, prefer evening"
  Coordinator asks Weather Agent:
    "Re-assess for evening time slot"

ROUND 3: Synthesis
  Weather (adjusted): "GO — evening temps acceptable"
  Venue (adjusted): "3 covered evening venues found"
  Coordinator → User: complete event plan with venue, timing, checklist
```

---

## Prerequisites

- Python 3.13+
- `uv` installed ([install guide](https://docs.astral.sh/uv/))
- OpenAI API key with GPT-4o access

---

## Step 1 — Set Up the Weather MCP Server

```bash
cd weather-skills
uv venv
# Windows: .\.venv\Scripts\Activate.ps1
# macOS/Linux: source .venv/bin/activate
uv sync
```

---

## Step 2 — Set Up the A2A Event Planner

```bash
cd ../a2a-event-planner
uv venv
# Windows: .\.venv\Scripts\Activate.ps1
# macOS/Linux: source .venv/bin/activate
uv sync
```

Configure your OpenAI API key:

```bash
cp ../.env.example .env
```

Edit `.env` and paste your actual key:

```
OPENAI_API_KEY=sk-xxxxxxxxxxxxxxxxxxxxxxxx
```

---

## Step 3 — Start the Agents (3 Terminals)

You need **three separate terminal windows**. Each must be in the `a2a-event-planner` directory with its venv activated.

### Terminal 1 — Weather Agent (port 5001)

```bash
cd a2a-event-planner
# activate venv
uv run weather_agent.py ../weather-skills/weather_server.py
```

Expected output:

```
Weather Agent starting...
  Connecting to MCP server: ../weather-skills/weather_server.py
  MCP connected — tools: ['get_alerts', 'get_forecast']
  Weather Agent ready on http://localhost:5001
INFO:     Uvicorn running on http://0.0.0.0:5001
```

### Terminal 2 — Venue Agent (port 5002)

```bash
cd a2a-event-planner
# activate venv
uv run venue_agent.py
```

Expected output:

```
Venue Agent starting...
  Loaded 10 venues from venue_data.json
  Venue Agent ready on http://localhost:5002
INFO:     Uvicorn running on http://0.0.0.0:5002
```

### Terminal 3 — Coordinator (interactive CLI)

```bash
cd a2a-event-planner
# activate venv
uv run coordinator.py
```

Expected output:

```
╔══════════════════════════════════════════╗
║     A2A Event Planner — Coordinator      ║
╠══════════════════════════════════════════╣
║  Agents expected:                        ║
║    Weather Agent → localhost:5001         ║
║    Venue Agent   → localhost:5002         ║
╚══════════════════════════════════════════╝

Type your event planning query, or 'quit' to exit.
Example: Plan an outdoor wedding in Austin TX this Saturday

Query:
```

---

## Step 4 — Verify Agent Discovery

Before running a full negotiation, confirm the agents are discoverable:

```bash
curl http://localhost:5001/.well-known/agent.json | python -m json.tool
curl http://localhost:5002/.well-known/agent.json | python -m json.tool
```

You should see Agent Cards with name, description, skills, and capabilities for each.

---

## Step 5 — Run Negotiation Queries

In the Coordinator terminal:

```
Query: Plan an outdoor wedding in Austin TX this Saturday
Query: Outdoor concert in Miami FL for 150 guests this weekend
Query: Birthday party in Denver CO next weekend, afternoon preferred
Query: Corporate team building in New York NY, need space for 100 people
```

Watch all three terminals as the negotiation unfolds. The Coordinator shows the full three-round flow; the specialists show the individual A2A task IDs they're processing.

To exit:

```
Query: quit
```

Then `Ctrl+C` in terminals 1 and 2 to stop the agents.

---

## How MCP and A2A Work Together

| Layer               | Protocol   | What It Does                                                           |
| ------------------- | ---------- | ---------------------------------------------------------------------- |
| Tool access         | **MCP**    | Weather Agent calls `get_alerts`, `get_forecast` on the MCP server     |
| Agent communication | **A2A**    | Coordinator sends tasks to Weather + Venue agents                      |
| Negotiation         | **A2A**    | Coordinator detects conflicts, sends adjusted tasks, reaches consensus |
| Reasoning           | **OpenAI** | Coordinator uses LLM for parsing, conflict detection, synthesis        |

MCP provides the **capabilities**. A2A provides the **collaboration**. Neither replaces the other.

---

## Troubleshooting

| Issue                                  | Solution                                                                        |
| -------------------------------------- | ------------------------------------------------------------------------------- |
| `Connection refused` on :5001 or :5002 | Make sure both agents are running first                                         |
| `ModuleNotFoundError: a2a_protocol`    | Run from the `a2a-event-planner/` directory                                     |
| Weather Agent fails to connect to MCP  | Check the path to `weather_server.py` — must be absolute or correct relative    |
| OpenAI errors                          | Verify `.env` has a valid `OPENAI_API_KEY`                                      |
| `Address already in use`               | Kill existing processes on ports 5001/5002                                      |
| Venue Agent returns 0 venues           | Check the city name matches `venue_data.json` (Austin, Miami, Denver, New York) |
