---
name: hackpulse-advisor
description: Autonomous hackathon advisory skill using MCP tools for discovery, profile feasibility scoring, real-time rule provenance auditing, rule diff tracking, and calibrated 15-hour sprint planning.
---

# HackPulse Advisor Agent Skill

This skill equips agentic pair-programmers with systematic workflows to discover hackathons, evaluate builder feasibility, audit participation rules with verifiable provenance, track regulatory diffs, and generate time-boxed sprint plans.

## Available MCP Tools

The `hackpulse-alexa-mcp` backend exposes seven core tools:

1. **`search_hackathons`**: Discover open, ended, or upcoming hackathons with keyword, tag, location, and platform filters (`Devpost`, `Devfolio`, `Unstop`). Automatically computes deterministic `feasibility_score` (0-100) based on active builder profile constraints (`budget_and_location_fit`, `stack_and_domain_fit`, `timeline_fit`, `prize_and_impact_signal`).
2. **`get_hackathon_details`**: Fetch complete metadata, prize breakdowns, timeline dates, and canonical portal links.
3. **`sync_upstream_catalog`**: Refresh local SQLite state from remote upstream catalog or offline fallback seed.
4. **`audit_rules`**: Run rule and FAQ extraction with explicit provenance tracking (`source_url`, `retrieval_timestamp`, and `verification_status`: `verified_live` vs. `fallback_unverified`). Extracts `eligibility`, `team_size`, `submission_requirements`, and `hosting_requirements`.
5. **`diff_rules`**: Detect changes across rule revisions or test a candidate submission against audited requirements with risk scoring (`HIGH`, `MEDIUM`, `LOW`).
6. **`generate_sprint_plan`**: Create or update a persistent, time-boxed sprint plan calibrated to builder hours (default 15h/week), structured into 5 concrete milestones (M1–M5), an explicit Hour 6 Go/No-Go checkpoint, and hackathon reuse compliance documentation. Supports milestone checkbox toggles.
7. **`manage_builder_profile`**: Retrieve or update persistent cross-session builder profile attributes (`hours_per_week`, `budget_inr`, `prefer_online_only`, `skills`, or toggle bookmark IDs).

---

## 4-Stage Agentic Workflow

### Stage 1: Profile-Calibrated Discovery & Feasibility Ranking
1. Call `manage_builder_profile(action="get")` to inspect the builder's available hours (e.g. 15h/week), budget (e.g. ₹0), location preference (Online Only), and technical stack.
2. Call `search_hackathons(sort_by_feasibility=True, limit=10)`.
3. Filter out options that conflict with hard constraints (e.g., in-person travel for ₹0 budget builders).
4. Review `fit_reasons` and `risk_flags` for top-ranked opportunities.

### Stage 2: Rule Auditing & Provenance Verification (Guardrail Compliance)
Before advising any builder to begin work:
1. Always invoke `audit_rules(identifier=hackathon_id)`.
2. Inspect `verification_status`:
   - `verified_live`: Highlight live-inspected constraints, timestamp, and source URL.
   - `fallback_unverified`: Clearly state: *"Treated as unverified offline fallback per execution guardrails. Live HTTP fetch could not be confirmed."*
3. Check `hosting_requirements` specifically:
   - Identify whether post-submission cloud hosting is mandatory (e.g. keeping endpoint live through Nov 20) or if a demo recording suffices.

### Stage 3: Rule Diff & Risk Evaluation
When evaluating differences between promotional listings and official terms:
1. Invoke `diff_rules(identifier=hackathon_id, new_rule_text=candidate_terms)`.
2. Review `risk_level`:
   - `HIGH`: Deadline shifts, strict deliverable additions, or mandatory deployment rules.
   - `MEDIUM`: Eligibility or team size modifications.
   - `LOW`: Formatting, cosmetic, or documentation clarifications.

### Stage 4: Calibrated 15-Hour Sprint & Go/No-Go Planning
1. Invoke `generate_sprint_plan(hackathon_id=hackathon_id, available_hours=15)`.
2. Present the 5 concrete milestones:
   - **M1**: Scope & Upstream Data Bridge (2h)
   - **M2**: Core Backend / MCP Protocol & Provenance (4h)
   - **M3**: Centre-Stage Multimodal UI (4h)
   - **M4**: Zero-Cost Cloud Hosting & Verification (3h)
   - **M5**: 2.5-Min Demo Video & Submission (2h)
3. Enforce the **Hour 6 Go/No-Go Checkpoint**: verify core protocol and data before continuing.
4. Document compliance with hackathon reuse policies: upstream projects must remain untouched, read-only feeds while all new MCP and UI logic is built cleanly in the target repository.
