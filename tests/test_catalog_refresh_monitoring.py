"""Comprehensive Verification for Automated Live Catalog Refresh & Freshness Monitoring.

Covers all 12 operational scenarios:
1. All sources successfully refresh.
2. One source fails while others succeed (partial success).
3. All sources fail (complete failure preserves last known good state).
4. A source returns a successful empty catalog (distinguishable from failure).
5. Previously discovered records survive temporary source failures.
6. Duplicate opportunities remain deduplicated across sources.
7. Freshness timestamps update only after successful refreshes.
8. Staleness detection against configurable threshold (default 24h).
9. Invalid refresh tokens are rejected (fail closed, constant-time compare).
10. Concurrent refresh protection works (concurrency locks & 409 Conflict).
11. An old fallback snapshot cannot overwrite newer verified data.
12. Registration and event lifecycle statuses remain accurate (open, closed, ended, upcoming, unknown).
"""

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
import pytest
import httpx
from starlette.testclient import TestClient

from app import app
from core.database import (
    get_catalog_freshness,
    get_db_connection,
    get_hackathon,
    get_refresh_status,
    init_db,
    normalize_event_dates_and_status,
    query_hackathons,
    record_refresh_log,
    sync_upstream_json,
)
from core.ingestion import (
    _REFRESH_LOCK,
    ConcurrencyError,
    refresh_discovery_sources,
    sanitize_error_message,
)

client = TestClient(app)

# -----------------------------------------------------------------------------
# Synthetic Mock Payloads
# -----------------------------------------------------------------------------
MOCK_DEVPOST_OPEN_DATA = {
    "hackathons": [
        {
            "id": 901,
            "title": "Devpost AI Grand Prix",
            "url": "https://devpost-ai-grand-prix.devpost.com",
            "open_state": "open",
            "displayed_location": {"location": "Online"},
            "submission_period_dates": "Oct 01 - Nov 30, 2026",
            "themes": [{"name": "AI"}, {"name": "Agents"}],
            "prize_amount": "$100,000",
            "organization_name": "Devpost Community",
        }
    ],
    "meta": {"total_count": 1, "per_page": 9},
}

MOCK_DEVPOST_UPCOMING_DATA = {
    "hackathons": [
        {
            "id": 902,
            "title": "Devpost Future Builders",
            "url": "https://devpost-future-builders.devpost.com",
            "open_state": "upcoming",
            "displayed_location": {"location": "Online"},
            "submission_period_dates": "Dec 01 - Dec 20, 2026",
            "themes": [{"name": "IoT"}],
            "prize_amount": "$50,000",
            "organization_name": "Devpost Future",
        }
    ],
    "meta": {"total_count": 1, "per_page": 9},
}

MOCK_DEVFOLIO_DATA = {
    "result": [
        {
            "uuid": "df-903",
            "name": "Devfolio Web3 Wave",
            "slug": "web3-wave",
            "is_online": True,
            "themes": [{"name": "Web3"}, {"name": "Blockchain"}],
            "starts_at": "2026-11-20T09:00:00.000Z",
            "ends_at": "2026-11-25T18:00:00.000Z",
            "hackathon_setting": {
                "reg_starts_at": "2026-10-01T00:00:00.000Z",
                "reg_ends_at": "2026-11-18T23:59:59.000Z",
            },
            "desc": "Official Devfolio Wave Hackathon",
        }
    ],
    "count": 1,
    "pages": 1,
}

MOCK_UNSTOP_DATA = {
    "data": {
        "data": [
            {
                "id": 904,
                "title": "Unstop National Codefest",
                "public_url": "hackathons/national-codefest-904",
                "status": "LIVE",
                "start_date": "2026-11-20T00:00:00+05:30",
                "end_date": "2026-11-22T23:59:59+05:30",
                "regnRequirements": {
                    "start_regn_dt": "2026-10-15T00:00:00+05:30",
                    "end_regn_dt": "2026-11-20T23:59:59+05:30",
                    "reg_status": "STARTED",
                },
                "locations": [{"city": "Online"}],
                "prizes": [{"cash": "50000", "currency": "INR"}],
                "tags": [{"name": "Competitive Programming"}],
                "organisation": {"name": "Unstop Tech"},
            }
        ],
        "current_page": 1,
        "last_page": 1,
        "total": 1,
    }
}


def create_mock_transport(devpost_resp=None, devfolio_resp=None, unstop_resp=None):
    def transport(request: httpx.Request) -> httpx.Response:
        url_str = str(request.url)
        if "devpost.com" in url_str:
            if isinstance(devpost_resp, Exception):
                raise devpost_resp
            if devpost_resp:
                return devpost_resp
            if "status=open" in url_str:
                return httpx.Response(200, json=MOCK_DEVPOST_OPEN_DATA)
            return httpx.Response(200, json=MOCK_DEVPOST_UPCOMING_DATA)
        elif "devfolio.co" in url_str:
            if isinstance(devfolio_resp, Exception):
                raise devfolio_resp
            return devfolio_resp or httpx.Response(200, json=MOCK_DEVFOLIO_DATA)
        elif "unstop.com" in url_str:
            if isinstance(unstop_resp, Exception):
                raise unstop_resp
            return unstop_resp or httpx.Response(200, json=MOCK_UNSTOP_DATA)
        return httpx.Response(404)

    return httpx.MockTransport(transport)


# -----------------------------------------------------------------------------
# Scenario 1: All Sources Successfully Refresh
# -----------------------------------------------------------------------------
def test_all_sources_successfully_refresh(tmp_path):
    db_file = tmp_path / "test_success.db"
    init_db(db_file)
    now_ref = datetime(2026, 10, 20, 12, 0, 0, tzinfo=timezone.utc)

    client_mock = httpx.Client(transport=create_mock_transport())
    res = refresh_discovery_sources(db_path=db_file, client=client_mock, now=now_ref)

    assert res["overall_status"] == "success"
    assert res["total_discovered"] == 4  # 2 devpost (open + upcoming) + 1 devfolio + 1 unstop
    assert res["total_added"] == 4
    assert res["total_updated"] == 0

    freshness = get_catalog_freshness(db_path=db_file, threshold_hours=24, now=now_ref)
    assert freshness["is_fresh"] is True
    assert freshness["overall_status"] == "fresh"
    assert len(freshness["fresh_sources"]) == 3
    assert len(freshness["stale_sources"]) == 0
    assert freshness["sources"]["Devpost"]["status"] == "success"
    assert freshness["sources"]["Devfolio"]["status"] == "success"
    assert freshness["sources"]["Unstop"]["status"] == "success"


# -----------------------------------------------------------------------------
# Scenario 2: One Source Fails While Others Succeed (Partial Success)
# -----------------------------------------------------------------------------
def test_one_source_fails_while_others_succeed(tmp_path):
    db_file = tmp_path / "test_partial.db"
    init_db(db_file)
    now_ref = datetime(2026, 10, 20, 12, 0, 0, tzinfo=timezone.utc)

    # Devfolio returns HTTP 500 error
    devfolio_err = httpx.Response(500, text="Internal Server Error at Devfolio")
    client_mock = httpx.Client(
        transport=create_mock_transport(devfolio_resp=devfolio_err)
    )

    res = refresh_discovery_sources(db_path=db_file, client=client_mock, now=now_ref)
    assert res["overall_status"] == "partial"
    assert res["sources"]["Devpost"]["status"] == "success"
    assert res["sources"]["Unstop"]["status"] == "success"
    assert res["sources"]["Devfolio"]["status"] == "failure"
    assert "500" in res["sources"]["Devfolio"]["error"]

    freshness = get_catalog_freshness(db_path=db_file, threshold_hours=24, now=now_ref)
    assert freshness["sources"]["Devpost"]["is_fresh"] is True
    assert freshness["sources"]["Unstop"]["is_fresh"] is True
    # Devfolio failed on its initial run -> not fresh
    assert freshness["sources"]["Devfolio"]["is_fresh"] is False
    assert freshness["sources"]["Devfolio"]["last_successful_at"] is None


# -----------------------------------------------------------------------------
# Scenario 3: All Sources Fail
# -----------------------------------------------------------------------------
def test_all_sources_fail(tmp_path):
    db_file = tmp_path / "test_all_fail.db"
    init_db(db_file)
    now_ref = datetime(2026, 10, 20, 12, 0, 0, tzinfo=timezone.utc)

    # Seed an initial record
    conn = get_db_connection(db_file)
    with conn:
        conn.execute(
            """
            INSERT INTO hackathons (id, fingerprint, title, url, source, status, created_at, updated_at, last_verified_at)
            VALUES ('seed_item', 'seed_item', 'Existing Hackathon', 'https://devpost.com', 'Devpost', 'open', '2026-10-15', '2026-10-15', '2026-10-15')
            """
        )
    conn.close()

    # All sources return 503 Service Unavailable
    err_503 = httpx.Response(503, text="Service Unavailable")
    client_mock = httpx.Client(
        transport=create_mock_transport(
            devpost_resp=err_503, devfolio_resp=err_503, unstop_resp=err_503
        )
    )

    res = refresh_discovery_sources(db_path=db_file, client=client_mock, now=now_ref)
    assert res["overall_status"] == "failure"
    assert all(r["status"] == "failure" for r in res["sources"].values())

    # Critical: Existing record must NOT be lost or marked closed
    existing = get_hackathon("seed_item", db_path=db_file, now=now_ref)
    assert existing is not None
    assert existing["status"] == "open"


# -----------------------------------------------------------------------------
# Scenario 4: A Source Returns a Successful Empty Catalog
# -----------------------------------------------------------------------------
def test_source_returns_successful_empty_catalog(tmp_path):
    db_file = tmp_path / "test_empty.db"
    init_db(db_file)
    now_ref = datetime(2026, 10, 20, 12, 0, 0, tzinfo=timezone.utc)

    # Devfolio returns empty result with HTTP 200
    empty_devfolio = httpx.Response(200, json={"result": [], "pages": 1, "count": 0})
    client_mock = httpx.Client(
        transport=create_mock_transport(devfolio_resp=empty_devfolio)
    )

    res = refresh_discovery_sources(db_path=db_file, client=client_mock, now=now_ref)
    assert res["sources"]["Devfolio"]["status"] == "success"
    assert res["sources"]["Devfolio"]["discovered"] == 0
    assert res["sources"]["Devfolio"]["error"] is None

    # Distinguishable from failure: last_successful_at is updated!
    freshness = get_catalog_freshness(db_path=db_file, threshold_hours=24, now=now_ref)
    assert freshness["sources"]["Devfolio"]["last_successful_at"] == now_ref.isoformat()
    assert freshness["sources"]["Devfolio"]["is_fresh"] is True


# -----------------------------------------------------------------------------
# Scenario 5: Previously Discovered Records Survive Temporary Source Failures
# -----------------------------------------------------------------------------
def test_previously_discovered_records_survive_temporary_source_failures(tmp_path):
    db_file = tmp_path / "test_survival.db"
    init_db(db_file)
    now_ref1 = datetime(2026, 10, 20, 12, 0, 0, tzinfo=timezone.utc)

    # Run 1: All sources succeed
    c1 = httpx.Client(transport=create_mock_transport())
    refresh_discovery_sources(db_path=db_file, client=c1, now=now_ref1)

    h_before = get_hackathon("devpost_901", db_path=db_file, now=now_ref1)
    assert h_before is not None
    assert h_before["status"] == "open"

    # Run 2: Devpost network drops
    now_ref2 = now_ref1 + timedelta(hours=6)
    devpost_fail = httpx.Response(500, text="Devpost API Crash")
    c2 = httpx.Client(transport=create_mock_transport(devpost_resp=devpost_fail))
    res2 = refresh_discovery_sources(db_path=db_file, client=c2, now=now_ref2)

    assert res2["sources"]["Devpost"]["status"] == "failure"
    # Record must survive unchanged
    h_after = get_hackathon("devpost_901", db_path=db_file, now=now_ref2)
    assert h_after is not None
    assert h_after["status"] == "open"
    assert h_after["missing_count"] == 0


# -----------------------------------------------------------------------------
# Scenario 6: Duplicate Opportunities Remain Deduplicated
# -----------------------------------------------------------------------------
def test_duplicate_opportunities_remain_deduplicated(tmp_path):
    db_file = tmp_path / "test_dedup.db"
    init_db(db_file)
    now_ref = datetime(2026, 10, 20, 12, 0, 0, tzinfo=timezone.utc)

    # Insert two records representing the same event from different sources
    conn = get_db_connection(db_file)
    with conn:
        conn.execute(
            """
            INSERT INTO hackathons (id, fingerprint, title, url, source, deadline, location, status, created_at, updated_at)
            VALUES ('devpost_101', 'fp101', 'Global AI Hackathon 2026', 'https://global-ai-2026.devpost.com/', 'Devpost', '2026-11-30', 'Online', 'open', '2026-10-15', '2026-10-15')
            """
        )
        conn.execute(
            """
            INSERT INTO hackathons (id, fingerprint, title, url, source, deadline, location, status, created_at, updated_at)
            VALUES ('devfolio_ai2026', 'fp102', 'Global AI Hackathon 2026', 'https://global-ai-2026.devpost.com', 'Devfolio', '2026-11-30', 'Online', 'open', '2026-10-15', '2026-10-15')
            """
        )
    conn.close()

    items = query_hackathons(db_path=db_file, now=now_ref)
    titles = [i["title"] for i in items]
    assert titles.count("Global AI Hackathon 2026") == 1


# -----------------------------------------------------------------------------
# Scenario 7: Freshness Timestamps Update Only After Successful Refreshes
# -----------------------------------------------------------------------------
def test_freshness_timestamps_update_only_after_successful_refreshes(tmp_path):
    db_file = tmp_path / "test_ts.db"
    init_db(db_file)
    t1 = datetime(2026, 10, 20, 12, 0, 0, tzinfo=timezone.utc)

    # Initial success at t1
    record_refresh_log(
        source="Devpost",
        status="success",
        discovered_count=10,
        added_count=2,
        updated_count=1,
        attempted_at=t1,
        successful_at=t1,
        db_path=db_file,
    )
    logs1 = {l["source"]: l for l in get_refresh_status(db_file)}
    assert logs1["Devpost"]["last_successful_at"] == t1.isoformat()
    assert logs1["Devpost"]["last_attempted_at"] == t1.isoformat()

    # Attempt at t2 that FAILS: last_successful_at must NOT change!
    t2 = t1 + timedelta(hours=5)
    record_refresh_log(
        source="Devpost",
        status="failure",
        error_message="Network Timeout",
        attempted_at=t2,
        successful_at=None,
        db_path=db_file,
    )
    logs2 = {l["source"]: l for l in get_refresh_status(db_file)}
    assert logs2["Devpost"]["last_attempted_at"] == t2.isoformat()
    # PRESERVED: last_successful_at remains t1!
    assert logs2["Devpost"]["last_successful_at"] == t1.isoformat()
    assert logs2["Devpost"]["status"] == "failure"
    assert logs2["Devpost"]["error_message"] == "Network Timeout"


# -----------------------------------------------------------------------------
# Scenario 8: Staleness Detection Against Configurable Threshold
# -----------------------------------------------------------------------------
def test_staleness_detection(tmp_path):
    db_file = tmp_path / "test_stale.db"
    init_db(db_file)
    now_ref = datetime(2026, 10, 22, 12, 0, 0, tzinfo=timezone.utc)

    # Devpost refreshed 10 hours ago (< 24h -> FRESH)
    record_refresh_log(
        source="Devpost",
        status="success",
        discovered_count=5,
        attempted_at=now_ref - timedelta(hours=10),
        successful_at=now_ref - timedelta(hours=10),
        db_path=db_file,
    )
    # Devfolio refreshed 28 hours ago (> 24h -> STALE)
    record_refresh_log(
        source="Devfolio",
        status="success",
        discovered_count=5,
        attempted_at=now_ref - timedelta(hours=28),
        successful_at=now_ref - timedelta(hours=28),
        db_path=db_file,
    )

    freshness = get_catalog_freshness(db_path=db_file, threshold_hours=24, now=now_ref)
    assert freshness["is_fresh"] is False
    assert freshness["overall_status"] == "stale"
    assert freshness["sources"]["Devpost"]["is_fresh"] is True
    assert freshness["sources"]["Devfolio"]["is_fresh"] is False
    assert "Devfolio" in freshness["stale_sources"]
    assert "Devpost" in freshness["fresh_sources"]


# -----------------------------------------------------------------------------
# Scenario 9: Invalid Refresh Tokens Are Rejected (Fail Closed)
# -----------------------------------------------------------------------------
def test_invalid_refresh_tokens_are_rejected(monkeypatch):
    test_secret = "test-secure-refresh-token-xyz"
    monkeypatch.setenv("CATALOG_REFRESH_TOKEN", test_secret)

    # 1. Missing Authorization header -> 401
    r_missing = client.post("/sync/refresh")
    assert r_missing.status_code == 401
    assert r_missing.json()["status"] == "unauthorized"

    # 2. Invalid token -> 401
    r_bad = client.post("/sync/refresh", headers={"Authorization": "Bearer wrong-token"})
    assert r_bad.status_code == 401
    assert r_bad.json()["status"] == "unauthorized"

    # 3. Non-Bearer schema -> 401
    r_basic = client.post("/sync/refresh", headers={"Authorization": f"Basic {test_secret}"})
    assert r_basic.status_code == 401

    # 4. Fail closed when server has NO token configured -> 401
    monkeypatch.delenv("CATALOG_REFRESH_TOKEN", raising=False)
    r_no_env = client.post("/sync/refresh", headers={"Authorization": f"Bearer {test_secret}"})
    assert r_no_env.status_code == 401


# -----------------------------------------------------------------------------
# Scenario 10: Concurrent Refresh Protection Works
# -----------------------------------------------------------------------------
def test_concurrent_refresh_protection(tmp_path, monkeypatch):
    test_secret = "test-token-concurrency"
    monkeypatch.setenv("CATALOG_REFRESH_TOKEN", test_secret)

    # Acquire lock manually to simulate an active running refresh
    assert _REFRESH_LOCK.acquire(blocking=False)
    try:
        # A) Internal function call raises ConcurrencyError
        with pytest.raises(ConcurrencyError, match="already in progress"):
            refresh_discovery_sources(db_path=tmp_path / "c.db")

        # B) HTTP endpoint returns 409 Conflict
        res = client.post(
            "/sync/refresh", headers={"Authorization": f"Bearer {test_secret}"}
        )
        assert res.status_code == 409
        assert res.json()["status"] == "conflict"
    finally:
        _REFRESH_LOCK.release()


# -----------------------------------------------------------------------------
# Scenario 11: An Old Fallback Snapshot Cannot Overwrite Newer Verified Data
# -----------------------------------------------------------------------------
def test_old_fallback_snapshot_cannot_overwrite_newer_verified_data(tmp_path):
    db_file = tmp_path / "test_fallback_protect.db"
    init_db(db_file)
    now_ref = datetime(2026, 11, 19, 12, 0, 0, tzinfo=timezone.utc)

    # 1. Existing live-verified item updated on Nov 15, 2026
    conn = get_db_connection(db_file)
    with conn:
        conn.execute(
            """
            INSERT INTO hackathons (
                id, fingerprint, title, url, source, deadline, reg_starts_at, reg_ends_at,
                starts_at, ends_at, description, tags, prize, prize_amount, prize_currency,
                location, status, created_at, updated_at, last_verified_at
            ) VALUES (
                'h_live', 'h_live', 'Live Verified Hackathon', 'https://example.com/live', 'Devpost',
                '2026-11-30', '2026-10-01', '2026-11-30', '2026-12-01', '2026-12-03',
                'Live Description', '["AI"]', '$50,000', 50000.0, 'USD',
                'Online', 'open', '2026-10-01', '2026-11-15T00:00:00Z', '2026-11-15T00:00:00Z'
            )
            """
        )
    conn.close()

    # 2. Stale fallback seed (from Oct 17, 2026) attempts to update 'h_live' with stale data
    fallback_payload = {
        "hackathons": [
            {
                "id": "h_live",
                "title": "Stale Fallback Title",
                "url": "https://example.com/stale",
                "source": "Devpost",
                "status": "closed",
                "description": "Stale Fallback Description",
                "last_verified_at": "2026-10-17T00:00:00Z",
            }
        ]
    }

    sync_res = sync_upstream_json(
        fallback_payload, is_fallback=True, db_path=db_file, now=now_ref
    )

    # Item must retain live verified title and description
    item = get_hackathon("h_live", db_path=db_file, now=now_ref)
    assert item["title"] == "Live Verified Hackathon"
    assert item["description"] == "Live Description"
    assert item["last_verified_at"] == "2026-11-15T00:00:00Z"


# -----------------------------------------------------------------------------
# Scenario 12: Registration and Event Lifecycle Statuses Remain Accurate
# -----------------------------------------------------------------------------
def test_registration_and_event_lifecycle_statuses_remain_accurate():
    ref_now = datetime(2026, 10, 20, 12, 0, 0, tzinfo=timezone.utc)

    # 1. Confirmed open: deadline in future
    h_open = normalize_event_dates_and_status(
        {
            "status": "open",
            "reg_ends_at": "2026-11-10T23:59:59Z",
            "ends_at": "2026-11-15T18:00:00Z",
        },
        now=ref_now,
    )
    assert h_open["status"] == "open"

    # 2. Registration closed, but event not ended
    h_closed = normalize_event_dates_and_status(
        {
            "status": "open",
            "reg_ends_at": "2026-10-18T23:59:59Z",  # in past
            "ends_at": "2026-11-15T18:00:00Z",      # in future
        },
        now=ref_now,
    )
    assert h_closed["status"] == "closed"

    # 3. Genuinely ended event
    h_ended = normalize_event_dates_and_status(
        {
            "status": "open",
            "reg_ends_at": "2026-10-15T23:59:59Z",
            "ends_at": "2026-10-18T18:00:00Z",  # in past
        },
        now=ref_now,
    )
    assert h_ended["status"] == "ended"

    # 4. Upcoming registration
    h_upcoming = normalize_event_dates_and_status(
        {
            "status": "upcoming",
            "reg_starts_at": "2026-11-01T00:00:00Z",  # in future
            "reg_ends_at": "2026-11-20T23:59:59Z",
            "ends_at": "2026-11-25T18:00:00Z",
        },
        now=ref_now,
    )
    assert h_upcoming["status"] == "upcoming"

    # 5. Unknown registration status is preserved honestly
    h_unknown = normalize_event_dates_and_status(
        {
            "status": "unverified",
            "reg_starts_at": "",
            "reg_ends_at": "",
            "ends_at": "",
        },
        now=ref_now,
    )
    assert h_unknown["status"] == "unknown"


# -----------------------------------------------------------------------------
# Additional Helper Tests: Error Sanitization and Freshness Endpoint
# -----------------------------------------------------------------------------
def test_error_message_sanitization():
    raw_err = "Failed connection to https://api.devfolio.co?token=secret12345&auth=supersecret Bearer my-jwt-token at C:\\Users\\User\\secret\\db.sqlite"
    sanitized = sanitize_error_message(raw_err)
    assert "secret12345" not in sanitized
    assert "supersecret" not in sanitized
    assert "my-jwt-token" not in sanitized
    assert "C:\\Users" not in sanitized
    assert "[REDACTED]" in sanitized
    assert "[LOCAL_PATH]" in sanitized


def test_catalog_freshness_http_endpoint():
    res = client.get("/catalog/freshness")
    assert res.status_code == 200
    data = res.json()
    assert "is_fresh" in data
    assert "overall_status" in data
    assert "sources" in data
    assert "total_hackathons" in data


def test_generate_markdown_summary_output():
    from scripts.run_catalog_refresh import generate_markdown_summary

    refresh_summary = {
        "overall_status": "success",
        "sources": {
            "Devpost": {"status": "success", "discovered": 18, "added": 3, "updated": 5},
            "Devfolio": {"status": "success", "discovered": 12, "added": 1, "updated": 4},
            "Unstop": {"status": "success", "discovered": 17, "added": 2, "updated": 6},
        },
    }
    freshness_data = {
        "is_fresh": True,
        "total_hackathons": 47,
        "open_hackathons": 32,
        "sources": {
            "Devpost": {"last_successful_at": "2026-11-19T06:00:00Z"},
            "Devfolio": {"last_successful_at": "2026-11-19T06:00:00Z"},
            "Unstop": {"last_successful_at": "2026-11-19T06:00:00Z"},
        },
    }

    md = generate_markdown_summary(refresh_summary, freshness_data, threshold_hours=24)
    assert "# HackPulse Catalog Refresh" in md
    assert "**Overall status:** SUCCESS" in md
    assert "### Devpost" in md
    assert "- **Discovered:** 18" in md
    assert "- **State:** FRESH" in md
    assert "- **Threshold:** 24h" in md
    assert "Total Opportunities:** 47 (Open: 32)" in md
