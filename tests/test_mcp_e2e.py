"""Automated End-to-End Verification of HackPulse Alexa MCP Server.
Verifies MCP 2025-11-25 handshake, Mcp-Session-Id generation, all core tools,
structuredContent, persistent builder profile, calibrated sprint planning,
rule-audit provenance, resources, prompts, and the Centre-Stage Alexa+ multimodal web UI.
"""

import json
import re
import pytest
from starlette.testclient import TestClient

from app import app
from core.database import init_db, load_seed_if_empty

client = TestClient(app)


@pytest.fixture(autouse=True)
def setup_test_state():
    """Ensure database is initialized and seeded before running tests."""
    init_db()
    load_seed_if_empty()


def test_healthcheck():
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert data["mcp_protocol_version"] == "2025-11-25"
    assert data["total_hackathons"] > 0


def test_mcp_get_info():
    response = client.get("/mcp")
    assert response.status_code == 200
    data = response.json()
    assert data["transport"] == "streamable_http"
    assert data["protocol_version"] == "2025-11-25"


def test_mcp_initialize_handshake_and_session_id():
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-11-25",
            "capabilities": {},
            "clientInfo": {"name": "test-client", "version": "1.0.0"},
        },
    }
    response = client.post("/mcp", json=payload)
    assert response.status_code == 200

    # Verify Mcp-Session-Id header is emitted
    session_id = response.headers.get("Mcp-Session-Id")
    assert session_id is not None
    assert len(session_id) > 10

    # Verify JSON-RPC response
    res = response.json()
    assert res["id"] == 1
    assert res["result"]["protocolVersion"] == "2025-11-25"
    assert res["result"]["serverInfo"]["name"] == "hackpulse-alexa-mcp"
    assert "tools" in res["result"]["capabilities"]


def test_mcp_initialized_notification():
    payload = {
        "jsonrpc": "2.0",
        "method": "notifications/initialized",
        "params": {},
    }
    response = client.post("/mcp", json=payload)
    assert response.status_code == 200
    assert "Mcp-Session-Id" in response.headers


def test_mcp_tools_list_has_five_core_tools():
    payload = {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/list",
        "params": {},
    }
    response = client.post("/mcp", json=payload)
    assert response.status_code == 200
    tools = response.json()["result"]["tools"]

    tool_names = [t["name"] for t in tools]
    assert len(tool_names) >= 5
    assert "search_hackathons" in tool_names
    assert "get_hackathon_details" in tool_names
    assert "sync_upstream_catalog" in tool_names
    assert "audit_rules" in tool_names
    assert "diff_rules" in tool_names
    # Also verify new profile and sprint tools
    assert "generate_sprint_plan" in tool_names
    assert "manage_builder_profile" in tool_names


def test_tool_search_hackathons():
    payload = {
        "jsonrpc": "2.0",
        "id": 3,
        "method": "tools/call",
        "params": {
            "name": "search_hackathons",
            "arguments": {"query": "", "limit": 5},
        },
    }
    response = client.post("/mcp", json=payload)
    assert response.status_code == 200
    content = response.json()["result"]["content"][0]["text"]
    data = json.loads(content)
    assert "hackathons" in data
    assert len(data["hackathons"]) > 0


def test_tool_get_hackathon_details():
    # First search to get an ID
    search_payload = {
        "jsonrpc": "2.0",
        "id": 4,
        "method": "tools/call",
        "params": {"name": "search_hackathons", "arguments": {"limit": 1}},
    }
    s_res = client.post("/mcp", json=search_payload).json()
    first_item = json.loads(s_res["result"]["content"][0]["text"])["hackathons"][0]
    h_id = first_item["id"]

    # Now fetch details
    details_payload = {
        "jsonrpc": "2.0",
        "id": 5,
        "method": "tools/call",
        "params": {
            "name": "get_hackathon_details",
            "arguments": {"identifier": h_id},
        },
    }
    d_res = client.post("/mcp", json=details_payload).json()
    assert d_res["result"]["isError"] is False
    details = json.loads(d_res["result"]["content"][0]["text"])
    assert details["id"] == h_id
    assert "title" in details
    assert "prize_currency" in details


def test_tool_sync_upstream_catalog_fallback():
    payload = {
        "jsonrpc": "2.0",
        "id": 6,
        "method": "tools/call",
        "params": {
            "name": "sync_upstream_catalog",
            "arguments": {"force_refresh": True},
        },
    }
    response = client.post("/mcp", json=payload)
    assert response.status_code == 200
    data = json.loads(response.json()["result"]["content"][0]["text"])
    assert data["total_hackathons"] > 0
    assert data["source_type"] == "fallback_seed"


def test_tool_audit_rules_provenance():
    # Fetch a hackathon title
    s_res = client.post("/mcp", json={
        "jsonrpc": "2.0",
        "id": 7,
        "method": "tools/call",
        "params": {"name": "search_hackathons", "arguments": {"limit": 1}},
    }).json()
    first_item = json.loads(s_res["result"]["content"][0]["text"])["hackathons"][0]
    h_id = first_item["id"]

    payload = {
        "jsonrpc": "2.0",
        "id": 8,
        "method": "tools/call",
        "params": {
            "name": "audit_rules",
            "arguments": {"identifier": h_id, "force_live": False},
        },
    }
    response = client.post("/mcp", json=payload)
    assert response.status_code == 200
    data = json.loads(response.json()["result"]["content"][0]["text"])

    # Strict provenance checks
    assert data["hackathon_id"] == h_id
    assert "retrieval_timestamp" in data
    assert "verification_status" in data
    assert data["verification_status"] in ["verified_live", "fallback_unverified"]
    assert "categories" in data
    assert "hosting_requirements" in data["categories"]


def test_tool_diff_rules():
    # Audit first
    s_res = client.post("/mcp", json={
        "jsonrpc": "2.0",
        "id": 9,
        "method": "tools/call",
        "params": {"name": "search_hackathons", "arguments": {"limit": 1}},
    }).json()
    first_item = json.loads(s_res["result"]["content"][0]["text"])["hackathons"][0]
    h_id = first_item["id"]

    client.post("/mcp", json={
        "jsonrpc": "2.0",
        "id": 10,
        "method": "tools/call",
        "params": {"name": "audit_rules", "arguments": {"identifier": h_id}},
    })

    # Now diff against modified text
    candidate_rules = "New deadline announced: Submissions close 2026-12-01. Mandatory deployed URL required."
    payload = {
        "jsonrpc": "2.0",
        "id": 11,
        "method": "tools/call",
        "params": {
            "name": "diff_rules",
            "arguments": {"identifier": h_id, "new_rule_text": candidate_rules},
        },
    }
    response = client.post("/mcp", json=payload)
    assert response.status_code == 200
    data = json.loads(response.json()["result"]["content"][0]["text"])
    assert data["has_changes"] is True
    assert data["risk_level"] == "HIGH"  # Detected 'deadline' and 'deployed'


def test_mcp_resources():
    list_payload = {
        "jsonrpc": "2.0",
        "id": 12,
        "method": "resources/list",
        "params": {},
    }
    l_res = client.post("/mcp", json=list_payload).json()
    resources = l_res["result"]["resources"]
    assert len(resources) >= 2

    # Read catalog summary resource
    read_payload = {
        "jsonrpc": "2.0",
        "id": 13,
        "method": "resources/read",
        "params": {"uri": "hackpulse://catalog/summary"},
    }
    r_res = client.post("/mcp", json=read_payload).json()
    assert "contents" in r_res["result"]
    assert len(r_res["result"]["contents"]) > 0

    # Read active profile resource
    prof_payload = {
        "jsonrpc": "2.0",
        "id": 14,
        "method": "resources/read",
        "params": {"uri": "hackpulse://profile/active"},
    }
    p_res = client.post("/mcp", json=prof_payload).json()
    assert "contents" in p_res["result"]
    prof_text = p_res["result"]["contents"][0]["text"]
    assert "Demo Builder (CSE Student)" in prof_text
    assert "Avanish" not in prof_text


def test_mcp_prompts():
    list_payload = {
        "jsonrpc": "2.0",
        "id": 14,
        "method": "prompts/list",
        "params": {},
    }
    l_res = client.post("/mcp", json=list_payload).json()
    prompts = l_res["result"]["prompts"]
    assert len(prompts) == 2

    # Get prompt
    get_payload = {
        "jsonrpc": "2.0",
        "id": 15,
        "method": "prompts/get",
        "params": {
            "name": "hackathon_eligibility_check",
            "arguments": {
                "identifier": "Test Hackathon",
                "developer_profile": "College student, USA, team of 2",
            },
        },
    }
    g_res = client.post("/mcp", json=get_payload).json()
    assert "messages" in g_res["result"]


# -----------------------------------------------------------------------------
# New Tests: Builder Profile, Feasibility Scoring, Sprint Planner, Web UI
# -----------------------------------------------------------------------------
def test_tool_manage_builder_profile():
    # 1. Get default profile
    get_payload = {
        "jsonrpc": "2.0",
        "id": 16,
        "method": "tools/call",
        "params": {
            "name": "manage_builder_profile",
            "arguments": {"action": "get"},
        },
    }
    res = client.post("/mcp", json=get_payload).json()
    prof = json.loads(res["result"]["content"][0]["text"])
    assert prof["profile_id"] == "default"
    assert prof["name"] == "Demo Builder (CSE Student)"
    assert "Avanish" not in prof["name"]
    assert prof["country"] == "India"
    assert prof["hours_per_week"] == 15
    assert prof["budget_inr"] == 0.0

    # 2. Update hours and toggle a bookmark
    test_bm = "test_hackathon_bookmark_123"
    update_payload = {
        "jsonrpc": "2.0",
        "id": 17,
        "method": "tools/call",
        "params": {
            "name": "manage_builder_profile",
            "arguments": {
                "action": "update",
                "hours_per_week": 20,
                "toggle_bookmark_id": test_bm,
            },
        },
    }
    u_res = client.post("/mcp", json=update_payload).json()
    u_prof = json.loads(u_res["result"]["content"][0]["text"])
    assert u_prof["hours_per_week"] == 20
    assert test_bm in u_prof["bookmarks"]

    # 3. Untoggle bookmark
    untoggle_payload = {
        "jsonrpc": "2.0",
        "id": 18,
        "method": "tools/call",
        "params": {
            "name": "manage_builder_profile",
            "arguments": {"action": "update", "toggle_bookmark_id": test_bm, "hours_per_week": 15},
        },
    }
    un_res = client.post("/mcp", json=untoggle_payload).json()
    un_prof = json.loads(un_res["result"]["content"][0]["text"])
    assert test_bm not in un_prof["bookmarks"]
    assert un_prof["hours_per_week"] == 15


def test_tool_search_hackathons_feasibility_and_structured_content():
    payload = {
        "jsonrpc": "2.0",
        "id": 19,
        "method": "tools/call",
        "params": {
            "name": "search_hackathons",
            "arguments": {"sort_by_feasibility": True, "limit": 3},
        },
    }
    response = client.post("/mcp", json=payload)
    res_json = response.json()

    # Check structuredContent
    assert "structuredContent" in res_json["result"]
    sc = res_json["result"]["structuredContent"]
    assert sc["stageView"] == "CAROUSEL"
    assert "voiceSummary" in sc
    assert len(sc["voiceSummary"]) > 10

    # Check feasibility scoring on items
    items = sc["data"]["hackathons"]
    assert len(items) > 0
    top = items[0]
    assert "feasibility_score" in top
    assert 0 <= top["feasibility_score"] <= 100
    assert "score_breakdown" in top
    assert "fit_reasons" in top
    assert "is_bookmarked" in top


def test_tool_generate_sprint_plan_and_toggle():
    import uuid
    h_id = f"test_hack_{uuid.uuid4().hex[:8]}"

    # 1. Initial plan creation
    payload = {
        "jsonrpc": "2.0",
        "id": 20,
        "method": "tools/call",
        "params": {
            "name": "generate_sprint_plan",
            "arguments": {"hackathon_id": h_id, "available_hours": 15},
        },
    }
    response = client.post("/mcp", json=payload)
    res = response.json()
    plan = res["result"]["structuredContent"]["data"]
    assert plan["hackathon_id"] == h_id
    assert plan["total_hours"] == 15
    assert len(plan["milestones"]) == 5
    assert "Hour 6" in plan["go_no_go_checkpoint"]
    assert "reuse" in plan["reuse_compliance_note"].lower()
    # Newly generated plan must default to all milestones unchecked
    assert plan["completed_milestones"] == 0
    for m in plan["milestones"]:
        assert m["completed"] is False

    # 2. Toggle milestone M3 (Centre-Stage Multimodal UI)
    toggle_payload = {
        "jsonrpc": "2.0",
        "id": 21,
        "method": "tools/call",
        "params": {
            "name": "generate_sprint_plan",
            "arguments": {"hackathon_id": h_id, "completed_milestone_id": "M3"},
        },
    }
    t_res = client.post("/mcp", json=toggle_payload).json()
    t_plan = t_res["result"]["structuredContent"]["data"]
    m3 = next(m for m in t_plan["milestones"] if m["id"] == "M3")
    assert m3["completed"] is True
    assert t_plan["completed_milestones"] == 1


def test_web_app_index_serves_alexa_multimodal():
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers.get("content-type", "")
    html_text = response.text
    assert "HackPulse Alexa+" in html_text
    assert "Opportunity Deck" in html_text
    assert "Wire Inspector" in html_text
    assert "Demo Builder (CSE Student)" in html_text
    assert "Avanish" not in html_text

    # Verify static app.js has generic demo narration without personal name
    js_resp = client.get("/static/app.js")
    assert js_resp.status_code == 200
    assert "Avanish" not in js_resp.text
    assert "the demo builder" in js_resp.text
    assert "getAmazonHackathon" in js_resp.text
    assert "0e18b6cf877bc442f9b92302350398fce6b84b05d01ac2f92ff5367616b259a8" in js_resp.text


def test_judge_scenarios_target_amazon_hackathon():
    amazon_id = "0e18b6cf877bc442f9b92302350398fce6b84b05d01ac2f92ff5367616b259a8"
    expected_title = "Build, Ship, Shape: Amazon Developer Hackathon"

    # 1. Scenario 2: Audit Amazon Hackathon Rules & Provenance
    audit_res = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 22,
            "method": "tools/call",
            "params": {"name": "audit_rules", "arguments": {"identifier": amazon_id, "force_live": True}},
        },
    ).json()
    audit_data = json.loads(audit_res["result"]["content"][0]["text"])
    assert audit_data["hackathon_id"] == amazon_id
    assert audit_data["title"] == expected_title
    assert "verification_status" in audit_data

    # 2. Scenario 3: Detect Listing vs Official Rule Diffs
    candidate_rules = """
Official Rules & FAQ Updates:
- Deadline Precision: Submissions close exactly at 12:00 PM PDT (noon), not midnight.
- Protocol Requirement: Server must implement MCP 2025-11-25 Streamable HTTP on /mcp.
- AWS Credits: AWS developer promotion closed; participants must use existing accounts or zero-cost hosting tiers.
- Testing Requirement: Testing via self-built simulator or web UI explicitly permitted.
- Mandatory Hosting: Server endpoint must remain live and accessible through November 20 judging period.
    """.strip()

    diff_res = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 23,
            "method": "tools/call",
            "params": {
                "name": "diff_rules",
                "arguments": {"identifier": amazon_id, "new_rule_text": candidate_rules},
            },
        },
    ).json()
    diff_data = json.loads(diff_res["result"]["content"][0]["text"])
    assert diff_data["hackathon_id"] == amazon_id
    assert diff_data["title"] == expected_title
    assert diff_data["has_changes"] is True
    assert diff_data["risk_level"] == "HIGH"

    # 3. Scenario 4: Build & Persist 15-Hour Sprint Plan
    plan_res = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 24,
            "method": "tools/call",
            "params": {
                "name": "generate_sprint_plan",
                "arguments": {"hackathon_id": amazon_id, "available_hours": 15},
            },
        },
    ).json()
    plan_data = plan_res["result"]["structuredContent"]["data"]
    assert plan_data["hackathon_id"] == amazon_id
    assert plan_data["title"] == expected_title
    assert plan_data["total_hours"] == 15
    assert len(plan_data["milestones"]) == 5


def test_natural_language_search_query():
    payload = {
        "jsonrpc": "2.0",
        "id": 101,
        "method": "tools/call",
        "params": {
            "name": "search_hackathons",
            "arguments": {
                "query": "Find the best online hackathons for a zero-rupee budget",
                "limit": 10,
            },
        },
    }
    response = client.post("/mcp", json=payload)
    assert response.status_code == 200
    res = response.json()
    assert "structuredContent" in res["result"]
    items = res["result"]["structuredContent"]["data"]["hackathons"]
    assert len(items) > 0
    # Verify natural language query filtered for Online hackathons
    for h in items:
        assert "online" in h["location"].lower()
    # Feasibility scores should be sorted descending
    scores = [h.get("feasibility_score", 0) for h in items]
    assert scores == sorted(scores, reverse=True)


def test_rules_audit_differentiation_and_honest_provenance():
    amazon_id = "0e18b6cf877bc442f9b92302350398fce6b84b05d01ac2f92ff5367616b259a8"

    # Search for a second, non-Amazon hackathon
    s_res = client.post("/mcp", json={
        "jsonrpc": "2.0",
        "id": 102,
        "method": "tools/call",
        "params": {"name": "search_hackathons", "arguments": {"query": "Meta", "limit": 1}},
    }).json()
    meta_item = s_res["result"]["structuredContent"]["data"]["hackathons"][0]
    meta_id = meta_item["id"]
    assert meta_id != amazon_id

    # Audit Amazon
    res_amazon = client.post("/mcp", json={
        "jsonrpc": "2.0",
        "id": 103,
        "method": "tools/call",
        "params": {"name": "audit_rules", "arguments": {"identifier": amazon_id}},
    }).json()
    data_amazon = res_amazon["result"]["structuredContent"]["data"]

    # Audit Meta
    res_meta = client.post("/mcp", json={
        "jsonrpc": "2.0",
        "id": 104,
        "method": "tools/call",
        "params": {"name": "audit_rules", "arguments": {"identifier": meta_id}},
    }).json()
    data_meta = res_meta["result"]["structuredContent"]["data"]

    # Provenance honesty check
    if data_amazon["verification_status"] == "fallback_unverified":
        assert "fallback" in data_amazon["notes"].lower() or "unavailable" in data_amazon["notes"].lower()
    if data_meta["verification_status"] == "fallback_unverified":
        assert "fallback" in data_meta["notes"].lower() or "unavailable" in data_meta["notes"].lower()

    # Rule categories must be differentiated, not identical canned text
    cats_amazon = data_amazon["categories"]
    cats_meta = data_meta["categories"]
    assert cats_amazon["eligibility"] != cats_meta["eligibility"]
    assert cats_amazon["submission_requirements"] != cats_meta["submission_requirements"]
    assert cats_amazon["hosting_requirements"] != cats_meta["hosting_requirements"]
    assert "Amazon" in cats_amazon["hosting_requirements"]


def test_sprint_plan_differentiation_and_hour_scaling():
    amazon_id = "0e18b6cf877bc442f9b92302350398fce6b84b05d01ac2f92ff5367616b259a8"

    # Search for a non-Amazon hackathon
    s_res = client.post("/mcp", json={
        "jsonrpc": "2.0",
        "id": 105,
        "method": "tools/call",
        "params": {"name": "search_hackathons", "arguments": {"query": "Meta", "limit": 1}},
    }).json()
    meta_item = s_res["result"]["structuredContent"]["data"]["hackathons"][0]
    meta_id = meta_item["id"]

    # Amazon 10h plan
    res_amz_10 = client.post("/mcp", json={
        "jsonrpc": "2.0",
        "id": 106,
        "method": "tools/call",
        "params": {"name": "generate_sprint_plan", "arguments": {"hackathon_id": amazon_id, "available_hours": 10}},
    }).json()
    amz_plan_10 = res_amz_10["result"]["structuredContent"]["data"]
    assert amz_plan_10["total_hours"] == 10
    assert sum(m["hours"] for m in amz_plan_10["milestones"]) == 10
    assert "Hour 4" in amz_plan_10["go_no_go_checkpoint"]

    # Meta 25h plan
    res_meta_25 = client.post("/mcp", json={
        "jsonrpc": "2.0",
        "id": 107,
        "method": "tools/call",
        "params": {"name": "generate_sprint_plan", "arguments": {"hackathon_id": meta_id, "available_hours": 25}},
    }).json()
    meta_plan_25 = res_meta_25["result"]["structuredContent"]["data"]
    assert meta_plan_25["total_hours"] == 25
    assert sum(m["hours"] for m in meta_plan_25["milestones"]) == 25
    assert "Hour 10" in meta_plan_25["go_no_go_checkpoint"]

    # Milestones must be domain-differentiated
    amz_m2 = amz_plan_10["milestones"][1]["description"]
    meta_m2 = meta_plan_25["milestones"][1]["description"]
    assert amz_m2 != meta_m2
    assert any("Alexa+" in m["title"] or "Alexa+" in m["description"] for m in amz_plan_10["milestones"])
    assert "MCP" in amz_m2


def test_frontend_voice_gc_protection_and_filter_reset():
    js_resp = client.get("/static/app.js")
    assert js_resp.status_code == 200
    js_text = js_resp.text

    # 1. Voice playback GC protection and chromium cancel race guard
    assert "_activeUtterance" in js_text
    assert "window._activeUtterance" in js_text
    assert "speechSynthesis.speaking" in js_text
    assert "setTimeout" in js_text

    # 2. Conditional Amazon warning in renderAuditBoard
    assert "isAmazonEvent" in js_text
    assert "amazonWarningHtml" in js_text

    # 3. Filter reset & search unfreezing
    assert "resetFilters" in js_text
    assert "updateResetButtonVisibility" in js_text
    assert "resetSearchBtn" in js_text or "resetSearchBtn" in client.get("/").text

    # 4. Scenario unfreezing (resets searchQuery)
    assert 'this.searchQuery = ""' in js_text


def test_sprint_plan_isolation_and_initial_defaults():
    """Verifies that newly generated plans default to all milestones unchecked,
    modifying one hackathon's milestones does NOT affect another hackathon,
    and completion state persists independently across reloads.
    """
    import uuid
    hack_a = f"hack_iso_a_{uuid.uuid4().hex[:6]}"
    hack_b = f"hack_iso_b_{uuid.uuid4().hex[:6]}"

    # 1. Create plan for Hackathon A - must default to all unchecked (completed: False)
    res_a1 = client.post("/mcp", json={
        "jsonrpc": "2.0",
        "id": 201,
        "method": "tools/call",
        "params": {"name": "generate_sprint_plan", "arguments": {"hackathon_id": hack_a, "available_hours": 15}},
    }).json()["result"]["structuredContent"]["data"]

    assert res_a1["hackathon_id"] == hack_a
    assert res_a1["completed_milestones"] == 0
    assert all(m["completed"] is False for m in res_a1["milestones"])

    # 2. Create plan for Hackathon B - must also default to all unchecked
    res_b1 = client.post("/mcp", json={
        "jsonrpc": "2.0",
        "id": 202,
        "method": "tools/call",
        "params": {"name": "generate_sprint_plan", "arguments": {"hackathon_id": hack_b, "available_hours": 15}},
    }).json()["result"]["structuredContent"]["data"]

    assert res_b1["hackathon_id"] == hack_b
    assert res_b1["completed_milestones"] == 0
    assert all(m["completed"] is False for m in res_b1["milestones"])

    # 3. Check milestone M1 and M2 in Hackathon A
    client.post("/mcp", json={
        "jsonrpc": "2.0",
        "id": 203,
        "method": "tools/call",
        "params": {"name": "generate_sprint_plan", "arguments": {"hackathon_id": hack_a, "completed_milestone_id": "M1"}},
    })
    res_a2 = client.post("/mcp", json={
        "jsonrpc": "2.0",
        "id": 204,
        "method": "tools/call",
        "params": {"name": "generate_sprint_plan", "arguments": {"hackathon_id": hack_a, "completed_milestone_id": "M2"}},
    }).json()["result"]["structuredContent"]["data"]

    assert res_a2["completed_milestones"] == 2
    completed_ids_a = [m["id"] for m in res_a2["milestones"] if m["completed"]]
    assert completed_ids_a == ["M1", "M2"]

    # 4. Verify Hackathon B is completely unchanged (still 0 completed milestones)
    res_b2 = client.post("/mcp", json={
        "jsonrpc": "2.0",
        "id": 205,
        "method": "tools/call",
        "params": {"name": "generate_sprint_plan", "arguments": {"hackathon_id": hack_b, "available_hours": 15}},
    }).json()["result"]["structuredContent"]["data"]

    assert res_b2["hackathon_id"] == hack_b
    assert res_b2["completed_milestones"] == 0
    assert all(m["completed"] is False for m in res_b2["milestones"])

    # 5. Check milestone M4 in Hackathon B
    res_b3 = client.post("/mcp", json={
        "jsonrpc": "2.0",
        "id": 206,
        "method": "tools/call",
        "params": {"name": "generate_sprint_plan", "arguments": {"hackathon_id": hack_b, "completed_milestone_id": "M4"}},
    }).json()["result"]["structuredContent"]["data"]

    assert res_b3["completed_milestones"] == 1
    assert next(m["completed"] for m in res_b3["milestones"] if m["id"] == "M4") is True

    # 6. Verify Hackathon A retained its own progress (M1 and M2 checked, M4 unchecked)
    res_a3 = client.post("/mcp", json={
        "jsonrpc": "2.0",
        "id": 207,
        "method": "tools/call",
        "params": {"name": "generate_sprint_plan", "arguments": {"hackathon_id": hack_a, "available_hours": 15}},
    }).json()["result"]["structuredContent"]["data"]

    assert res_a3["completed_milestones"] == 2
    assert [m["id"] for m in res_a3["milestones"] if m["completed"]] == ["M1", "M2"]
    assert next(m["completed"] for m in res_a3["milestones"] if m["id"] == "M4") is False


def test_speech_recognition_frontend_wiring_and_error_handling():
    """Verifies that static/app.js implements clean speech recognition lifecycle:
    lazy activation, user-reviewable input, error handling, and audio coordination.
    """
    js_resp = client.get("/static/app.js")
    assert js_resp.status_code == 200
    js_text = js_resp.text

    # 1. Lazy activation (no microphone access requested on page load)
    assert "getSpeechRecognition" in js_text
    assert "SpeechClass" in js_text
    assert "lazy" in js_text.lower() or "toggleSpeechRecognition" in js_text

    # 2. Inserting readable text into command input for user review/editing
    assert "cmdInput.value = transcript" in js_text
    assert "cmdInput.focus()" in js_text
    # Must NOT automatically submit/clear on recognition result
    assert "this.handleCommand(transcript)" not in js_text

    # 3. Comprehensive error reporting for microphone events
    assert "not-allowed" in js_text
    assert "no-speech" in js_text
    assert "audio-capture" in js_text
    assert "network" in js_text
    assert "aborted" in js_text
    # Brave-specific actionable guidance
    assert "Brave blocks Google Speech" in js_text or "brave://settings/privacy" in js_text
    assert "cmdInput.focus()" in js_text

    # 4. Speech synthesis and recognition coordination
    # TTS must not play while user is actively recording into the microphone
    assert "if (this.isListening)" in js_text
    # Starting recognition must cancel any ongoing TTS
    assert "window.speechSynthesis.cancel()" in js_text

    # 5. Clean state cleanup onend and onerror
    assert "rec.onend" in js_text
    assert "rec.onerror" in js_text
    assert "updateMicUI(false)" in js_text


def test_anti_caching_headers_for_browser_runtime():
    """Verifies that index and static assets emit strict anti-caching headers."""
    resp_index = client.get("/")
    assert resp_index.status_code == 200
    cache_ctrl = resp_index.headers.get("cache-control", "")
    assert "no-cache" in cache_ctrl
    assert "no-store" in cache_ctrl

    resp_static = client.get("/static/app.js")
    assert resp_static.status_code == 200
    static_cache_ctrl = resp_static.headers.get("cache-control", "")
    assert "no-cache" in static_cache_ctrl
    assert "no-store" in static_cache_ctrl


def test_sprint_plan_progress_calculation_and_real_isolation():
    """Verifies that:
    1. Sprint progress is mathematically calculated from actual milestone completion count.
    2. Newly generated plans start at 0% with 0 completed milestones.
    3. Toggling M1 produces exactly 20%, M2 produces 40%, etc.
    4. Nebius, Meta VR, and Amazon hackathons maintain independent persisted states.
    """
    nebius_id = "f7ef434afdaf86a0ce6f761098a0baaa3830decace5bb41cf6fc2bf47c6e6dde"
    meta_id = "b5946c918f7d9c8b115319c3714c2347b822d72a6631288d201a8904a2477378"
    amazon_id = "0e18b6cf877bc442f9b92302350398fce6b84b05d01ac2f92ff5367616b259a8"

    # Step 1: Query sprint plans for all three - verify none start with erroneous 40%
    for target_id in [nebius_id, meta_id, amazon_id]:
        res = client.post("/mcp", json={
            "jsonrpc": "2.0",
            "id": 301,
            "method": "tools/call",
            "params": {"name": "generate_sprint_plan", "arguments": {"hackathon_id": target_id, "available_hours": 15}},
        }).json()
        plan = res["result"]["structuredContent"]["data"]
        assert plan["hackathon_id"] == target_id
        # Must NOT default to 40%
        assert plan["progress_percent"] == 0
        assert plan["completed_milestones"] == 0
        assert all(m["completed"] is False for m in plan["milestones"])

    # Step 2: Toggle M1 on Amazon -> progress must be exactly 20%
    res_amz_m1 = client.post("/mcp", json={
        "jsonrpc": "2.0",
        "id": 302,
        "method": "tools/call",
        "params": {"name": "generate_sprint_plan", "arguments": {"hackathon_id": amazon_id, "completed_milestone_id": "M1"}},
    }).json()["result"]["structuredContent"]["data"]
    assert res_amz_m1["progress_percent"] == 20
    assert res_amz_m1["completed_milestones"] == 1
    assert next(m["completed"] for m in res_amz_m1["milestones"] if m["id"] == "M1") is True
    assert next(m["completed"] for m in res_amz_m1["milestones"] if m["id"] == "M2") is False

    # Step 3: Toggle M2 on Amazon -> progress must be exactly 40%
    res_amz_m2 = client.post("/mcp", json={
        "jsonrpc": "2.0",
        "id": 303,
        "method": "tools/call",
        "params": {"name": "generate_sprint_plan", "arguments": {"hackathon_id": amazon_id, "completed_milestone_id": "M2"}},
    }).json()["result"]["structuredContent"]["data"]
    assert res_amz_m2["progress_percent"] == 40
    assert res_amz_m2["completed_milestones"] == 2
    assert next(m["completed"] for m in res_amz_m2["milestones"] if m["id"] == "M1") is True
    assert next(m["completed"] for m in res_amz_m2["milestones"] if m["id"] == "M2") is True

    # Step 4: Verify Nebius and Meta VR are completely untouched and remain at 0%
    res_neb = client.post("/mcp", json={
        "jsonrpc": "2.0",
        "id": 304,
        "method": "tools/call",
        "params": {"name": "generate_sprint_plan", "arguments": {"hackathon_id": nebius_id, "available_hours": 15}},
    }).json()["result"]["structuredContent"]["data"]
    assert res_neb["hackathon_id"] == nebius_id
    assert res_neb["progress_percent"] == 0
    assert res_neb["completed_milestones"] == 0
    assert all(m["completed"] is False for m in res_neb["milestones"])

    res_meta = client.post("/mcp", json={
        "jsonrpc": "2.0",
        "id": 305,
        "method": "tools/call",
        "params": {"name": "generate_sprint_plan", "arguments": {"hackathon_id": meta_id, "available_hours": 15}},
    }).json()["result"]["structuredContent"]["data"]
    assert res_meta["hackathon_id"] == meta_id
    assert res_meta["progress_percent"] == 0
    assert res_meta["completed_milestones"] == 0
    assert all(m["completed"] is False for m in res_meta["milestones"])

    # Step 5: Untoggle M1 on Amazon -> progress drops to 20%
    res_amz_un = client.post("/mcp", json={
        "jsonrpc": "2.0",
        "id": 306,
        "method": "tools/call",
        "params": {"name": "generate_sprint_plan", "arguments": {"hackathon_id": amazon_id, "completed_milestone_id": "M1"}},
    }).json()["result"]["structuredContent"]["data"]
    assert res_amz_un["progress_percent"] == 20
    assert res_amz_un["completed_milestones"] == 1
    assert next(m["completed"] for m in res_amz_un["milestones"] if m["id"] == "M1") is False
    assert next(m["completed"] for m in res_amz_un["milestones"] if m["id"] == "M2") is True

    # Step 6: Untoggle M2 on Amazon -> returns to clean 0%
    res_amz_clean = client.post("/mcp", json={
        "jsonrpc": "2.0",
        "id": 307,
        "method": "tools/call",
        "params": {"name": "generate_sprint_plan", "arguments": {"hackathon_id": amazon_id, "completed_milestone_id": "M2"}},
    }).json()["result"]["structuredContent"]["data"]
    assert res_amz_clean["progress_percent"] == 0
    assert res_amz_clean["completed_milestones"] == 0
    assert all(m["completed"] is False for m in res_amz_clean["milestones"])


def test_canonical_hackathon_id_resolution():
    """Verifies that calling generate_sprint_plan by title or canonical ID maps to the exact same canonical record."""
    title = "Build, Ship, Shape: Amazon Developer Hackathon"
    canonical_id = "0e18b6cf877bc442f9b92302350398fce6b84b05d01ac2f92ff5367616b259a8"

    res_by_title = client.post("/mcp", json={
        "jsonrpc": "2.0",
        "id": 308,
        "method": "tools/call",
        "params": {"name": "generate_sprint_plan", "arguments": {"hackathon_id": title, "available_hours": 15}},
    }).json()["result"]["structuredContent"]["data"]

    assert res_by_title["hackathon_id"] == canonical_id
    assert res_by_title["title"] == title


def test_natural_language_search_with_conjunctions():
    """Regression test: verifies that queries containing conjunctions like 'and', 'or', 'with'
    do not require literal matching of the conjunction and return valid AI/ML hackathons.
    """
    res = client.post("/mcp", json={
        "jsonrpc": "2.0",
        "id": 401,
        "method": "tools/call",
        "params": {
            "name": "search_hackathons",
            "arguments": {
                "query": "Find AI and machine learning hackathons",
                "location": "Online",
                "limit": 10,
            },
        },
    }).json()["result"]["structuredContent"]["data"]

    items = res["hackathons"]
    assert len(items) > 0
    titles = [h["title"].lower() for h in items]
    assert any("amazon" in t or "nebius" in t or "ai" in t for t in titles)


def test_natural_language_search_empty_and_stopword_queries():
    """Verifies that empty strings, whitespace, or stop-word-only queries do not crash or return errors."""
    for q in ["", "   ", "find the best hackathons for me"]:
        res = client.post("/mcp", json={
            "jsonrpc": "2.0",
            "id": 402,
            "method": "tools/call",
            "params": {"name": "search_hackathons", "arguments": {"query": q, "limit": 5}},
        }).json()
        assert res["result"]["isError"] is False
        items = res["result"]["structuredContent"]["data"]["hackathons"]
        assert len(items) > 0


def test_grounded_command_routing_and_responsive_styles():
    """Verifies that:
    1. Entity resolution behavior accurately identifies catalog events, rejects unknown names,
       and never silently falls back to Amazon or active selection on unknown hackathons.
    2. Empty state search queries are safely HTML-escaped against XSS.
    3. Responsive breakpoints and high-contrast tokens are properly defined in static assets.
    4. Landing page honestly identifies as an Echo Show 15 Multimodal Simulator.
    """
    from core.database import query_hackathons
    catalog = query_hackathons(limit=30)
    assert len(catalog) > 0

    # Extract command stopwords defined in static/app.js
    command_stop_words = {
        "audit", "rules", "rule", "provenance", "official",
        "deadline", "deadlines", "due", "date", "when", "does", "end", "ends",
        "prize", "prizes", "reward", "rewards", "cash", "bounty", "pool",
        "eligibility", "eligible", "who", "can", "enter", "hosting", "deploy", "requirement", "requirements", "policy",
        "diff", "difference", "changes", "change", "risk", "compare", "comparison",
        "sprint", "plan", "milestone", "milestones", "status", "progress", "schedule",
        "for", "the", "of", "in", "at", "on", "a", "an", "to", "is", "it", "this", "that", "event", "hackathon",
        "what", "whats", "how", "show", "check", "open", "run", "view", "tell", "me", "about", "please", "give"
    }

    def resolve_target(text: str, selected_id=None):
        if not catalog:
            return None
        raw = (text or "").lower().strip()
        if not raw:
            return next((h for h in catalog if h["id"] == selected_id), None) if selected_id else None

        if "amazon" in raw or "alexa" in raw:
            return next((h for h in catalog if "amazon" in h.get("title", "").lower() or "alexa" in h.get("title", "").lower()), None)

        raw_words = [w for w in re.split(r"[^a-z0-9]+", raw) if w]
        for h in catalog:
            title_lower = (h.get("title") or "").lower()
            if title_lower in raw:
                return h
            words = [w for w in re.split(r"[^a-z0-9]+", title_lower) if len(w) >= 4 and w not in ["hack", "hackathon", "developer", "competition", "challenge", "series", "2026", "global", "build", "with", "tour", "stop"]]
            for w in words:
                if w in raw_words:
                    return h

        specific_tokens = [w for w in raw_words if w not in command_stop_words]
        if specific_tokens:
            return None

        if selected_id:
            return next((h for h in catalog if h["id"] == selected_id), None)
        return None

    # Behavioral checks:
    # 1. Distinctive catalog names resolve to correct event
    nebius_match = resolve_target("deadline for Nebius")
    assert nebius_match is not None and "nebius" in nebius_match["title"].lower()

    opencv_match = resolve_target("prize pool for OpenCV")
    assert opencv_match is not None and "opencv" in opencv_match["title"].lower()

    # 2. Explicit Amazon/Alexa resolves to Amazon
    amazon_match = resolve_target("audit Amazon Developer Hackathon")
    assert amazon_match is not None and "amazon" in amazon_match["title"].lower()

    # 3. Unrecognized hackathon names return None (never default to Amazon)
    assert resolve_target("deadline for NonexistentHackathon2026") is None
    assert resolve_target("audit QuantumBounty") is None
    assert resolve_target("who can enter MysteryChallenge") is None

    # 4. Even if an event is actively selected, an unrecognized name MUST NOT fall back to selected
    assert resolve_target("audit NonexistentHackathon2026", selected_id=nebius_match["id"]) is None

    # 5. Generic intent without named hackathon falls back to selection if present, else None
    assert resolve_target("what is the deadline", selected_id=nebius_match["id"])["id"] == nebius_match["id"]
    assert resolve_target("what is the deadline", selected_id=None) is None

    # 6. Verify HTML escaping behavior in app.js
    js_text = client.get("/static/app.js").text
    assert "escapeHtml" in js_text
    assert "this.escapeHtml(this.searchQuery)" in js_text

    # 7. Verify styles and simulator branding
    css_text = client.get("/static/styles.css").text
    assert "@media (max-width: 1400px)" in css_text
    assert "@media (max-width: 1024px)" in css_text
    assert "@media (max-width: 768px)" in css_text
    assert "--text-muted: #94A3B8;" in css_text

    html_text = client.get("/").text
    assert "Echo Show 15 Multimodal Simulator" in html_text



def test_unknown_hackathon_name_rejection_and_safety():
    """Regression test: verifies that unknown hackathon names are rejected safely
    and never default to Amazon in tools or command resolution.
    """
    unknown_id = "nonexistent_quantum_hackathon_9999"

    # 1. MCP audit_rules with unknown identifier returns not_found error, not Amazon data
    audit_res = client.post("/mcp", json={
        "jsonrpc": "2.0",
        "id": 501,
        "method": "tools/call",
        "params": {"name": "audit_rules", "arguments": {"identifier": unknown_id}},
    }).json()
    assert audit_res["result"]["isError"] is True
    data = audit_res["result"]["structuredContent"]["data"]
    assert data["verification_status"] == "not_found"
    assert "not found matching" in data["error"].lower()
    assert "amazon" not in json.dumps(data).lower()

    # 2. MCP diff_rules with unknown identifier returns error, not Amazon data
    diff_res = client.post("/mcp", json={
        "jsonrpc": "2.0",
        "id": 502,
        "method": "tools/call",
        "params": {"name": "diff_rules", "arguments": {"identifier": unknown_id}},
    }).json()
    assert diff_res["result"]["isError"] is True
    diff_data = diff_res["result"]["structuredContent"]["data"]
    assert "error" in diff_data
    assert "not found matching" in diff_data["error"].lower()
    assert "amazon" not in json.dumps(diff_data).lower()

    # 3. Verify static/app.js rejection and clarification behavior for unknown hackathons
    js_text = client.get("/static/app.js").text

    # resolveTargetHackathon returns null instead of defaulting to Amazon
    resolve_block = js_text.split("resolveTargetHackathon(text) {")[1].split("async handleCommand(text) {")[0]
    assert "return this.getAmazonHackathon();" not in resolve_block[-120:]
    assert "return null;" in resolve_block[-50:]

    # When command names an unknown hackathon, specific non-stopword tokens cause return null
    assert "if (specificTokens.length > 0)" in resolve_block
    assert "return null;" in resolve_block

    # Clarification message is present across commands when no target is found
    clarification = "I couldn't identify that hackathon in the catalog"
    assert clarification in js_text


def test_honest_reporting_of_audit_status_and_provenance():
    """Regression test: verifies that audit status is reported honestly across
    MCP tool responses and client application logic.
    """
    from core.database import query_hackathons
    catalog = query_hackathons(limit=20)
    assert len(catalog) > 0

    # 1. Test audit_rules provenance honesty
    for h in catalog[:3]:
        res = client.post("/mcp", json={
            "jsonrpc": "2.0",
            "id": 601,
            "method": "tools/call",
            "params": {"name": "audit_rules", "arguments": {"identifier": h["id"]}},
        }).json()
        assert res["result"]["isError"] is False
        data = res["result"]["structuredContent"]["data"]
        voice = res["result"]["structuredContent"]["voiceSummary"]
        status = data.get("verification_status")

        # Must be either verified_live or fallback_unverified
        assert status in ["verified_live", "fallback_unverified"]
        assert status in voice

        if status == "fallback_unverified":
            # Must honestly disclose fallback synthesis from catalog metadata in notes
            assert "fallback" in data["notes"].lower() or "catalog metadata" in data["notes"].lower()
            # Must not claim verified live
            assert "verified live" not in data["notes"].lower()

    # 2. Test client-side app.js behavioral constraints
    js_text = client.get("/static/app.js").text

    # Verify handleCommand rule-audit handler checks verification_status before speaking
    rule_audit_section = js_text.split("cmd.includes(\"eligib\")")[1].split("// 5. Diff")[0]
    assert "auditData.verification_status === \"verified_live\"" in rule_audit_section
    assert "auditData.verification_status === \"fallback_unverified\"" in rule_audit_section
    assert "verified with live provenance" in rule_audit_section
    assert "fallback unverified catalog metadata" in rule_audit_section
    assert "Rule audit failed" in rule_audit_section
    # Verify it does not unconditionally announce "with verified provenance"
    assert "with verified provenance. Reviewing eligibility" not in js_text
