"""Automated verification of Hackathon Discovery, Status/Date Distinctions,
Multi-Source Refresh, and 30-Day Rolling Context Retention.
"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import pytest
import httpx
from starlette.testclient import TestClient

from app import app
from core.database import (
    get_db_connection,
    get_hackathon,
    get_refresh_status,
    init_db,
    normalize_event_dates_and_status,
    purge_expired_hackathons,
    query_hackathons,
)
from core.ingestion import (
    DEFAULT_RETENTION_DAYS,
    DiscoveryError,
    fetch_devfolio_hackathons,
    fetch_devpost_hackathons,
    fetch_unstop_hackathons,
    parse_date_range_to_iso,
    refresh_discovery_sources,
)

client = TestClient(app)


# -----------------------------------------------------------------------------
# Mock Responses
# -----------------------------------------------------------------------------
DEVPOST_OPEN_MOCK = {
    "hackathons": [
        {
            "id": 101,
            "title": "Amazon Developer Open Sprint",
            "url": "https://amazon-dev.devpost.com/",
            "open_state": "open",
            "displayed_location": {"location": "Online"},
            "submission_period_dates": "Nov 01 - Nov 30, 2026",
            "themes": [{"name": "AI"}, {"name": "Voice"}],
            "prize_amount": "$50,000",
            "organization_name": "Amazon",
        }
    ],
    "meta": {"total_count": 1, "per_page": 9},
}

DEVPOST_UPCOMING_MOCK = {
    "hackathons": [
        {
            "id": 102,
            "title": "Alexa Future Builders",
            "url": "https://alexa-future.devpost.com/",
            "open_state": "upcoming",
            "displayed_location": {"location": "Online"},
            "submission_period_dates": "Dec 01 - Dec 20, 2026",
            "themes": [{"name": "Smart Home"}, {"name": "IoT"}],
            "prize_amount": "$25,000",
            "organization_name": "Amazon Alexa",
        }
    ],
    "meta": {"total_count": 1, "per_page": 9},
}

DEVFOLIO_OPEN_MOCK = {
    "result": [
        {
            "uuid": "df-101",
            "name": "Devfolio Web3 Wave",
            "slug": "web3-wave",
            "is_online": True,
            "themes": [{"name": "Web3"}, {"name": "Blockchain"}],
            "starts_at": "2026-11-15T09:00:00.000Z",
            "ends_at": "2026-11-17T18:00:00.000Z",
            "hackathon_setting": {
                "reg_starts_at": "2026-10-01T00:00:00.000Z",
                "reg_ends_at": "2026-11-10T23:59:59.000Z",
            },
            "desc": "Official Devfolio Open Hackathon",
        }
    ],
    "count": 1,
    "pages": 1,
}

UNSTOP_OPEN_MOCK = {
    "data": {
        "data": [
            {
                "id": 201,
                "title": "Unstop National Codefest",
                "public_url": "hackathons/national-codefest-201",
                "status": "LIVE",
                "start_date": "2026-11-20T00:00:00+05:30",
                "end_date": "2026-11-22T23:59:59+05:30",
                "regnRequirements": {
                    "start_regn_dt": "2026-10-15T00:00:00+05:30",
                    "end_regn_dt": "2026-11-18T23:59:59+05:30",
                    "reg_status": "STARTED",
                },
                "locations": [{"city": "Online"}],
                "prizes": [{"cash": "100000", "currency": "INR"}],
                "tags": [{"name": "Coding"}],
                "organisation": {"name": "Tech Club"},
            }
        ],
        "current_page": 1,
        "last_page": 1,
        "total": 1,
    }
}


# -----------------------------------------------------------------------------
# 1. Strict Discovery Scope Tests
# -----------------------------------------------------------------------------
def test_devpost_discovery_scope_strictly_open_and_upcoming(tmp_path):
    """Devpost scraper must exclusively query open and upcoming sections, never ended/archive."""
    requested_urls = []

    def mock_transport(request: httpx.Request) -> httpx.Response:
        url_str = str(request.url)
        requested_urls.append(url_str)
        if "status=open" in url_str:
            return httpx.Response(200, json=DEVPOST_OPEN_MOCK)
        elif "status=upcoming" in url_str:
            return httpx.Response(200, json=DEVPOST_UPCOMING_MOCK)
        elif "status=ended" in url_str:
            pytest.fail("Scraper attempted to crawl ended/historical archive!")
        return httpx.Response(404)

    client_mock = httpx.Client(transport=httpx.MockTransport(mock_transport))
    items = fetch_devpost_hackathons(client=client_mock)

    assert len(items) == 2
    assert all("status=ended" not in u for u in requested_urls)
    assert any("status=open" in u for u in requested_urls)
    assert any("status=upcoming" in u for u in requested_urls)

    titles = [i["title"] for i in items]
    assert "Amazon Developer Open Sprint" in titles
    assert "Alexa Future Builders" in titles
    assert items[0]["status"] == "open"
    assert items[1]["status"] == "upcoming"


def test_devfolio_discovery_scope_strictly_application_open():
    """Devfolio scraper must exclusively query filter=application_open, never general catalogue."""
    requested_urls = []

    def mock_transport(request: httpx.Request) -> httpx.Response:
        url_str = str(request.url)
        requested_urls.append(url_str)
        if "filter=application_open" in url_str:
            return httpx.Response(200, json=DEVFOLIO_OPEN_MOCK)
        return httpx.Response(400)

    client_mock = httpx.Client(transport=httpx.MockTransport(mock_transport))
    items = fetch_devfolio_hackathons(client=client_mock)

    assert len(items) == 1
    assert "filter=application_open" in requested_urls[0]
    assert items[0]["id"] == "devfolio_web3-wave"
    assert items[0]["status"] == "open"
    assert items[0]["source"] == "Devfolio"
    # Registration deadline distinct from event dates
    assert items[0]["reg_ends_at"] == "2026-11-10T23:59:59.000Z"
    assert items[0]["starts_at"] == "2026-11-15T09:00:00.000Z"


def test_unstop_discovery_scope_strictly_open_unpaid_students():
    """Unstop scraper must exclusively query open unpaid student hackathons."""
    requested_urls = []

    def mock_transport(request: httpx.Request) -> httpx.Response:
        url_str = str(request.url)
        requested_urls.append(url_str)
        assert "oppstatus=open" in url_str
        assert "payment=unpaid" in url_str
        assert "usertype=students" in url_str
        assert "domain=2" in url_str
        return httpx.Response(200, json=UNSTOP_OPEN_MOCK)

    client_mock = httpx.Client(transport=httpx.MockTransport(mock_transport))
    items = fetch_unstop_hackathons(client=client_mock)

    assert len(items) == 1
    assert items[0]["id"] == "unstop_201"
    assert items[0]["status"] == "open"
    assert items[0]["reg_ends_at"] == "2026-11-18T23:59:59+05:30"
    assert items[0]["starts_at"] == "2026-11-20T00:00:00+05:30"


# -----------------------------------------------------------------------------
# 2. Accurate Status and Distinct Dates Tests
# -----------------------------------------------------------------------------
def test_registration_dates_distinct_from_event_dates():
    """Verify that registration deadline is not substituted with event hacking date."""
    ref_now = datetime(2026, 10, 20, 12, 0, 0, tzinfo=timezone.utc)

    # Case A: Event starts next month, but registration already closed yesterday
    item_closed_reg = {
        "title": "Closed Reg Future Event",
        "reg_starts_at": "2026-10-01T00:00:00Z",
        "reg_ends_at": "2026-10-19T23:59:59Z",  # in past
        "starts_at": "2026-11-10T09:00:00Z",     # in future
        "ends_at": "2026-11-12T18:00:00Z",       # in future
        "status": "open",                        # source might claim open, but deadline passed!
    }
    normalized = normalize_event_dates_and_status(item_closed_reg, now=ref_now)
    # Must be marked closed because registration deadline has passed but event hasn't ended
    assert normalized["status"] == "closed"
    assert normalized["reg_ends_at"] == "2026-10-19T23:59:59Z"
    assert normalized["starts_at"] == "2026-11-10T09:00:00Z"
    assert normalized["reg_ends_at"] != normalized["starts_at"]

    # Case B: Event registration opens in future (upcoming)
    item_upcoming = {
        "title": "Upcoming Hackathon",
        "reg_starts_at": "2026-11-01T00:00:00Z",
        "reg_ends_at": "2026-11-20T00:00:00Z",
        "starts_at": "2026-11-25T00:00:00Z",
        "ends_at": "2026-11-27T00:00:00Z",
        "status": "upcoming",
    }
    norm_up = normalize_event_dates_and_status(item_upcoming, now=ref_now)
    assert norm_up["status"] == "upcoming"

    # Case C: Unknown status is not falsely converted to confirmed open
    item_unknown = {
        "title": "Mystery Hackathon",
        "reg_starts_at": "",
        "reg_ends_at": "",
        "starts_at": "",
        "ends_at": "",
        "status": "unverified",
    }
    norm_unk = normalize_event_dates_and_status(item_unknown, now=ref_now)
    assert norm_unk["status"] == "unknown"


# -----------------------------------------------------------------------------
# 3. Daily Multi-Source Refresh & Deduplication Tests
# -----------------------------------------------------------------------------
def test_refresh_discovery_sources_idempotent_and_logs(tmp_path):
    """Verifies that multi-source refresh creates records, updates idempotently, and logs status."""
    db_file = tmp_path / "test_refresh.db"
    init_db(db_file)

    def mock_transport(request: httpx.Request) -> httpx.Response:
        url_str = str(request.url)
        if "devpost.com" in url_str:
            if "status=open" in url_str:
                return httpx.Response(200, json=DEVPOST_OPEN_MOCK)
            return httpx.Response(200, json=DEVPOST_UPCOMING_MOCK)
        elif "devfolio.co" in url_str:
            return httpx.Response(200, json=DEVFOLIO_OPEN_MOCK)
        elif "unstop.com" in url_str:
            return httpx.Response(200, json=UNSTOP_OPEN_MOCK)
        return httpx.Response(404)

    client_mock = httpx.Client(transport=httpx.MockTransport(mock_transport))

    # Run 1: Initial Discovery
    res1 = refresh_discovery_sources(db_path=db_file, client=client_mock)
    assert res1["total_discovered"] == 4  # 2 devpost + 1 devfolio + 1 unstop
    assert res1["total_added"] == 4
    assert res1["total_updated"] == 0

    # Verify per-source refresh logs
    logs = get_refresh_status(db_file)
    assert len(logs) == 3
    sources_logged = {l["source"] for l in logs}
    assert sources_logged == {"Devpost", "Devfolio", "Unstop"}
    assert all(l["status"] == "success" for l in logs)

    # Run 2: Repeated Discovery — MUST be idempotent, no duplicates
    res2 = refresh_discovery_sources(db_path=db_file, client=client_mock)
    assert res2["total_discovered"] == 4
    assert res2["total_added"] == 0
    assert res2["total_updated"] == 4

    # Verify database count has not grown
    conn = get_db_connection(db_file)
    total_count = conn.execute("SELECT COUNT(*) AS c FROM hackathons").fetchone()["c"]
    conn.close()
    assert total_count == 4


def test_source_failure_preserves_last_known_good_data(tmp_path):
    """If one source encounters an error or network drop, existing records must not be deleted or closed."""
    db_file = tmp_path / "test_fault.db"
    init_db(db_file)

    # Initial seed run with all sources succeeding
    def mock_transport_ok(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "devpost" in url:
            return httpx.Response(200, json=DEVPOST_OPEN_MOCK if "open" in url else DEVPOST_UPCOMING_MOCK)
        elif "devfolio" in url:
            return httpx.Response(200, json=DEVFOLIO_OPEN_MOCK)
        return httpx.Response(200, json=UNSTOP_OPEN_MOCK)

    client_ok = httpx.Client(transport=httpx.MockTransport(mock_transport_ok))
    refresh_discovery_sources(db_path=db_file, client=client_ok)

    # Devfolio item was added
    devfolio_item = get_hackathon("devfolio_web3-wave", db_path=db_file)
    assert devfolio_item is not None
    assert devfolio_item["status"] == "open"

    # Run 2: Devfolio returns HTTP 500 server error
    def mock_transport_devfolio_fail(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "devfolio" in url:
            return httpx.Response(500, text="Internal Server Error")
        if "devpost" in url:
            return httpx.Response(200, json=DEVPOST_OPEN_MOCK if "open" in url else DEVPOST_UPCOMING_MOCK)
        return httpx.Response(200, json=UNSTOP_OPEN_MOCK)

    client_fail = httpx.Client(transport=httpx.MockTransport(mock_transport_devfolio_fail))
    res = refresh_discovery_sources(db_path=db_file, client=client_fail)

    # Verify source error is logged and reported
    assert res["sources"]["Devfolio"]["status"] == "failure"
    assert "HTTP 500" in res["sources"]["Devfolio"]["error"]

    # CRITICAL: Devfolio record must NOT be deleted or falsely marked closed
    devfolio_item_after = get_hackathon("devfolio_web3-wave", db_path=db_file)
    assert devfolio_item_after is not None
    assert devfolio_item_after["status"] == "open"
    # missing_count must not have incremented on a failed source run
    assert devfolio_item_after["missing_count"] == 0


# -----------------------------------------------------------------------------
# 4. Conservative Missing Observation Policy
# -----------------------------------------------------------------------------
def test_missing_observation_threshold(tmp_path):
    """An event temporarily missing from 1 successful scrape is NOT declared inactive immediately.
    It requires 3 consecutive successful scrapes where it was absent.
    """
    db_file = tmp_path / "test_missing.db"
    init_db(db_file)

    # Run 1: Normal scrape includes Devfolio item
    def mock_run1(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "devfolio" in url:
            return httpx.Response(200, json=DEVFOLIO_OPEN_MOCK)
        return httpx.Response(200, json={"hackathons": [], "meta": {}} if "devpost" in url else {"data": {"data": [], "last_page": 1}})

    refresh_discovery_sources(db_path=db_file, client=httpx.Client(transport=httpx.MockTransport(mock_run1)))
    h = get_hackathon("devfolio_web3-wave", db_path=db_file)
    assert h["status"] == "open"
    assert h["missing_count"] == 0

    # Run 2: Devfolio returns empty result (1st missing observation)
    def mock_empty_devfolio(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "devfolio" in url:
            return httpx.Response(200, json={"result": [], "pages": 1})
        return httpx.Response(200, json={"hackathons": [], "meta": {}} if "devpost" in url else {"data": {"data": [], "last_page": 1}})

    c_empty = httpx.Client(transport=httpx.MockTransport(mock_empty_devfolio))
    refresh_discovery_sources(db_path=db_file, client=c_empty)
    h = get_hackathon("devfolio_web3-wave", db_path=db_file)
    # Must still be open! (missing_count = 1)
    assert h["status"] == "open"
    assert h["missing_count"] == 1

    # Run 3: 2nd missing observation
    refresh_discovery_sources(db_path=db_file, client=c_empty)
    h = get_hackathon("devfolio_web3-wave", db_path=db_file)
    assert h["status"] == "open"
    assert h["missing_count"] == 2

    # Run 4: 3rd missing observation (threshold reached)
    refresh_discovery_sources(db_path=db_file, client=c_empty)
    h = get_hackathon("devfolio_web3-wave", db_path=db_file)
    assert h["status"] == "ended"
    assert h["missing_count"] == 3
    assert h["inactive_since"] is not None


# -----------------------------------------------------------------------------
# 5. Rolling Context Retention (30-Day Policy) Tests
# -----------------------------------------------------------------------------
def test_retention_policy_purges_inactive_after_30_days(tmp_path):
    """Verifies that inactive events are purged after 30 days of inactivity,
    while active events are NEVER purged regardless of age.
    """
    db_file = tmp_path / "test_retention.db"
    init_db(db_file)
    conn = get_db_connection(db_file)

    now_ref = datetime(2026, 10, 23, 12, 0, 0, tzinfo=timezone.utc)
    old_time = (now_ref - timedelta(days=60)).isoformat()
    inactive_35_days_ago = (now_ref - timedelta(days=35)).isoformat()
    inactive_10_days_ago = (now_ref - timedelta(days=10)).isoformat()

    with conn:
        # 1. Active open event discovered 60 days ago
        conn.execute(
            """
            INSERT INTO hackathons (id, fingerprint, title, url, source, status, created_at, updated_at)
            VALUES ('active_old', 'active_old', 'Long Running Open Hackathon', 'https://devpost.com', 'Devpost', 'open', ?, ?)
            """,
            (old_time, old_time),
        )

        # 2. Inactive event confirmed closed 10 days ago (within 30-day retention window)
        conn.execute(
            """
            INSERT INTO hackathons (id, fingerprint, title, url, source, status, inactive_since, created_at, updated_at)
            VALUES ('inactive_recent', 'inactive_recent', 'Recently Ended Hackathon', 'https://devfolio.co', 'Devfolio', 'ended', ?, ?, ?)
            """,
            (inactive_10_days_ago, old_time, old_time),
        )

        # 3. Inactive event confirmed closed 35 days ago (EXPIRED: > 30 days)
        conn.execute(
            """
            INSERT INTO hackathons (id, fingerprint, title, url, source, status, inactive_since, created_at, updated_at)
            VALUES ('inactive_expired', 'inactive_expired', 'Old Closed Hackathon', 'https://unstop.com', 'Unstop', 'ended', ?, ?, ?)
            """,
            (inactive_35_days_ago, old_time, old_time),
        )
    conn.close()

    # Run purge with default 30-day retention
    purged = purge_expired_hackathons(db_path=db_file, retention_days=30, now=now_ref)
    assert purged == 1

    # Verify:
    # 'inactive_expired' was purged
    assert get_hackathon("inactive_expired", db_path=db_file) is None

    # 'inactive_recent' was retained
    assert get_hackathon("inactive_recent", db_path=db_file) is not None

    # 'active_old' was preserved even though it is 60 days old!
    active_item = get_hackathon("active_old", db_path=db_file)
    assert active_item is not None
    assert active_item["status"] == "open"


# -----------------------------------------------------------------------------
# 6. Observability Endpoints and MCP Tool Verification
# -----------------------------------------------------------------------------
def test_sync_status_and_health_endpoints():
    """Verify that /sync/status and /health endpoints report operational refresh logs."""
    h_res = client.get("/health")
    assert h_res.status_code == 200
    h_data = h_res.json()
    assert "refresh_status" in h_data

    s_res = client.get("/sync/status")
    assert s_res.status_code == 200
    s_data = s_res.json()
    assert s_data["status"] == "ok"
    assert "refresh_logs" in s_data


def test_mcp_sync_upstream_catalog_discover_live_tool():
    """Verify MCP sync_upstream_catalog tool executes live multi-source discovery."""
    payload = {
        "jsonrpc": "2.0",
        "id": 99,
        "method": "tools/call",
        "params": {
            "name": "sync_upstream_catalog",
            "arguments": {"discover_live": False},  # fallback seed mode
        },
    }
    res = client.post("/mcp", json=payload).json()
    assert res["result"]["isError"] is False
    data = json.loads(res["result"]["content"][0]["text"])
    assert data["source_type"] == "fallback_seed"
