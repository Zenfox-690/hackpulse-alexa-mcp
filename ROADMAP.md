# HackPulse Alexa+ — Product Roadmap & Future Capabilities

This document records planned future enhancements and architectural extensions for HackPulse Alexa+.

---

## 1. Feature in Scope for Future Release: AI Project Idea Generator

### 1.1 Objective & Overview
While HackPulse Alexa+ currently excels at **opportunity discovery, rule verification, risk diffs, and sprint planning**, builders often experience friction during early ideation: deciding *what* to build that authentically aligns with the hackathon's specific judging criteria, required technologies, and the builder's actual weekly bandwidth.

The **AI Project Idea Generator** is designed to provide grounded, differentiated project proposals tailored to a selected hackathon and the user's builder profile.

---

### 1.2 Proposed Inputs
The ideation engine will consume structured, verified inputs from the existing HackPulse SQLite and MCP layer:

1. **Hackathon Context:**
   - **Theme & Domain:** Extracted title, description, and categorized tags (e.g., AI/Agent, Web3, Spatial, Voice).
   - **Official Rules & Constraints:** Submission requirements, required APIs/SDKs, and hosting rules verified by the Rule Auditor.
   - **Judging Criteria & Weightings:** Specific evaluation axes (e.g., Technical Depth 40%, Innovation 30%, Completeness 30%).
2. **Builder Profile:**
   - **Verified Skills:** Builder's declared skill set (e.g., Python, FastAPI, SQLite, MCP, React).
   - **Available Runway:** Weekly hours (e.g., 10h, 15h, 25h) and remaining days to deadline.
   - **Budget Constraints:** Hardware and cloud hosting budget (e.g., ₹0 free-tier constraints).

---

### 1.3 Proposed Outputs
For a selected opportunity, the generator will output 3 structured, actionable project concepts:

1. **Project Title & One-Sentence Pitch:** A concise, punchy project concept.
2. **Judging Criteria Fit:** Explicit mapping explaining why this concept targets high marks across the specific evaluation criteria.
3. **Core Architectural Differentiator:** What separates this project from typical generic hackathon submissions.
4. **15-Hour MVP Scope (Must-Haves vs Nice-to-Haves):** A realistic scope boundary that fits within the builder's runway.
5. **Zero-Cost Deployment Strategy:** Exact hosting architecture (e.g., FastAPI on Render free tier, client on GitHub Pages, SQLite WAL).

---

### 1.4 Architecture & Safety Constraints
- **Zero Paid Dependencies:** Will utilize local inference, open-weights models, or client-provided bring-your-own-key (BYOK) endpoints. No mandatory paid external APIs.
- **Strict Grounding:** Ideas will be explicitly grounded in the verified rules and deliverables extracted by the HackPulse Rule Auditor.
- **Clear Separation:** Kept distinct from the deterministic feasibility scoring and provenance auditing engine to maintain strict transparency and avoid hallucinated rule interpretations.

---

## 2. Additional Roadmap Items

- **Real-time Webhook Alerts:** Browser and ambient voice notifications when an audited hackathon announces a rule amendment or deadline shift.
- **Multi-Builder Team Sync:** Allowing teammates to share a synchronized sprint plan and distribute milestones.
- **Automated Devpost/Devfolio Submission Draft Export:** Exporting verified compliance declarations and README scaffolding directly to the project repository.
