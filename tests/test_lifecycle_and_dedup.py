"""Targeted unit and integration tests for HackPulse Alexa+ lifecycle filtering and deduplication.

Covers the 6 required scenarios:
1. Duplicate records merging with best official URL and non-conflicting fields, without merging distinct editions.
2. Open registration with future event date appears in Open Hackathons.
3. Passed registration deadline with an event that has not ended (status='closed') is excluded from both Open and Past.
4. Genuinely ended event within 30 days appears in Past Hackathons.
5. Event older than 30 days is excluded from Past Hackathons (rolling 30-day window).
6. Unknown/unverified status is preserved as 'unknown'.
"""

from datetime import datetime, timezone, timedelta
from pathlib import Path
import pytest

from core.database import (
    canonicalize_url,
    normalize_event_identity,
    merge_hackathon_records,
    deduplicate_hackathons,
    normalize_event_dates_and_status,
    query_hackathons,
    init_db,
    sync_upstream_json,
)


@pytest.fixture
def test_db_path(tmp_path):
    """Provides an isolated SQLite database for lifecycle testing."""
    db_file = tmp_path / "lifecycle_test.db"
    init_db(db_file)
    return db_file


def test_scenario_1_deduplication_and_distinct_editions():
    """1. Deduplicate records across sources, prefer canonical official URLs, merge non-conflicting fields,
    and verify distinct editions of recurring hackathons are never merged.
    """
    # URL Canonicalization
    devpost_sub = canonicalize_url("https://ai-challenge.devpost.com/?ref=search")
    devpost_dir = canonicalize_url("https://devpost.com/hackathons/ai-challenge?preview=true")
    assert devpost_sub == "https://ai-challenge.devpost.com"
    assert devpost_dir == "https://ai-challenge.devpost.com"
    assert canonicalize_url("https://devpost.com/hackathons") == ""

    devfolio_sub = canonicalize_url("https://hack-summit.devfolio.co/#schedule")
    devfolio_dir = canonicalize_url("https://devfolio.co/hackathons/hack-summit")
    assert devfolio_sub == "https://hack-summit.devfolio.co"
    assert devfolio_dir == "https://hack-summit.devfolio.co"

    # Distinct editions preservation
    v1_ident = normalize_event_identity("BIOS V1", "Devfolio")
    v2_ident = normalize_event_identity("BIOS V2", "Devfolio")
    assert v1_ident != v2_ident

    conf_1 = normalize_event_identity("Confluence Buildathon 1.0", "Unstop")
    conf_2 = normalize_event_identity("Confluence Buildathon 2.0", "Unstop")
    assert conf_1 != conf_2

    hack25 = normalize_event_identity("StoxraHack 2025", "FinTech")
    hack26 = normalize_event_identity("StoxraHack 2026", "FinTech")
    assert hack25 != hack26

    # Merging records with canonical URL and non-conflicting fields
    primary = {
        "id": "item_1",
        "title": "Autonomous AI Agent Hackathon",
        "url": "https://devpost.com/hackathons?search=ai",
        "source": "Devpost",
        "description": "Build agentic workflows.",
        "tags": ["AI", "Agents"],
        "prize": "$10,000",
        "location": "Online",
        "reg_ends_at": "",
    }
    duplicate = {
        "id": "item_2",
        "title": "Autonomous AI Agent Hackathon",
        "url": "https://ai-agents.devpost.com",
        "source": "Devpost",
        "tags": ["Python", "Agents"],
        "reg_ends_at": "2026-11-15T23:59:59+00:00",
        "starts_at": "2026-11-20T00:00:00+00:00",
        "ends_at": "2026-11-22T00:00:00+00:00",
    }

    merged = merge_hackathon_records(primary, duplicate)
    assert merged["url"] == "https://ai-agents.devpost.com"
    assert merged["reg_ends_at"] == "2026-11-15T23:59:59+00:00"
    assert merged["starts_at"] == "2026-11-20T00:00:00+00:00"
    assert set(merged["tags"]) == {"AI", "Agents", "Python"}

    # Full deduplication list operation
    distinct_v1 = {
        "id": "bios_v1",
        "title": "BIOS V1",
        "url": "https://bios-v1.devfolio.co",
        "source": "Devfolio",
    }
    distinct_v2 = {
        "id": "bios_v2",
        "title": "BIOS V2",
        "url": "https://bios-v2.devfolio.co",
        "source": "Devfolio",
    }
    deduped = deduplicate_hackathons([primary, duplicate, distinct_v1, distinct_v2])
    assert len(deduped) == 3
    titles = [d["title"] for d in deduped]
    assert "Autonomous AI Agent Hackathon" in titles
    assert "BIOS V1" in titles
    assert "BIOS V2" in titles


def test_scenario_2_open_registration_with_future_event_date(test_db_path):
    """2. Open registration with a future event date belongs in Open Hackathons."""
    ref_now = datetime.now(timezone.utc)

    item = {
        "id": "open_future_event",
        "title": "NextGen AI Buildathon 2026",
        "url": "https://nextgen.devpost.com",
        "source": "Devpost",
        "status": "open",
        "reg_starts_at": "2026-10-01T00:00:00+00:00",
        "reg_ends_at": (ref_now + timedelta(days=10)).isoformat(),
        "starts_at": (ref_now + timedelta(days=15)).isoformat(),
        "ends_at": (ref_now + timedelta(days=17)).isoformat(),
        "deadline": (ref_now + timedelta(days=10)).isoformat(),
        "location": "Online",
    }

    norm = normalize_event_dates_and_status(item, now=ref_now)
    assert norm["status"] == "open"
    item.update(norm)

    sync_upstream_json({"hackathons": [item]}, is_fallback=False, db_path=test_db_path)

    open_results = query_hackathons(status="open", db_path=test_db_path)
    assert any(h["id"] == "open_future_event" for h in open_results)

    past_results = query_hackathons(status="past", db_path=test_db_path)
    assert not any(h["id"] == "open_future_event" for h in past_results)


def test_scenario_3_passed_registration_deadline_with_event_not_ended(test_db_path):
    """3. A passed registration deadline does not mean the event itself has ended.
    Status is 'closed', excluded from Open Hackathons, and excluded from Past Hackathons.
    """
    ref_now = datetime.now(timezone.utc)

    # Registration ended 2 days ago, but event hacking runs 7 days from now
    item = {
        "id": "closed_reg_event",
        "title": "Competitive Coding Hackathon",
        "url": "https://compcode.devfolio.co",
        "source": "Devfolio",
        "status": "open",  # Raw upstream may still say open or active
        "reg_starts_at": "2026-10-01T00:00:00+00:00",
        "reg_ends_at": (ref_now - timedelta(days=2)).isoformat(),
        "starts_at": (ref_now + timedelta(days=7)).isoformat(),
        "ends_at": (ref_now + timedelta(days=9)).isoformat(),
        "deadline": (ref_now - timedelta(days=2)).isoformat(),
        "location": "Online",
    }

    norm = normalize_event_dates_and_status(item, now=ref_now)
    assert norm["status"] == "closed", "Passed reg deadline for future event must normalize to 'closed', not 'ended'"
    item.update(norm)

    sync_upstream_json({"hackathons": [item]}, is_fallback=False, db_path=test_db_path)

    # Must be excluded from Open Hackathons
    open_results = query_hackathons(status="open", db_path=test_db_path)
    assert not any(h["id"] == "closed_reg_event" for h in open_results)

    # Must be excluded from Past Hackathons (event hacking has not ended yet)
    past_results = query_hackathons(status="past", db_path=test_db_path)
    assert not any(h["id"] == "closed_reg_event" for h in past_results)


def test_scenario_4_genuinely_ended_event_within_30_days(test_db_path):
    """4. An event confirmed to have ended within the last 30 days appears in Past Hackathons,
    sorted newest first.
    """
    ref_now = datetime.now(timezone.utc)

    ended_10d_ago = {
        "id": "ended_recent_10d",
        "title": "October Sprint Hack 2026",
        "url": "https://octsprint.devpost.com",
        "source": "Devpost",
        "status": "ended",
        "starts_at": (ref_now - timedelta(days=12)).isoformat(),
        "ends_at": (ref_now - timedelta(days=10)).isoformat(),
        "deadline": (ref_now - timedelta(days=10)).isoformat(),
        "location": "Online",
    }

    ended_5d_ago = {
        "id": "ended_recent_5d",
        "title": "Super AI Hackathon",
        "url": "https://superai.devpost.com",
        "source": "Devpost",
        "status": "ended",
        "starts_at": (ref_now - timedelta(days=7)).isoformat(),
        "ends_at": (ref_now - timedelta(days=5)).isoformat(),
        "deadline": (ref_now - timedelta(days=5)).isoformat(),
        "location": "Online",
    }

    norm_10d = normalize_event_dates_and_status(ended_10d_ago, now=ref_now)
    norm_5d = normalize_event_dates_and_status(ended_5d_ago, now=ref_now)
    assert norm_10d["status"] == "ended"
    assert norm_5d["status"] == "ended"
    ended_10d_ago.update(norm_10d)
    ended_5d_ago.update(norm_5d)

    sync_upstream_json({"hackathons": [ended_10d_ago, ended_5d_ago]}, is_fallback=False, db_path=test_db_path)

    # Excluded from Open
    open_results = query_hackathons(status="open", db_path=test_db_path)
    assert not any(h["id"] in ["ended_recent_10d", "ended_recent_5d"] for h in open_results)

    # Included in Past Hackathons, sorted newest first (5d ago before 10d ago)
    past_results = query_hackathons(status="past", db_path=test_db_path)
    past_ids = [h["id"] for h in past_results]
    assert "ended_recent_5d" in past_ids
    assert "ended_recent_10d" in past_ids
    assert past_ids.index("ended_recent_5d") < past_ids.index("ended_recent_10d")


def test_scenario_5_event_older_than_30_days(test_db_path):
    """5. An event older than 30 days is excluded from the Past Hackathons view
    under the rolling 30-day window policy.
    """
    ref_now = datetime.now(timezone.utc)

    ended_45d_ago = {
        "id": "ended_ancient_45d",
        "title": "Summer Buildathon 2026",
        "url": "https://summerbuild.devpost.com",
        "source": "Devpost",
        "status": "ended",
        "starts_at": (ref_now - timedelta(days=47)).isoformat(),
        "ends_at": (ref_now - timedelta(days=45)).isoformat(),
        "deadline": (ref_now - timedelta(days=45)).isoformat(),
        "location": "Online",
    }

    norm_45d = normalize_event_dates_and_status(ended_45d_ago, now=ref_now)
    assert norm_45d["status"] == "ended"
    ended_45d_ago.update(norm_45d)

    sync_upstream_json({"hackathons": [ended_45d_ago]}, is_fallback=False, db_path=test_db_path)

    open_results = query_hackathons(status="open", db_path=test_db_path)
    assert not any(h["id"] == "ended_ancient_45d" for h in open_results)

    past_results = query_hackathons(status="past", db_path=test_db_path)
    assert not any(h["id"] == "ended_ancient_45d" for h in past_results), "Events older than 30 days must be excluded from Past view"


def test_scenario_6_unknown_unverified_status(test_db_path):
    """6. An unknown or unverified status remains honestly classified as 'unknown'.
    It is never falsely promoted to 'open' or 'ended'.
    """
    ref_now = datetime.now(timezone.utc)

    item_unverified = {
        "id": "unverified_event",
        "title": "Community Mystery Hack",
        "url": "https://mystery.example.org",
        "source": "Devpost",
        "status": "unknown",
        "deadline": "",
        "reg_ends_at": "",
        "starts_at": "Unknown",
        "ends_at": "",
        "location": "Online",
    }

    norm = normalize_event_dates_and_status(item_unverified, now=ref_now)
    assert norm["status"] == "unknown"
    item_unverified.update(norm)

    sync_upstream_json({"hackathons": [item_unverified]}, is_fallback=False, db_path=test_db_path)

    open_results = query_hackathons(status="open", db_path=test_db_path)
    assert not any(h["id"] == "unverified_event" for h in open_results)

    past_results = query_hackathons(status="past", db_path=test_db_path)
    assert not any(h["id"] == "unverified_event" for h in past_results)
