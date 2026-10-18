---
name: hackpulse-advisor
description: Autonomous hackathon advisory skill using MCP tools for discovery, real-time rule verification, hosting requirements analysis, and rule diff tracking.
---

# HackPulse Advisor Agent Skill

This skill equips agentic pair-programmers with systematic workflows to discover hackathons, verify participation rules, track regulatory diffs, and validate project hosting requirements.

## Available MCP Tools

The `hackpulse-alexa-mcp` backend exposes five core tools:

1. **`search_hackathons`**: Discover open, ended, or upcoming hackathons with keyword, tag, location, and platform filters (`Devpost`, `Devfolio`, `Unstop`).
2. **`get_hackathon_details`**: Fetch complete metadata, prize breakdowns, timeline dates, and canonical portal links.
3. **`sync_upstream_catalog`**: Refresh local database state from remote upstream catalog or offline fallback seed.
4. **`audit_rules`**: Run rule and FAQ extraction with explicit provenance tracking (`source_url`, `retrieval_timestamp`, and `verification_status`: `verified_live` vs. `fallback_unverified`).
5. **`diff_rules`**: Detect changes across rule versions or test a candidate submission against audited requirements.

---

## Standard Workflows

### 1. Hackathon Discovery & Filtering
When the user asks for hackathons in a domain (e.g. "Find AI hackathons with prizes"):
- Call `search_hackathons(query="ai", status="open", limit=10)`.
- If filtered results are empty, broaden by searching tags (`tag="ai"`) or sources.
- Present sorted options with normalized deadlines and prize amounts.

### 2. Rule & Hosting Verification (Guardrail Compliance)
Before advising a user to commit to a hackathon:
- Always call `audit_rules(identifier=hackathon_id)`.
- Check the `verification_status`:
  - If `verified_live`: Highlight live-inspected constraints.
  - If `fallback_unverified`: Clearly state: *"Treated as unverified offline fallback per execution guardrails. Live HTTP fetch could not be confirmed."*
- Check the `hosting_requirements` section specifically:
  - Correct any assumptions that require complex post-submission cloud infrastructure unless explicitly stated in official rules.

### 3. Change Detection & Deadline Tracking
If a hackathon deadline or terms might have shifted:
- Call `diff_rules(identifier=hackathon_id)`.
- Report `has_changes` and assess the `risk_level` (`HIGH` for deadline/deliverable changes).

### 4. Readiness & Pre-Submission Audit
Before submitting:
- Verify:
  1. Team size compliance (e.g. 1-4 members).
  2. Public code repository accessibility (e.g. GitHub link).
  3. Video duration limits (typically 2-3 minutes).
  4. Working demo or cloud endpoint accessibility.
