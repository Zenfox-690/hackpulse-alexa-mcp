# HackPulse Alexa+ 🎙️⚡
### Autonomous Opportunity Discovery, Rule-Audit Provenance Engine & Centre-Stage Multimodal Assistant
**Built for the Build, Ship, Shape: Amazon Developer Hackathon 2026 (Alexa+ Track)**

[![MCP Specification](https://img.shields.io/badge/MCP%20Spec-2025--11--25%20Streamable%20HTTP-00CAFF?logo=fastapi&logoColor=white)](https://modelcontextprotocol.io)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![Python 3.13](https://img.shields.io/badge/Python-3.13-3776AB?logo=python&logoColor=white)](https://python.org)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Tests Passing](https://img.shields.io/badge/Tests-16%20Passing-10B981?logo=pytest&logoColor=white)](tests/test_mcp_e2e.py)
[![Docker Ready](https://img.shields.io/badge/Docker-Hugging%20Face%20%7C%20Render-blueviolet?logo=docker&logoColor=white)](Dockerfile)

---

## 1. Project Overview & Core Value Proposition

Every year, thousands of developers invest weeks building hackathon projects only to face last-minute disqualification due to hidden restrictions:
- **Obscure Regional & Age Clauses:** Geographic or student-only terms buried in nested accordion FAQs.
- **Sudden Deadline Shifts:** Submissions closing at 12:00 PM (noon) PDT instead of the assumed midnight.
- **Hidden Post-Submission Hosting Requirements:** Mandatory persistent cloud uptime through multi-week judging periods (e.g. keeping an MCP endpoint online through November 20).
- **Misaligned Feasibility:** Committing to an in-person hackathon despite having a ₹0 travel budget and only 15 hours/week.

**HackPulse Alexa+** solves this by transforming passive hackathon directories into an **autonomous pair-programming agent and multimodal ambient display (Amazon Echo Show 15)**.

### Why HackPulse Alexa+ is NOT Just a Chatbot or Directory:
1. **MCP `2025-11-25` Streamable HTTP Standard:** Complies with the latest Model Context Protocol specification, providing stateful sessions via `Mcp-Session-Id` headers.
2. **Strict Provenance Honesty:** Transparently separates live HTTP inspections (`verified_live`) from offline seeds (`fallback_unverified`), recording source URLs, UTC timestamps, and SHA-256 content hashes.
3. **Cross-Session SQLite Memory:** Remembers builder constraints (15h/week, ₹0 budget, tech stack) and persists sprint milestone checklists across page reloads.
4. **Centre-Stage Multimodal Experience:** 80% of the viewport is an interactive visual canvas driven by real JSON-RPC 2.0 tool calls, accompanied by voice speech synthesis/recognition and a live Wire Inspector drawer.

---

## 2. Mandatory Disclosure — Official Rules Compliance

In accordance with official hackathon rules regarding existing projects and attribution:

| Component | Status | Origin & Description |
| :--- | :--- | :--- |
| **Upstream Data Feed (`hackathon-ingestion-platform`)** | **Pre-Existing** (External Read-Only Feed) | A public GitHub Pages static catalog (`data/hackathons.json`) that scrapes raw listing cards across Devpost, Devfolio, and Unstop. **100% untouched during this hackathon**; used strictly as an external data source. |
| **`hackpulse-alexa-mcp` Core Backend** | **100% Newly Built** | Full FastAPI implementation of the **MCP 2025-11-25 Streamable HTTP protocol**, session manager, HTML/prize sanitizer, and WAL-mode SQLite persistence. |
| **Rule-Audit & Diff Engine (`core/rule_auditor.py`)** | **100% Newly Built** | Live HTTP scraper + deterministic fallback inspector tracking rule provenance and computing risk diffs (`HIGH`, `MEDIUM`, `LOW`). |
| **Builder Profiler & Sprint Planner (`core/database.py`)** | **100% Newly Built** | 4-factor deterministic feasibility scoring engine (0-100) and persistent 15-hour time-boxed milestone checklist with Hour 6 Go/No-Go checkpoints. |
| **Echo Show 15 Multimodal Canvas (`static/`)** | **100% Newly Built** | Dark-mode ambient interface with Opportunity Deck, Rule Provenance Board, Rule Diff Viewer, Sprint Planner, Alexa+ voice bar, and real-time MCP Wire Inspector. |
| **Agent Skill (`skills/hackpulse-advisor/SKILL.md`)** | **100% Newly Built** | Open-standard playbook teaching AI agents the 4-stage advisory workflow. |

---

## 3. Architecture & MCP `2025-11-25` Specification

```mermaid
flowchart TD
    subgraph ClientLayer ["Multimodal Client Interfaces"]
        A[Amazon Echo Show 15 / Web Canvas] -->|JSON-RPC 2.0 over Streamable HTTP| B[POST /mcp]
        Voice[Alexa+ Voice Interaction Bar] --> A
        CLI[External MCP Host / Agent Client] -->|Streamable HTTP / Tools Call| B
    end

    subgraph ServerLayer ["HackPulse Alexa+ ASGI Server (FastAPI)"]
        B --> C[MCP Protocol Engine (2025-11-25)]
        C --> D[Mcp-Session-Id Tracker]
        C --> E[7 Core MCP Tools]
    end

    subgraph ToolLayer ["Core Tool Implementations"]
        E --> T1[search_hackathons]
        E --> T2[get_hackathon_details]
        E --> T3[sync_upstream_catalog]
        E --> T4[audit_rules]
        E --> T5[diff_rules]
        E --> T6[generate_sprint_plan]
        E --> T7[manage_builder_profile]
    end

    subgraph PersistenceLayer ["Persistent State & External Feeds"]
        T1 & T2 & T6 & T7 <--> DB[(SQLite: hackpulse.db WAL Mode)]
        T4 --> LiveHTTP[Live Official Rules Scraper]
        T3 <--> Seed[Upstream Static Catalog Feed]
    end
```

### The 7 Core MCP Tools

| Tool Name | Parameters | Description |
| :--- | :--- | :--- |
| **`search_hackathons`** | `query`, `source`, `status`, `location`, `tag`, `sort_by_feasibility`, `limit` | Searches catalog, automatically enriching each item with the builder's `feasibility_score` (0-100), `fit_reasons`, and `risk_flags`. |
| **`get_hackathon_details`**| `identifier` | Retrieves full metadata, normalized dates, timeline, and prize breakdowns. |
| **`sync_upstream_catalog`** | `upstream_url`, `force_refresh` | Syncs remote upstream catalog or offline fallback seed into persistent SQLite storage. |
| **`audit_rules`** | `identifier`, `force_live` | Audits official rules with provenance: `source_url`, UTC timestamp, `verification_status` (`verified_live` vs `fallback_unverified`), and 4 rule categories (Eligibility, Team Size, Deliverables, Hosting). |
| **`diff_rules`** | `identifier`, `new_rule_text` | Calculates unified diffs between listing baselines and official terms, calculating regulatory risk (`HIGH`, `MEDIUM`, `LOW`). |
| **`generate_sprint_plan`** | `hackathon_id`, `available_hours`, `completed_milestone_id` | Generates a persistent 15-hour time-boxed plan (M1–M5), Hour 6 Go/No-Go checkpoint, reuse compliance note, and toggles milestone checkboxes. |
| **`manage_builder_profile`**| `action`, `hours_per_week`, `budget_inr`, `prefer_online_only`, `skills`, `toggle_bookmark_id` | Retrieves or updates persistent cross-session builder profile and bookmark states in SQLite. |

---

## 4. 10-Second Terminal Test (Live `curl` Verification)

Judges can verify the live MCP Streamable HTTP server directly from the command line:

### 1. Handshake (`initialize`)
```bash
curl -i -X POST http://127.0.0.1:8000/mcp   -H "Content-Type: application/json"   -d '{
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
      "protocolVersion": "2025-11-25",
      "capabilities": {},
      "clientInfo": {"name": "judge-terminal", "version": "1.0.0"}
    }
  }'
```
*Response Header:* `Mcp-Session-Id: <uuid>`  
*Response Body:* `{"protocolVersion":"2025-11-25","capabilities":{"tools":{"listChanged":false}...}}`

### 2. Search Top Hackathons with Feasibility Ranking
```bash
curl -s -X POST http://127.0.0.1:8000/mcp   -H "Content-Type: application/json"   -H "Mcp-Session-Id: default"   -d '{
    "jsonrpc": "2.0",
    "id": 2,
    "method": "tools/call",
    "params": {
      "name": "search_hackathons",
      "arguments": {"sort_by_feasibility": true, "limit": 2}
    }
  }' | jq .result.structuredContent
```

### 3. Audit Rules with Provenance Tracking
```bash
curl -s -X POST http://127.0.0.1:8000/mcp   -H "Content-Type: application/json"   -H "Mcp-Session-Id: default"   -d '{
    "jsonrpc": "2.0",
    "id": 3,
    "method": "tools/call",
    "params": {
      "name": "audit_rules",
      "arguments": {"identifier": "0e18b6cf877bc442f9b92302350398fce6b84b05d01ac2f92ff5367616b259a8"}
    }
  }' | jq .result.structuredContent.data.verification_status
```

---

## 5. Local Quickstart

### Prerequisites
- Python 3.13+
- Git

```bash
# 1. Clone repository
git clone https://github.com/Zenfox-690/hackpulse-alexa-mcp.git
cd hackpulse-alexa-mcp

# 2. Create and activate virtual environment
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Run automated test suite (16 passing tests)
pytest tests/test_mcp_e2e.py -v

# 5. Launch Centre-Stage Multimodal Web App & MCP Server
uvicorn app:app --host 0.0.0.0 --port 8000 --reload
```
Open your browser at **`http://localhost:8000`** to experience the Echo Show 15 Multimodal interface.

---

## 6. Zero-Cost Cloud Deployment (Hugging Face Spaces & Render)

### Option A: Hugging Face Spaces (Recommended — ₹0 Free Tier)
1. Create a new **Hugging Face Space** with SDK type **Docker (Blank)**.
2. Push this repository directly to your Space's Git remote.
3. The included [`Dockerfile`](Dockerfile) automatically configures:
   - Port `7860`
   - Non-root UID `1000`
   - Least-privilege permissions (`chown -R 1000:1000 /app` and `chmod -R 755 /app/data`) ensuring secure SQLite WAL database operations under non-root execution.

### Option B: Render Free Tier
1. Connect your GitHub repository to [Render](https://render.com).
2. Render automatically detects [`render.yaml`](render.yaml) and provisions a free Docker web service with zero manual configuration.

### Keeping the Endpoint Live Through November 20 (Judging Period)
The repository includes [`.github/workflows/ci_and_keepalive.yml`](.github/workflows/ci_and_keepalive.yml). Set your deployed URL as a repository variable:
- **Repository Variable:** `HACKPULSE_LIVE_URL = https://your-space-or-render-url`
The GitHub Action will ping `/health` and send an MCP `initialize` handshake twice daily, keeping your free-tier instance warm throughout the judging period at ₹0 infrastructure cost.

---

## 7. 2.5-Minute Demo Video Script (One-Take Recording)

| Timestamp | Screen / Stage View | Action & Spoken Script |
| :--- | :--- | :--- |
| **0:00 – 0:35** | **Header & Opportunity Deck** | *"Welcome to HackPulse Alexa+, an autonomous opportunity discovery and rule-audit engine built for the Amazon Developer Hackathon 2026. Rather than a plain directory or text chatbot, we designed an Amazon Echo Show 15 ambient display where 80% of the screen is an interactive canvas driven by the MCP 2025-11-25 streamable HTTP standard. Notice our persistent profile bar: Avanish, B.Tech CSE, India, 15 hours per week, and a ₹0 budget. I'll click **Judge Demo Chip 1** to rank opportunities by feasibility."* |
| **0:35 – 1:10** | **Opportunity Deck (Sorted by Fit)** | *"Behind the scenes, the `search_hackathons` MCP tool calculates a 0-100 feasibility score. In-person events with ₹0 budget are penalized, while online AI and agent hackathons receive top scores. Each card displays fit reasons, risk flags, deadlines, and sanitized prize amounts. Let's inspect our top recommendation by clicking **Judge Demo Chip 2: Audit Amazon Hackathon Rules**."* |
| **1:10 – 1:45** | **Rule Provenance & Live Audit Board** | *"The canvas transitions to the Rule Provenance board. Under our execution guardrails, we never fabricate live fetches: this green banner confirms verified live inspection via direct HTTP, complete with retrieval timestamp and SHA-256 content hash. The 4 cards extract eligibility, team sizes, deliverables, and critical post-submission hosting rules—warning builders that their MCP streamable endpoint must remain live through November 20."* |
| **1:45 – 2:10** | **Rule Diff Inspector** | *"Next, clicking **Judge Demo Chip 3** triggers `diff_rules`. Often, promotional listings differ from official rules. Our diff engine detected a HIGH RISK alert: submissions close at 12:00 PM noon PDT rather than midnight, AWS credits are closed, and self-built test simulators are explicitly permitted. The unified diff highlights these discrepancies in green and red."* |
| **2:10 – 2:30** | **Sprint Planner & Wire Inspector** | *"Finally, clicking **Judge Demo Chip 4** generates our calibrated 15-hour sprint plan with an explicit Hour 6 Go/No-Go checkpoint and hackathon reuse compliance notice. As I check off Milestone M4, the state immediately persists to SQLite across page reloads. Opening the MCP Wire Inspector drawer reveals the live JSON-RPC 2.0 messages, session tokens, and latency. HackPulse Alexa+ gives builders verified clarity and confidence from discovery to submission."* |

---

## 8. Technology Stack

- **Protocol:** Model Context Protocol (MCP) `2025-11-25` Streamable HTTP
- **Backend:** FastAPI, Uvicorn, Python 3.13, Pydantic v2, HTTPX, BeautifulSoup4
- **Persistence:** SQLite 3 (WAL Mode, Foreign Keys Enabled)
- **Frontend:** Vanilla HTML5, CSS3, JavaScript (Echo Show 15 Ambient Design System, Web Speech API)
- **Testing:** Pytest, Starlette TestClient, Playwright Headless Chromium (16 Passing Tests)
- **Deployment:** Docker, Render Blueprint (`render.yaml`), GitHub Actions CI/CD & Keep-Alive

---

## 9. Developer Product Feedback (Alexa+ Track — MCP & Agent Skills)

As part of the Alexa+ Track for the Amazon Developer Hackathon 2026, building **HackPulse Alexa+** with the MCP `2025-11-25` Streamable HTTP standard and Agent Skills provided valuable developer experience insights. Below is constructive product feedback and architectural suggestions:

### 1. MCP `2025-11-25` Streamable HTTP Transport
- **Strengths:**
  - The unified single endpoint (`POST /mcp`) replacing dual SSE endpoints (`/sse` + separate POST message channel) drastically simplifies reverse-proxy routing, corporate firewall traversal, and CORS configuration in cloud environments like Hugging Face Spaces and Render.
  - Stateful session tracking via the `Mcp-Session-Id` header provides clean conversational isolation without polluting request payloads with transport-level metadata.
- **Friction Points & Opportunities:**
  - **Multimodal Testing Gap:** For non-partner developers without pre-release Alexa+ hardware access, there is currently no public Alexa+ multimodal web simulator to natively preview and validate `ui://` custom card rendering, touch events, and viewport responsiveness. Providing an open-source or web-based Alexa+ viewport simulator (similar to the Alexa Developer Console's APL Simulator) would dramatically accelerate developer iteration for centre-stage voice/visual experiences.

### 2. MCP Agent Skills (`SKILL.md`) & MCP Apps (`ui://` Resources)
- **Strengths:**
  - Standardizing agent workflows into portable markdown playbooks (`SKILL.md`) allows cross-agent interoperability, enabling Alexa+ and paired developer agents to execute multi-stage advisory tasks deterministically without hardcoding agent logic into tool endpoints.
  - The clean protocol boundary between `structuredContent` (machine-readable state) and text/multimodal presentations enables independent evolution of visual cards and LLM tool arguments.
- **Friction Points & Opportunities:**
  - **Schema Validation for Voice UI Transitions:** In voice-first multimodal agents, tools frequently transition across distinct visual stages (e.g., from search deck to live provenance inspection to diff comparison). There is an opportunity for standard tooling or SDK decorators that validate `structuredContent` stage transitions and UI contracts against JSON Schemas, ensuring that multimodal display templates never fail silently when an agent selects alternative tool paths.

### 3. Amazon Developer Documentation & Sample Code
- **Strengths:**
  - The conceptual overview of Model Context Protocol integration with agentic skills provides a strong vision for next-generation ambient computing.
- **Recommendations for `github.com/amazonappdev`:**
  - **Official Python FastAPI/Starlette Reference Template:** Most existing reference repositories lean heavily toward Node.js/TypeScript or stdio transports. Providing an official, production-ready Python FastAPI/Starlette repository implementing the `2025-11-25` Streamable HTTP specification—complete with session lifecycle management, CORS middleware, non-root Docker packaging, and SSE streaming fallback—would establish a definitive benchmark for Python AI developers building Alexa+ tools.

---

## 10. License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details. Mandatory for public open-source submission per hackathon guidelines.
