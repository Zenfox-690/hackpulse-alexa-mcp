"""Automated End-to-End Verification of HackPulse Alexa MCP Server.
Verifies MCP 2025-11-25 handshake, Mcp-Session-Id generation, all 5 core tools,
rule-audit provenance, resources, prompts, and health monitoring.
"""

import json
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
    assert len(tool_names) == 5
    assert "search_hackathons" in tool_names
    assert "get_hackathon_details" in tool_names
    assert "sync_upstream_catalog" in tool_names
    assert "audit_rules" in tool_names
    assert "diff_rules" in tool_names


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
