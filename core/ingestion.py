"""Hackathon Discovery, Multi-Source Refresh, and Rolling Context Retention Engine.

Strict discovery scope:
- Devpost: Open & Upcoming hackathons (via https://devpost.com/hackathons filters)
- Devfolio: Open hackathons (via https://devfolio.co/hackathons/open, filter=application_open)
- Unstop: Open student hackathons (via https://unstop.com/hackathons?oppstatus=open&payment=unpaid&usertype=students&domain=2)

Accurate status & date distinction:
- Distinguishes registration status, reg_starts_at, reg_ends_at, starts_at, ends_at, and last_verified_at.
- Enforces 30-day retention for confirmed inactive records while preserving active opportunities indefinitely.
- Idempotent upserts and fault-tolerant per-source refresh logging.
"""

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from bs4 import BeautifulSoup
import httpx

from core.database import (
    get_db_connection,
    init_db,
    normalize_event_dates_and_status,
    parse_event_datetime,
    purge_expired_hackathons,
    record_refresh_log,
    sanitize_html,
    sanitize_prize,
)

logger = logging.getLogger("hackpulse.ingestion")

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
}

MISSING_OBSERVATION_THRESHOLD = 3
DEFAULT_RETENTION_DAYS = 30


def parse_date_range_to_iso(range_str: Optional[str]) -> Tuple[str, str]:
    """Parses date range strings like 'Aug 31 - Oct 23, 2026' into (start_iso, end_iso)."""
    if not range_str or " - " not in range_str:
        return "", ""
    parts = range_str.split(" - ")
    start_raw = parts[0].strip()
    end_raw = parts[1].strip()

    year_match = re.search(r"\b(202\d)\b", end_raw)
    year = year_match.group(1) if year_match else str(datetime.now(timezone.utc).year)

    if not re.search(r"\b(202\d)\b", start_raw):
        start_full = f"{start_raw}, {year}"
    else:
        start_full = start_raw

    start_iso = ""
    end_iso = ""
    for fmt in ("%b %d, %Y", "%B %d, %Y", "%b %d %Y", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(start_full, fmt).replace(tzinfo=timezone.utc)
            start_iso = dt.isoformat()
            break
        except Exception:
            pass

    for fmt in ("%b %d, %Y", "%B %d, %Y", "%b %d %Y", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(end_raw, fmt).replace(tzinfo=timezone.utc)
            end_iso = dt.isoformat()
            break
        except Exception:
            pass

    return start_iso, end_iso


class DiscoveryError(Exception):
    """Raised when an approved discovery source fails or changes structure."""
    pass


# -----------------------------------------------------------------------------
# 1. Devpost Discovery (Open & Upcoming only)
# -----------------------------------------------------------------------------
def fetch_devpost_hackathons(
    client: Optional[httpx.Client] = None,
    max_pages: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Discovers open and upcoming hackathons exclusively from Devpost.
    Base listing: https://devpost.com/hackathons with open/upcoming filters.
    Strictly avoids crawling the ended/historical catalogue.
    """
    discovered: List[Dict[str, Any]] = []
    own_client = False
    if client is None:
        client = httpx.Client(headers=DEFAULT_HEADERS, timeout=20.0, follow_redirects=True)
        own_client = True

    try:
        # Strictly explore open and upcoming discovery sections
        for status_filter in ["open", "upcoming"]:
            page = 1
            while True:
                url = f"https://devpost.com/api/hackathons?status={status_filter}&page={page}"
                try:
                    resp = client.get(url)
                except Exception as e:
                    raise DiscoveryError(f"Devpost connection error for {status_filter} on page {page}: {e}")

                if resp.status_code != 200:
                    raise DiscoveryError(
                        f"Devpost returned HTTP {resp.status_code} for {status_filter} page {page}. Expected 200."
                    )

                try:
                    data = resp.json()
                except Exception as e:
                    raise DiscoveryError(f"Devpost returned unparseable JSON on page {page}: {e}")

                raw_list = data.get("hackathons", [])
                meta = data.get("meta", {})
                if not isinstance(raw_list, list):
                    raise DiscoveryError("Devpost response missing 'hackathons' list structure.")

                for h in raw_list:
                    h_id = f"devpost_{h.get('id')}"
                    title = sanitize_html(h.get("title", ""))
                    url_val = (h.get("url") or "").strip()
                    open_state = (h.get("open_state") or "").lower()

                    # Explicit registration status mapping
                    if open_state == "open":
                        status = "open"
                    elif open_state == "upcoming":
                        status = "upcoming"
                    elif open_state in ["ended", "closed"]:
                        # If Devpost returned ended in open/upcoming filter, honour accurately
                        status = "ended"
                    else:
                        status = "unknown"

                    sub_dates = h.get("submission_period_dates", "")
                    reg_start, reg_end = parse_date_range_to_iso(sub_dates)

                    # Distinct format/location
                    loc = "Online"
                    disp_loc = h.get("displayed_location")
                    if isinstance(disp_loc, dict):
                        loc = disp_loc.get("location", "Online")
                    elif isinstance(disp_loc, str) and disp_loc.strip():
                        loc = disp_loc.strip()

                    themes = [
                        t.get("name")
                        for t in h.get("themes", [])
                        if isinstance(t, dict) and t.get("name")
                    ]

                    # Prize sanitization
                    prize_html = h.get("prize_amount", "")
                    prize_clean = sanitize_html(prize_html) if prize_html else "Unknown"

                    discovered.append({
                        "id": h_id,
                        "title": title,
                        "url": url_val,
                        "source": "Devpost",
                        "status": status,
                        "reg_starts_at": reg_start,
                        "reg_ends_at": reg_end,
                        "deadline": reg_end,
                        "starts_at": reg_start,
                        "ends_at": reg_end,
                        "location": loc,
                        "tags": themes,
                        "prize": prize_clean,
                        "description": h.get("organization_name", "") or "",
                    })

                total_count = meta.get("total_count", 0)
                per_page = meta.get("per_page", 9) or 9
                max_page_calc = (total_count + per_page - 1) // per_page if per_page else 1

                if page >= max_page_calc or (max_pages and page >= max_pages):
                    break
                page += 1

    finally:
        if own_client:
            client.close()

    return discovered


# -----------------------------------------------------------------------------
# 2. Devfolio Discovery (Open Hackathons only)
# -----------------------------------------------------------------------------
def fetch_devfolio_hackathons(
    client: Optional[httpx.Client] = None,
    max_pages: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Discovers open hackathons exclusively from Devfolio.
    Designated section: https://devfolio.co/hackathons/open (filter=application_open).
    Strictly avoids crawling past hackathons.
    """
    discovered: List[Dict[str, Any]] = []
    own_client = False
    if client is None:
        client = httpx.Client(headers=DEFAULT_HEADERS, timeout=20.0, follow_redirects=True)
        own_client = True

    try:
        page = 1
        while True:
            url = f"https://api.devfolio.co/api/hackathons?page={page}&filter=application_open"
            try:
                resp = client.get(url)
            except Exception as e:
                raise DiscoveryError(f"Devfolio connection error on page {page}: {e}")

            if resp.status_code != 200:
                raise DiscoveryError(
                    f"Devfolio returned HTTP {resp.status_code} on page {page}. Expected 200."
                )

            try:
                data = resp.json()
            except Exception as e:
                raise DiscoveryError(f"Devfolio returned unparseable JSON on page {page}: {e}")

            raw_list = data.get("result", [])
            if not isinstance(raw_list, list):
                raise DiscoveryError("Devfolio response missing 'result' list structure.")

            for h in raw_list:
                slug = (h.get("slug") or h.get("uuid") or "").strip()
                if not slug:
                    continue
                h_id = f"devfolio_{slug}"
                title = sanitize_html(h.get("name", ""))
                url_val = f"https://{slug}.devfolio.co"
                loc = "Online" if h.get("is_online") else "In-Person"

                themes = [
                    t.get("name")
                    for t in h.get("themes", [])
                    if isinstance(t, dict) and t.get("name")
                ]

                # Extract accurate and distinct dates
                setting = h.get("hackathon_setting") or {}
                reg_start = setting.get("reg_starts_at") or h.get("registrations_open_at") or ""
                reg_end = setting.get("reg_ends_at") or h.get("registrations_close_at") or ""
                starts_at = h.get("starts_at") or ""
                ends_at = h.get("ends_at") or ""

                discovered.append({
                    "id": h_id,
                    "title": title,
                    "url": url_val,
                    "source": "Devfolio",
                    "status": "open",
                    "reg_starts_at": reg_start,
                    "reg_ends_at": reg_end,
                    "deadline": reg_end,
                    "starts_at": starts_at,
                    "ends_at": ends_at,
                    "location": loc,
                    "tags": themes,
                    "prize": "Unknown",
                    "description": h.get("tagline") or h.get("desc") or "",
                })

            pages = data.get("pages", 1) or 1
            if page >= pages or (max_pages and page >= max_pages):
                break
            page += 1

    finally:
        if own_client:
            client.close()

    return discovered


# -----------------------------------------------------------------------------
# 3. Unstop Discovery (Open student hackathons only)
# -----------------------------------------------------------------------------
def fetch_unstop_hackathons(
    client: Optional[httpx.Client] = None,
    max_pages: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Discovers open hackathons exclusively from Unstop.
    Designated section: https://unstop.com/hackathons?oppstatus=open&payment=unpaid&usertype=students&domain=2.
    Strictly avoids crawling non-student or closed archives.
    """
    discovered: List[Dict[str, Any]] = []
    own_client = False
    if client is None:
        client = httpx.Client(headers=DEFAULT_HEADERS, timeout=20.0, follow_redirects=True)
        own_client = True

    try:
        page = 1
        while True:
            url = (
                "https://unstop.com/api/public/opportunity/search-result"
                "?opportunity=hackathons&oppstatus=open&payment=unpaid&usertype=students&domain=2"
                f"&page={page}"
            )
            try:
                resp = client.get(url)
            except Exception as e:
                raise DiscoveryError(f"Unstop connection error on page {page}: {e}")

            if resp.status_code != 200:
                raise DiscoveryError(
                    f"Unstop returned HTTP {resp.status_code} on page {page}. Expected 200."
                )

            try:
                body = resp.json()
            except Exception as e:
                raise DiscoveryError(f"Unstop returned unparseable JSON on page {page}: {e}")

            data_obj = body.get("data", {})
            raw_list = data_obj.get("data", [])
            if not isinstance(raw_list, list):
                raise DiscoveryError("Unstop response missing 'data.data' list structure.")

            for h in raw_list:
                item_id = h.get("id")
                if not item_id:
                    continue
                h_id = f"unstop_{item_id}"
                title = sanitize_html(h.get("title", ""))
                pub_url = (h.get("public_url") or "").strip()
                url_val = (
                    pub_url
                    if pub_url.startswith("http")
                    else f"https://unstop.com/{pub_url.lstrip('/')}"
                )

                # Authoritative registration dates
                reqs = h.get("regnRequirements") or {}
                reg_start = reqs.get("start_regn_dt") or ""
                reg_end = reqs.get("end_regn_dt") or ""
                reg_st = (reqs.get("reg_status") or "").upper()
                opp_st = (h.get("status") or "").upper()

                # Accurately verify registration status
                if reg_st == "STARTED" or opp_st == "LIVE":
                    status = "open"
                elif reg_st in ["CLOSED", "ENDED"] or opp_st in ["EXPIRED", "ENDED", "COMPLETED"]:
                    status = "ended"
                else:
                    status = "unknown"

                # Parse location
                loc = "Online"
                locations = h.get("locations", [])
                if locations and isinstance(locations, list):
                    first_loc = locations[0]
                    if isinstance(first_loc, dict):
                        city = first_loc.get("city") or first_loc.get("country")
                        if city:
                            loc = city

                # Parse prize breakdown
                prize_str = "Unknown"
                prizes_list = h.get("prizes", []) or []
                if prizes_list and isinstance(prizes_list, list):
                    first_p = prizes_list[0]
                    cash = first_p.get("cash")
                    others = first_p.get("others")
                    curr = first_p.get("currency") or "INR"
                    if cash:
                        prize_str = f"₹{cash}" if "rupee" in curr.lower() else f"{cash} {curr}"
                    elif others:
                        prize_str = sanitize_html(others)

                org = h.get("organisation")
                org_name = org.get("name") if isinstance(org, dict) else ""

                discovered.append({
                    "id": h_id,
                    "title": title,
                    "url": url_val,
                    "source": "Unstop",
                    "status": status,
                    "reg_starts_at": reg_start,
                    "reg_ends_at": reg_end,
                    "deadline": reg_end,
                    "starts_at": h.get("start_date") or "",
                    "ends_at": h.get("end_date") or "",
                    "location": loc,
                    "tags": [
                        t.get("name")
                        for t in h.get("tags", [])
                        if isinstance(t, dict) and t.get("name")
                    ],
                    "prize": prize_str,
                    "description": org_name or "",
                })

            last_page = data_obj.get("last_page", 1) or 1
            if page >= last_page or (max_pages and page >= max_pages):
                break
            page += 1

    finally:
        if own_client:
            client.close()

    return discovered


# -----------------------------------------------------------------------------
# 4. Multi-Source Refresh & 30-Day Context Retention Orchestrator
# -----------------------------------------------------------------------------
def refresh_discovery_sources(
    db_path: Optional[Path | str] = None,
    retention_days: int = DEFAULT_RETENTION_DAYS,
    sources: Optional[List[str]] = None,
    client: Optional[httpx.Client] = None,
    now: Optional[datetime] = None,
    max_pages_per_source: Optional[int] = None,
) -> Dict[str, Any]:
    """Executes multi-source daily refresh and rolling 30-day context retention.
    
    Guarantees:
    - Queries exclusively the 3 designated sections (Devpost, Devfolio, Unstop).
    - Preserves last known good data if any individual source fails (no mass deletion, no false closures).
    - Discovered events are upserted idempotently.
    - Tracks last_verified_at, inactive_since, and missing_count.
    - Requires 3 consecutive missing observations before declaring an unlisted item inactive.
    - Purges inactive records only after retention_days past confirmed inactivity.
    - Never deletes active (open/upcoming) opportunities regardless of age.
    """
    init_db(db_path)
    if now is None:
        now = datetime.now(timezone.utc)
    now_iso = now.isoformat()

    available_sources = {
        "Devpost": lambda: fetch_devpost_hackathons(client=client, max_pages=max_pages_per_source),
        "Devfolio": lambda: fetch_devfolio_hackathons(client=client, max_pages=max_pages_per_source),
        "Unstop": lambda: fetch_unstop_hackathons(client=client, max_pages=max_pages_per_source),
    }

    target_sources = [s for s in (sources or ["Devpost", "Devfolio", "Unstop"]) if s in available_sources]
    summary_report: Dict[str, Any] = {
        "refreshed_at": now_iso,
        "sources": {},
        "total_discovered": 0,
        "total_updated": 0,
        "total_added": 0,
        "total_purged": 0,
    }

    conn = get_db_connection(db_path)

    for src_name in target_sources:
        src_fn = available_sources[src_name]
        try:
            items = src_fn()
            added_count = 0
            updated_count = 0
            discovered_ids = set()

            with conn:
                for item in items:
                    h_id = item.get("id")
                    if not h_id:
                        continue
                    discovered_ids.add(h_id)

                    title = item.get("title", "")
                    url_val = item.get("url", "")
                    loc = item.get("location", "Online")
                    raw_tags = item.get("tags", [])
                    tags_json = json.dumps(raw_tags if isinstance(raw_tags, list) else [])
                    raw_prize = item.get("prize", "Unknown")
                    prize_str, prize_amt, prize_curr = sanitize_prize(raw_prize)
                    desc = item.get("description", "")
                    raw_str = json.dumps(item)

                    normalized = normalize_event_dates_and_status(item, now=now)
                    reg_starts = normalized["reg_starts_at"]
                    reg_ends = normalized["reg_ends_at"]
                    starts_at = normalized["starts_at"]
                    ends_at = normalized["ends_at"]
                    deadline = normalized["deadline"]
                    status = normalized["status"]

                    existing = conn.execute(
                        "SELECT id, status, inactive_since, missing_count FROM hackathons WHERE id = ?",
                        (h_id,),
                    ).fetchone()

                    # Date and status discrepancy checking
                    if status in ["open", "upcoming"]:
                        inactive_since = None
                        missing_count = 0
                    else:
                        inactive_since = (
                            existing["inactive_since"]
                            if (existing and existing["inactive_since"])
                            else now_iso
                        )
                        missing_count = (existing["missing_count"] if existing else 0)

                    if existing:
                        conn.execute(
                            """
                            UPDATE hackathons SET
                                title = ?,
                                url = ?,
                                source = ?,
                                deadline = ?,
                                reg_starts_at = ?,
                                reg_ends_at = ?,
                                starts_at = ?,
                                ends_at = ?,
                                description = ?,
                                tags = ?,
                                prize = ?,
                                prize_amount = ?,
                                prize_currency = ?,
                                location = ?,
                                status = ?,
                                raw_json = ?,
                                updated_at = ?,
                                last_verified_at = ?,
                                inactive_since = ?,
                                missing_count = ?
                            WHERE id = ?
                            """,
                            (
                                title,
                                url_val,
                                src_name,
                                deadline,
                                reg_starts,
                                reg_ends,
                                starts_at,
                                ends_at,
                                desc,
                                tags_json,
                                prize_str,
                                prize_amt,
                                prize_curr,
                                loc,
                                status,
                                raw_str,
                                now_iso,
                                now_iso,
                                inactive_since,
                                missing_count,
                                h_id,
                            ),
                        )
                        updated_count += 1
                    else:
                        conn.execute(
                            """
                            INSERT INTO hackathons (
                                id, fingerprint, title, url, source, deadline,
                                reg_starts_at, reg_ends_at, starts_at, ends_at,
                                description, tags, prize, prize_amount, prize_currency,
                                location, status, raw_json, created_at, updated_at,
                                last_verified_at, inactive_since, missing_count
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                h_id,
                                h_id,
                                title,
                                url_val,
                                src_name,
                                deadline,
                                reg_starts,
                                reg_ends,
                                starts_at,
                                ends_at,
                                desc,
                                tags_json,
                                prize_str,
                                prize_amt,
                                prize_curr,
                                loc,
                                status,
                                raw_str,
                                now_iso,
                                now_iso,
                                now_iso,
                                inactive_since,
                                missing_count,
                            ),
                        )
                        added_count += 1

                # Conservative missing-observation policy:
                # Existing items from this source not returned in this successful run
                existing_source_items = conn.execute(
                    "SELECT id, status, reg_ends_at, ends_at, inactive_since, missing_count FROM hackathons WHERE LOWER(source) = LOWER(?)",
                    (src_name,),
                ).fetchall()

                for row in existing_source_items:
                    r_id = row["id"]
                    if r_id not in discovered_ids:
                        new_missing = (row["missing_count"] or 0) + 1
                        curr_status = row["status"]
                        dt_reg_ends = parse_event_datetime(row["reg_ends_at"])
                        dt_ends = parse_event_datetime(row["ends_at"])

                        # Declare inactive if repeated missing observation OR deadline passed
                        is_now_inactive = False
                        if (dt_reg_ends and dt_reg_ends <= now) or (dt_ends and dt_ends <= now):
                            is_now_inactive = True
                        elif new_missing >= MISSING_OBSERVATION_THRESHOLD:
                            is_now_inactive = True

                        if is_now_inactive and curr_status not in ["ended", "closed"]:
                            new_status = "ended"
                            inact_since = row["inactive_since"] or now_iso
                        else:
                            new_status = curr_status
                            inact_since = row["inactive_since"]

                        conn.execute(
                            "UPDATE hackathons SET missing_count = ?, status = ?, inactive_since = ?, updated_at = ? WHERE id = ?",
                            (new_missing, new_status, inact_since, now_iso, r_id),
                        )

            record_refresh_log(
                source=src_name,
                status="success",
                discovered_count=len(items),
                updated_count=updated_count,
                error_message=None,
                db_path=db_path,
            )

            summary_report["sources"][src_name] = {
                "status": "success",
                "discovered": len(items),
                "added": added_count,
                "updated": updated_count,
                "error": None,
            }
            summary_report["total_discovered"] += len(items)
            summary_report["total_added"] += added_count
            summary_report["total_updated"] += updated_count

        except Exception as e:
            err_msg = str(e)
            logger.error(f"Refresh failed for source {src_name}: {err_msg}")
            # Record failure in refresh_logs; preserve last known good data
            record_refresh_log(
                source=src_name,
                status="failure",
                discovered_count=0,
                updated_count=0,
                error_message=err_msg,
                db_path=db_path,
            )
            summary_report["sources"][src_name] = {
                "status": "failure",
                "discovered": 0,
                "added": 0,
                "updated": 0,
                "error": err_msg,
            }

    conn.close()

    # Step 5: Execute 30-day retention purge on confirmed inactive records
    purged = purge_expired_hackathons(db_path=db_path, retention_days=retention_days, now=now)
    summary_report["total_purged"] = purged

    return summary_report
