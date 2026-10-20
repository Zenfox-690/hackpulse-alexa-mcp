"""Database layer for HackPulse Alexa MCP.
Handles SQLite persistence, schema initialization, upstream JSON sync,
HTML/prize sanitization, session state tracking, persistent builder profiles,
and calibrated sprint plans.
"""

import json
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from bs4 import BeautifulSoup

DEFAULT_DB_PATH = Path(__file__).parent.parent / "data" / "hackpulse.db"
DEFAULT_SEED_PATH = Path(__file__).parent.parent / "data" / "upstream_seed.json"

DEFAULT_PROFILE_ID = "default"
DEFAULT_PROFILE_DATA = {
    "profile_id": "default",
    "name": "Demo Builder (CSE Student)",
    "country": "India",
    "skills": ["Python", "Playwright", "SQLite", "FastAPI", "Web Scraping", "JavaScript", "MCP"],
    "hours_per_week": 15,
    "budget_inr": 0.0,
    "prefer_online_only": 1,
    "bookmarks": ["0e18b6cf877bc442f9b92302350398fce6b84b05d01ac2f92ff5367616b259a8"],
}

CONVERSATIONAL_STOP_WORDS = {
    "find", "show", "me", "the", "best", "top", "good", "online",
    "in-person", "hackathons", "hackathon", "events", "for", "a",
    "an", "with", "under", "zero", "rupee", "free", "budget", "in",
    "my", "profile", "feasible", "search", "list", "get", "recommend",
    "match", "matches", "please", "can", "you", "tell", "about", "looking",
}


def get_db_connection(db_path: Optional[Path | str] = None) -> sqlite3.Connection:
    path = Path(db_path or DEFAULT_DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def init_db(db_path: Optional[Path | str] = None) -> None:
    conn = get_db_connection(db_path)
    now_iso = datetime.now(timezone.utc).isoformat()
    with conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS hackathons (
            id TEXT PRIMARY KEY,
            fingerprint TEXT,
            title TEXT NOT NULL,
            url TEXT NOT NULL,
            source TEXT NOT NULL,
            deadline TEXT,
            reg_starts_at TEXT,
            reg_ends_at TEXT,
            starts_at TEXT,
            ends_at TEXT,
            description TEXT,
            tags TEXT,
            prize TEXT,
            prize_amount REAL DEFAULT 0.0,
            prize_currency TEXT DEFAULT 'USD',
            location TEXT,
            status TEXT,
            benefits TEXT,
            raw_json TEXT,
            created_at TEXT,
            updated_at TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_hackathons_source ON hackathons(source);
        CREATE INDEX IF NOT EXISTS idx_hackathons_status ON hackathons(status);
        CREATE INDEX IF NOT EXISTS idx_hackathons_deadline ON hackathons(deadline);

        CREATE TABLE IF NOT EXISTS rule_audits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            hackathon_id TEXT NOT NULL,
            source_url TEXT NOT NULL,
            retrieval_timestamp TEXT NOT NULL,
            verification_status TEXT NOT NULL,
            raw_rules_text TEXT NOT NULL,
            eligibility TEXT,
            team_size TEXT,
            submission_requirements TEXT,
            hosting_requirements TEXT,
            content_hash TEXT,
            notes TEXT,
            FOREIGN KEY (hackathon_id) REFERENCES hackathons(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_rule_audits_hackathon ON rule_audits(hackathon_id);
        CREATE INDEX IF NOT EXISTS idx_rule_audits_timestamp ON rule_audits(retrieval_timestamp);

        CREATE TABLE IF NOT EXISTS mcp_sessions (
            session_id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            context_data TEXT
        );

        CREATE TABLE IF NOT EXISTS builder_profiles (
            profile_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            country TEXT NOT NULL,
            skills_json TEXT NOT NULL,
            hours_per_week INTEGER DEFAULT 15,
            budget_inr REAL DEFAULT 0.0,
            prefer_online_only INTEGER DEFAULT 1,
            bookmarks_json TEXT DEFAULT '[]',
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS sprint_plans (
            hackathon_id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            hours_per_week INTEGER DEFAULT 15,
            total_hours INTEGER DEFAULT 15,
            go_no_go_checkpoint TEXT NOT NULL,
            reuse_compliance_note TEXT NOT NULL,
            milestones_json TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        """)

        # Ensure default builder profile exists
        row = conn.execute(
            "SELECT profile_id, name FROM builder_profiles WHERE profile_id = ?",
            (DEFAULT_PROFILE_ID,),
        ).fetchone()
        if not row:
            conn.execute(
                """
                INSERT INTO builder_profiles (
                    profile_id, name, country, skills_json, hours_per_week,
                    budget_inr, prefer_online_only, bookmarks_json, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    DEFAULT_PROFILE_DATA["profile_id"],
                    DEFAULT_PROFILE_DATA["name"],
                    DEFAULT_PROFILE_DATA["country"],
                    json.dumps(DEFAULT_PROFILE_DATA["skills"]),
                    DEFAULT_PROFILE_DATA["hours_per_week"],
                    DEFAULT_PROFILE_DATA["budget_inr"],
                    DEFAULT_PROFILE_DATA["prefer_online_only"],
                    json.dumps(DEFAULT_PROFILE_DATA["bookmarks"]),
                    now_iso,
                ),
            )
        elif "Avanish" in (row["name"] or ""):
            conn.execute(
                "UPDATE builder_profiles SET name = ? WHERE profile_id = ?",
                (DEFAULT_PROFILE_DATA["name"], DEFAULT_PROFILE_ID),
            )

        # One-time migration: repair pre-populated M1/M2 artifact on existing sprint plans
        conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version TEXT PRIMARY KEY);")
        migrated = conn.execute("SELECT 1 FROM schema_migrations WHERE version = 'repair_m1_m2_prepop'").fetchone()
        if not migrated:
            try:
                plan_rows = conn.execute("SELECT hackathon_id, milestones_json FROM sprint_plans").fetchall()
                for pr in plan_rows:
                    ms = json.loads(pr["milestones_json"])
                    completed_ids = [m["id"] for m in ms if m.get("completed")]
                    if completed_ids == ["M1", "M2"]:
                        for m in ms:
                            m["completed"] = False
                        conn.execute(
                            "UPDATE sprint_plans SET milestones_json = ? WHERE hackathon_id = ?",
                            (json.dumps(ms), pr["hackathon_id"]),
                        )
                conn.execute("INSERT OR IGNORE INTO schema_migrations (version) VALUES ('repair_m1_m2_prepop');")
            except Exception:
                pass
    conn.close()


def sanitize_html(text: Optional[str]) -> str:
    if not text:
        return ""
    try:
        soup = BeautifulSoup(text, "html.parser")
        clean_text = soup.get_text(separator=" ")
        return re.sub(r"\s+", " ", clean_text).strip()
    except Exception:
        clean = re.sub(r"<[^>]+>", " ", text)
        return re.sub(r"\s+", " ", clean).strip()


def sanitize_prize(prize_str: Optional[str]) -> Tuple[str, float, str]:
    if not prize_str or prize_str.strip() == "":
        return ("Unknown", 0.0, "UNKNOWN")
    raw = prize_str.strip()
    clean = sanitize_html(raw)

    currency = "USD"
    if "₹" in clean or "INR" in clean.upper() or "RS" in clean.upper():
        currency = "INR"
    elif "€" in clean or "EUR" in clean.upper():
        currency = "EUR"
    elif "£" in clean or "GBP" in clean.upper():
        currency = "GBP"

    numbers = re.findall(r"[0-9]+(?:,[0-9]+)*(?:\.[0-9]+)?", clean)
    amount = 0.0
    if numbers:
        try:
            cleaned_nums = [float(n.replace(",", "")) for n in numbers]
            amount = max(cleaned_nums)
        except ValueError:
            amount = 0.0

    return (clean, amount, currency)


def sync_upstream_json(
    data: Dict[str, Any],
    is_fallback: bool = False,
    db_path: Optional[Path | str] = None,
) -> Dict[str, Any]:
    """Syncs catalog JSON into SQLite database."""
    init_db(db_path)
    conn = get_db_connection(db_path)

    items = data.get("hackathons", []) if isinstance(data, dict) else data
    if not isinstance(items, list):
        items = []

    added = 0
    updated = 0
    now_iso = datetime.now(timezone.utc).isoformat()

    with conn:
        for item in items:
            h_id = item.get("id") or item.get("fingerprint")
            if not h_id:
                continue

            title = item.get("title") or item.get("name") or "Untitled Hackathon"
            title = sanitize_html(title)
            url = item.get("url") or item.get("link") or ""
            source = item.get("source") or "Devpost"
            deadline = item.get("deadline") or item.get("reg_ends_at") or ""
            reg_starts = item.get("reg_starts_at") or ""
            reg_ends = item.get("reg_ends_at") or ""
            starts_at = item.get("starts_at") or ""
            ends_at = item.get("ends_at") or ""
            description = sanitize_html(item.get("description", ""))

            raw_tags = item.get("tags", [])
            tags_json = json.dumps(raw_tags if isinstance(raw_tags, list) else [])

            raw_prize = item.get("prize", "Unknown")
            prize_str, prize_amt, prize_curr = sanitize_prize(raw_prize)

            location = item.get("location", "Online")
            status = (item.get("status") or "open").lower()
            benefits = json.dumps(item.get("benefits", []))
            raw_str = json.dumps(item)

            existing = conn.execute(
                "SELECT id FROM hackathons WHERE id = ?", (h_id,)
            ).fetchone()

            if existing:
                conn.execute(
                    """
                    UPDATE hackathons SET
                        fingerprint = ?,
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
                        benefits = ?,
                        raw_json = ?,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        h_id,
                        title,
                        url,
                        source,
                        deadline,
                        reg_starts,
                        reg_ends,
                        starts_at,
                        ends_at,
                        description,
                        tags_json,
                        prize_str,
                        prize_amt,
                        prize_curr,
                        location,
                        status,
                        benefits,
                        raw_str,
                        now_iso,
                        h_id,
                    ),
                )
                updated += 1
            else:
                conn.execute(
                    """
                    INSERT INTO hackathons (
                        id, fingerprint, title, url, source, deadline,
                        reg_starts_at, reg_ends_at, starts_at, ends_at,
                        description, tags, prize, prize_amount, prize_currency,
                        location, status, benefits, raw_json, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        h_id,
                        h_id,
                        title,
                        url,
                        source,
                        deadline,
                        reg_starts,
                        reg_ends,
                        starts_at,
                        ends_at,
                        description,
                        tags_json,
                        prize_str,
                        prize_amt,
                        prize_curr,
                        location,
                        status,
                        benefits,
                        raw_str,
                        now_iso,
                        now_iso,
                    ),
                )
                added += 1

    total = conn.execute("SELECT COUNT(*) AS c FROM hackathons").fetchone()["c"]
    conn.close()

    return {
        "total_hackathons": total,
        "added": added,
        "updated": updated,
        "source_type": "fallback_seed" if is_fallback else "live_upstream",
        "synced_at": now_iso,
    }


def load_seed_if_empty(
    seed_path: Optional[Path | str] = None,
    db_path: Optional[Path | str] = None,
) -> bool:
    init_db(db_path)
    conn = get_db_connection(db_path)
    count = conn.execute("SELECT COUNT(*) AS c FROM hackathons").fetchone()["c"]
    conn.close()

    if count == 0:
        spath = Path(seed_path or DEFAULT_SEED_PATH)
        if spath.exists():
            with open(spath, "r", encoding="utf-8") as f:
                data = json.load(f)
            sync_upstream_json(data, is_fallback=True, db_path=db_path)
            return True
    return False


def get_builder_profile(
    profile_id: str = DEFAULT_PROFILE_ID,
    db_path: Optional[Path | str] = None,
) -> Dict[str, Any]:
    init_db(db_path)
    conn = get_db_connection(db_path)
    row = conn.execute(
        "SELECT * FROM builder_profiles WHERE profile_id = ?",
        (profile_id,),
    ).fetchone()
    conn.close()

    if not row:
        return {
            "profile_id": DEFAULT_PROFILE_ID,
            "name": DEFAULT_PROFILE_DATA["name"],
            "country": DEFAULT_PROFILE_DATA["country"],
            "skills": DEFAULT_PROFILE_DATA["skills"],
            "hours_per_week": DEFAULT_PROFILE_DATA["hours_per_week"],
            "budget_inr": DEFAULT_PROFILE_DATA["budget_inr"],
            "prefer_online_only": True,
            "bookmarks": DEFAULT_PROFILE_DATA["bookmarks"],
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

    return {
        "profile_id": row["profile_id"],
        "name": row["name"],
        "country": row["country"],
        "skills": json.loads(row["skills_json"]) if row["skills_json"] else [],
        "hours_per_week": row["hours_per_week"],
        "budget_inr": row["budget_inr"],
        "prefer_online_only": bool(row["prefer_online_only"]),
        "bookmarks": json.loads(row["bookmarks_json"]) if row["bookmarks_json"] else [],
        "updated_at": row["updated_at"],
    }


def update_builder_profile(
    profile_id: str = DEFAULT_PROFILE_ID,
    name: Optional[str] = None,
    country: Optional[str] = None,
    skills: Optional[List[str]] = None,
    hours_per_week: Optional[int] = None,
    budget_inr: Optional[float] = None,
    prefer_online_only: Optional[bool] = None,
    toggle_bookmark_id: Optional[str] = None,
    db_path: Optional[Path | str] = None,
) -> Dict[str, Any]:
    init_db(db_path)
    current = get_builder_profile(profile_id, db_path=db_path)

    new_name = name if name is not None else current["name"]
    new_country = country if country is not None else current["country"]
    new_skills = skills if skills is not None else current["skills"]
    new_hours = hours_per_week if hours_per_week is not None else current["hours_per_week"]
    new_budget = budget_inr if budget_inr is not None else current["budget_inr"]
    new_online = prefer_online_only if prefer_online_only is not None else current["prefer_online_only"]
    new_bookmarks = list(current["bookmarks"])

    if toggle_bookmark_id:
        if toggle_bookmark_id in new_bookmarks:
            new_bookmarks.remove(toggle_bookmark_id)
        else:
            new_bookmarks.append(toggle_bookmark_id)

    now_iso = datetime.now(timezone.utc).isoformat()
    conn = get_db_connection(db_path)
    with conn:
        conn.execute(
            """
            INSERT INTO builder_profiles (
                profile_id, name, country, skills_json, hours_per_week,
                budget_inr, prefer_online_only, bookmarks_json, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(profile_id) DO UPDATE SET
                name = excluded.name,
                country = excluded.country,
                skills_json = excluded.skills_json,
                hours_per_week = excluded.hours_per_week,
                budget_inr = excluded.budget_inr,
                prefer_online_only = excluded.prefer_online_only,
                bookmarks_json = excluded.bookmarks_json,
                updated_at = excluded.updated_at
            """,
            (
                profile_id,
                new_name,
                new_country,
                json.dumps(new_skills),
                new_hours,
                new_budget,
                1 if new_online else 0,
                json.dumps(new_bookmarks),
                now_iso,
            ),
        )
    conn.close()

    return get_builder_profile(profile_id, db_path=db_path)


def score_hackathon_for_profile(
    hackathon: Dict[str, Any],
    profile: Dict[str, Any],
) -> Dict[str, Any]:
    """Computes deterministic feasibility score (0-100) and fit explanations."""
    fit_reasons: List[str] = []
    risk_flags: List[str] = []

    # 1. Budget and Location Fit (30 pts)
    location = (hackathon.get("location") or "Online").strip()
    budget_inr = profile.get("budget_inr", 0.0)
    prefer_online = profile.get("prefer_online_only", True)

    if location.lower() == "online" or not prefer_online:
        loc_score = 30
        fit_reasons.append("100% Online — zero travel or accommodation expenses required")
    elif "hybrid" in location.lower():
        loc_score = 20
        fit_reasons.append("Hybrid format — virtual participation option available")
    else:  # In-Person with ₹0 budget
        loc_score = 10
        risk_flags.append("In-Person travel required — conflicts with ₹0 budget")

    # 2. Stack and Domain Fit (35 pts)
    profile_skills = [s.lower() for s in profile.get("skills", [])]
    haystack = f"{hackathon.get('title', '')} {hackathon.get('description', '')} {' '.join(hackathon.get('tags', []))}".lower()

    matched_skills = []
    for skill in profile_skills:
        if re.search(r"\b" + re.escape(skill) + r"\b", haystack):
            matched_skills.append(skill.title())

    # Check for domain keywords (AI, Agent, Cloud, Voice, Web)
    ai_keywords = ["ai", "agent", "cloud", "mcp", "voice", "alexa", "llm", "automation"]
    matched_domains = [kw.upper() for kw in ai_keywords if re.search(r"\b" + re.escape(kw) + r"\b", haystack)]

    match_count = len(matched_skills) + len(matched_domains)
    if match_count >= 3:
        stack_score = 35
        all_hits = list(dict.fromkeys(matched_skills + matched_domains))
        fit_reasons.append(f"Strong stack match: {', '.join(all_hits[:4])}")
    elif match_count == 2:
        stack_score = 28
        all_hits = list(dict.fromkeys(matched_skills + matched_domains))
        fit_reasons.append(f"Moderate stack match: {', '.join(all_hits)}")
    elif match_count == 1:
        stack_score = 22
        fit_reasons.append(f"Partial match: {matched_skills[0] if matched_skills else matched_domains[0]}")
    else:
        stack_score = 15
        risk_flags.append("General theme — requires tailored application architecture")

    # 3. Timeline Fit (20 pts)
    deadline_str = hackathon.get("deadline", "")
    if not deadline_str or deadline_str == "Unknown":
        time_score = 10
        risk_flags.append("Unknown deadline — requires rule audit")
    else:
        try:
            dl = datetime.fromisoformat(deadline_str.replace("Z", "+00:00"))
            now = datetime.now(timezone.utc)
            delta_days = (dl - now).total_seconds() / 86400.0

            if delta_days >= 14:
                time_score = 20
                fit_reasons.append(f"Ample runway: ~{int(delta_days)} days remaining for 15h sprint")
            elif delta_days >= 5:
                time_score = 16
                fit_reasons.append(f"Comfortable runway: ~{int(delta_days)} days remaining")
            elif delta_days >= 1:
                time_score = 8
                risk_flags.append(f"Tight deadline: {int(delta_days)} days remaining")
            else:
                time_score = 4
                risk_flags.append("Submission window closing in < 24h or concluded")
        except Exception:
            time_score = 12

    # 4. Prize and Impact Signal (15 pts)
    prize_amt = hackathon.get("prize_amount", 0.0)
    if prize_amt >= 50000:
        prize_score = 15
        fit_reasons.append(f"Substantial prize pool: {hackathon.get('prize')}")
    elif prize_amt > 0:
        prize_score = 12
        fit_reasons.append(f"Verified cash prize: {hackathon.get('prize')}")
    else:
        prize_score = 8

    total_score = max(0, min(100, int(round(loc_score + stack_score + time_score + prize_score))))
    is_bookmarked = hackathon.get("id") in profile.get("bookmarks", [])

    return {
        "feasibility_score": total_score,
        "score_breakdown": {
            "budget_and_location_fit": loc_score,
            "stack_and_domain_fit": stack_score,
            "timeline_fit": time_score,
            "prize_and_impact_signal": prize_score,
        },
        "fit_reasons": fit_reasons,
        "risk_flags": risk_flags,
        "is_bookmarked": is_bookmarked,
    }


def query_hackathons(
    query: Optional[str] = None,
    source: Optional[str] = None,
    status: Optional[str] = None,
    location: Optional[str] = None,
    tag: Optional[str] = None,
    sort_by_feasibility: bool = False,
    limit: int = 20,
    db_path: Optional[Path | str] = None,
) -> List[Dict[str, Any]]:
    conn = get_db_connection(db_path)
    sql = ["SELECT * FROM hackathons WHERE 1=1"]
    params: List[Any] = []

    if query and query.strip():
        q_raw = query.strip()
        q_lower = q_raw.lower()

        # Detect structured intent flags inside natural language queries
        if not location:
            if "online" in q_lower:
                location = "Online"
            elif "in-person" in q_lower or "in person" in q_lower:
                location = "In-Person"

        if any(w in q_lower for w in ["zero", "budget", "free", "rupee", "feasible", "rank", "best"]):
            sort_by_feasibility = True

        # Extract individual alphanumeric tokens (treating hyphens/punctuation as word separators)
        q_cleaned = re.sub(r"[^a-zA-Z0-9\+\#]+", " ", q_lower)
        tokens = [t for t in q_cleaned.split() if len(t) > 1]
        meaningful_tokens = [t for t in tokens if t not in CONVERSATIONAL_STOP_WORDS]

        if meaningful_tokens:
            for token in meaningful_tokens:
                t_param = f"%{token}%"
                sql.append("AND (LOWER(title) LIKE ? OR LOWER(description) LIKE ? OR LOWER(tags) LIKE ? OR LOWER(source) LIKE ?)")
                params.extend([t_param, t_param, t_param, t_param])
        # If no meaningful domain tokens remain (e.g. "Find the best online hackathons for a zero-rupee budget"),
        # we deliberately avoid adding an impossible literal sentence match, allowing location and feasibility
        # flags to cleanly filter and rank catalog items.

    if source:
        sql.append("AND LOWER(source) = LOWER(?)")
        params.append(source.strip())

    if status:
        sql.append("AND LOWER(status) = LOWER(?)")
        params.append(status.strip())

    if location:
        sql.append("AND LOWER(location) LIKE LOWER(?)")
        params.append(f"%{location.strip()}%")

    if tag:
        sql.append("AND LOWER(tags) LIKE LOWER(?)")
        params.append(f"%{tag.strip()}%")

    sql.append("ORDER BY CASE WHEN deadline != '' THEN deadline ELSE '9999-99-99' END ASC LIMIT ?")
    params.append(max(1, min(limit, 100)))

    rows = conn.execute(" ".join(sql), params).fetchall()
    profile = get_builder_profile(db_path=db_path)

    results = []
    for r in rows:
        item = {
            "id": r["id"],
            "title": r["title"],
            "url": r["url"],
            "source": r["source"],
            "deadline": r["deadline"],
            "description": r["description"],
            "tags": json.loads(r["tags"]) if r["tags"] else [],
            "prize": r["prize"],
            "prize_amount": r["prize_amount"],
            "prize_currency": r["prize_currency"],
            "location": r["location"],
            "status": r["status"],
            "starts_at": r["starts_at"],
            "ends_at": r["ends_at"],
        }
        scored = score_hackathon_for_profile(item, profile)
        item.update(scored)
        results.append(item)

    conn.close()

    if sort_by_feasibility:
        results.sort(key=lambda x: x.get("feasibility_score", 0), reverse=True)

    return results


def get_hackathon(
    identifier: str,
    db_path: Optional[Path | str] = None,
) -> Optional[Dict[str, Any]]:
    conn = get_db_connection(db_path)
    clean_id = identifier.strip()

    row = conn.execute(
        "SELECT * FROM hackathons WHERE id = ? OR fingerprint = ?",
        (clean_id, clean_id),
    ).fetchone()

    if not row:
        row = conn.execute(
            "SELECT * FROM hackathons WHERE LOWER(url) = LOWER(?) OR LOWER(title) = LOWER(?)",
            (clean_id, clean_id),
        ).fetchone()

    if not row:
        row = conn.execute(
            "SELECT * FROM hackathons WHERE LOWER(title) LIKE LOWER(?) LIMIT 1",
            (f"%{clean_id}%",),
        ).fetchone()

    if not row:
        conn.close()
        return None

    item = {
        "id": row["id"],
        "fingerprint": row["fingerprint"],
        "title": row["title"],
        "url": row["url"],
        "source": row["source"],
        "deadline": row["deadline"],
        "reg_starts_at": row["reg_starts_at"],
        "reg_ends_at": row["reg_ends_at"],
        "starts_at": row["starts_at"],
        "ends_at": row["ends_at"],
        "description": row["description"],
        "tags": json.loads(row["tags"]) if row["tags"] else [],
        "prize": row["prize"],
        "prize_amount": row["prize_amount"],
        "prize_currency": row["prize_currency"],
        "location": row["location"],
        "status": row["status"],
        "benefits": json.loads(row["benefits"]) if row["benefits"] else [],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }
    conn.close()

    profile = get_builder_profile(db_path=db_path)
    item.update(score_hackathon_for_profile(item, profile))
    return item


def _calculate_milestone_hours(total_hours: int) -> Tuple[int, int, int, int, int]:
    h1 = max(1, round(total_hours * 0.15))
    h2 = max(2, round(total_hours * 0.30))
    h3 = max(2, round(total_hours * 0.25))
    h4 = max(1, round(total_hours * 0.15))
    h5 = total_hours - (h1 + h2 + h3 + h4)
    if h5 < 1:
        h5 = 1
        h2 = total_hours - (h1 + h3 + h4 + h5)
    return h1, h2, h3, h4, h5


def _build_tailored_sprint_plan(
    hackathon_id: str,
    hackathon: Optional[Dict[str, Any]],
    available_hours: int,
) -> Tuple[str, str, List[Dict[str, Any]]]:
    h1, h2, h3, h4, h5 = _calculate_milestone_hours(available_hours)
    go_hour = max(1, round(available_hours * 0.40))

    # Amazon Developer Hackathon Specialization
    if hackathon_id == "0e18b6cf877bc442f9b92302350398fce6b84b05d01ac2f92ff5367616b259a8":
        go_no_go = f"Hour {go_hour}: Verify upstream sync, persistent SQLite state, and at least 3 MCP tools working in JSON-RPC 2.0 handshake before building UI."
        reuse_note = "Compliant with hackathon reuse policies: 'devpost-hackathon-bot' is an external, read-only upstream feed (₹0 budget GitHub Pages catalog). All MCP server logic, rule-diff engine, SQLite schemas, and Echo Show 15 multimodal interface are 100% newly created in 'hackpulse-alexa-mcp'."
        milestones = [
            {
                "id": "M1",
                "title": "Scope & Upstream Data Bridge",
                "hours": h1,
                "completed": False,
                "description": "Establish read-only bridge to devpost-hackathon-bot data seed and isolate project boundaries.",
                "deliverables": "upstream_seed.json fallback & offline verification logic",
            },
            {
                "id": "M2",
                "title": "Core Backend / MCP Protocol & Provenance",
                "hours": h2,
                "completed": False,
                "description": "Implement MCP 2025-11-25 streamable HTTP protocol, 7 core tools, SQLite persistence, and provenance tracker.",
                "deliverables": "app.py, core/database.py, core/mcp_server.py, core/rule_auditor.py",
            },
            {
                "id": "M3",
                "title": "Centre-Stage Multimodal UI",
                "hours": h3,
                "completed": False,
                "description": "Build Echo Show 15 / Alexa+ ambient web canvas with live MCP JSON-RPC 2.0 tool execution and wire inspector.",
                "deliverables": "static/index.html, static/styles.css, static/app.js mounted on /",
            },
            {
                "id": "M4",
                "title": "Zero-Cost Cloud Hosting & Verification",
                "hours": h4,
                "completed": False,
                "description": "Deploy ASGI backend to free-tier cloud host (Hugging Face / Render) and verify persistent endpoint uptime.",
                "deliverables": "Dockerfile with least-privilege UID 1000 permissions & health check monitor",
            },
            {
                "id": "M5",
                "title": "2.5-Min Demo Video & Submission",
                "hours": h5,
                "completed": False,
                "description": "Record walkthrough highlighting MCP compliance, verified rule provenance, and Alexa+ multimodal interaction.",
                "deliverables": "Public GitHub repo with MIT License, demo video link, Devpost submission",
            },
        ]
        return go_no_go, reuse_note, milestones

    title = hackathon["title"] if hackathon else f"Hackathon ({hackathon_id[:12]})"
    source = hackathon.get("source", "Devpost") if hackathon else "Devpost"
    tags = [t.lower() for t in hackathon.get("tags", [])] if hackathon else []
    haystack = f"{title} {' '.join(tags)} {hackathon.get('description', '')}".lower() if hackathon else ""

    go_no_go = f"Hour {go_hour}: Validate core prototype architecture, API connectivity, and minimum viable workflow for '{title}' before building UI."
    reuse_note = f"Original Work & Code Reuse Declaration: Pre-existing templates and open-source libraries must be documented in README per {source} official rules. Core application logic and deliverables for '{title}' must be authored during the hackathon sprint."

    def _has_domain_kw(keywords: List[str], text: str) -> bool:
        for kw in keywords:
            if re.search(r"\b" + re.escape(kw) + r"\b", text):
                return True
        return False

    if _has_domain_kw(["vr", "ar", "xr", "meta", "quest", "spatial", "3d", "webxr", "visionpro"], haystack):
        milestones = [
            {"id": "M1", "title": "Concept, WebXR/SDK Setup & Asset Scope", "hours": h1, "completed": False,
             "description": f"Configure spatial developer SDK, establish 3D viewport canvas, and outline target interaction loop for {title}.",
             "deliverables": "Project scaffolding, spatial environment baseline & asset inventory"},
            {"id": "M2", "title": "Core Interactive Mechanics & Spatial Logic", "hours": h2, "completed": False,
             "description": "Implement primary controller inputs, object interactions, spatial sound, and core real-time state engine.",
             "deliverables": "Interactive mechanics prototype and event listeners"},
            {"id": "M3", "title": "Scene Composition & Performance Optimization", "hours": h3, "completed": False,
             "description": "Assemble 3D environment, optimize rendering framerate for headset display constraints, and build spatial UI.",
             "deliverables": "Polished 3D scene, responsive UI overlay & framerate profiling"},
            {"id": "M4", "title": "Device Packaging & Hardware Verification", "hours": h4, "completed": False,
             "description": "Package build target, test on simulated hardware/browser, and verify comfort/ergonomic guidelines.",
             "deliverables": "Deployable package / hosted WebXR build with health validation"},
            {"id": "M5", "title": f"{source} Showcase Video & Submission", "hours": h5, "completed": False,
             "description": f"Record in-headset demonstration video showcasing interactive features for {title} and complete {source} submission.",
             "deliverables": f"Public repository with open-source license, demo video link & {source} entry"},
        ]
    elif _has_domain_kw(["web3", "blockchain", "crypto", "defi", "trading", "fintech", "token", "smart contract", "solana", "ethereum"], haystack):
        milestones = [
            {"id": "M1", "title": "Protocol Architecture & API Integration", "hours": h1, "completed": False,
             "description": f"Define financial/transaction workflows, configure testnet/sandbox API credentials, and review compliance constraints for {title}.",
             "deliverables": "API client scaffolding, sandbox connection & data contracts"},
            {"id": "M2", "title": "Core Transaction & Automated Logic Engine", "hours": h2, "completed": False,
             "description": "Implement algorithmic decision rules, secure transaction execution, state persistence, and error boundaries.",
             "deliverables": "Backend execution engine, automated tests & state store"},
            {"id": "M3", "title": "Dashboard Interface & Transaction Visualization", "hours": h3, "completed": False,
             "description": "Develop responsive user dashboard showing live balances, activity feeds, risk metrics, and transaction status.",
             "deliverables": "Frontend dashboard, charts & wallet/account status indicators"},
            {"id": "M4", "title": "Security Audit & Cloud Deployment", "hours": h4, "completed": False,
             "description": "Audit cryptographic signing/API key protection, deploy to zero-cost cloud host, and run stress tests.",
             "deliverables": "Hardened deployment manifest, environment configs & sanity check"},
            {"id": "M5", "title": f"Demo Walkthrough & {source} Submission", "hours": h5, "completed": False,
             "description": f"Record demonstration showing end-to-end transaction flow for {title} and complete {source} submission requirements.",
             "deliverables": f"Public repository with open-source license, demo video & {source} documentation"},
        ]
    elif _has_domain_kw(["agent", "agentic", "ai", "llm", "intelligence", "automation", "nlp", "machine learning"], haystack):
        milestones = [
            {"id": "M1", "title": "Agent Architecture & Model Scaffolding", "hours": h1, "completed": False,
             "description": f"Define agent tool definitions, system prompts, API bridges, and structured schemas tailored for {title}.",
             "deliverables": "Model integration harness, prompt templates & schema definitions"},
            {"id": "M2", "title": "Agent Tool Execution & Decision Engine", "hours": h2, "completed": False,
             "description": "Implement tool calling loops, stateful session memory, guardrails, and deterministic fallbacks.",
             "deliverables": "Working agent core with unit tests & tool registry"},
            {"id": "M3", "title": "Multimodal Interface & Agent Inspector", "hours": h3, "completed": False,
             "description": "Build interactive visual interface displaying live agent reasoning, tool payloads, and user controls.",
             "deliverables": "Interactive UI, real-time agent status indicators & message stream"},
            {"id": "M4", "title": "Evaluation, Cloud Hosting & Latency Optimization", "hours": h4, "completed": False,
             "description": "Benchmark response latency, deploy ASGI backend to zero-cost cloud hosting, and verify endpoint stability.",
             "deliverables": "Containerized deployment, keep-alive monitor & latency benchmarks"},
            {"id": "M5", "title": f"Demo Video Walkthrough & {source} Submission", "hours": h5, "completed": False,
             "description": f"Record 2.5-min video demonstrating autonomous tool calls and problem solving for {title}.",
             "deliverables": f"Public GitHub repository with open-source license, video link & {source} entry"},
        ]
    else:
        milestones = [
            {"id": "M1", "title": "Scope, System Architecture & Wireframes", "hours": h1, "completed": False,
             "description": f"Scope MVP deliverables for {title}, set up repository structure, and define component boundaries.",
             "deliverables": "Repository scaffolding, data models & wireframe architecture"},
            {"id": "M2", "title": "Core Backend Logic & Data Persistence", "hours": h2, "completed": False,
             "description": "Implement business logic, REST/RPC endpoints, persistent database schema, and test fixtures.",
             "deliverables": "Tested backend endpoints, schema migrations & CRUD handlers"},
            {"id": "M3", "title": "Interactive Frontend & User Experience", "hours": h3, "completed": False,
             "description": "Develop responsive client interface, state management, form validations, and user feedback cues.",
             "deliverables": "Frontend views, styled design system & client-side routing"},
            {"id": "M4", "title": "Cloud Packaging, CI/CD & Production Build", "hours": h4, "completed": False,
             "description": "Containerize application, configure environment secrets, deploy to cloud hosting, and verify endpoint health.",
             "deliverables": "Container manifest, cloud deployment URL & smoke tests"},
            {"id": "M5", "title": f"Walkthrough Video & {source} Final Submission", "hours": h5, "completed": False,
             "description": f"Record demo video highlighting features solving {title}'s challenge and complete {source} submission.",
             "deliverables": f"Public repository with open-source license, demo recording & submission link"},
        ]

    return go_no_go, reuse_note, milestones


def generate_or_update_sprint_plan(
    hackathon_id: str,
    available_hours: int = 15,
    completed_milestone_id: Optional[str] = None,
    db_path: Optional[Path | str] = None,
) -> Dict[str, Any]:
    """Generates or updates a persistent, time-boxed sprint plan calibrated to builder hours."""
    init_db(db_path)
    hackathon = get_hackathon(hackathon_id, db_path=db_path)
    canonical_id = hackathon["id"] if hackathon else hackathon_id
    title = hackathon["title"] if hackathon else f"Hackathon ({canonical_id[:12]})"

    conn = get_db_connection(db_path)
    row = conn.execute(
        "SELECT * FROM sprint_plans WHERE hackathon_id = ?",
        (canonical_id,),
    ).fetchone()

    # Fallback check if row was stored under original raw identifier
    if not row and canonical_id != hackathon_id:
        row = conn.execute(
            "SELECT * FROM sprint_plans WHERE hackathon_id = ?",
            (hackathon_id,),
        ).fetchone()

    now_iso = datetime.now(timezone.utc).isoformat()

    # Determine if existing cached plan needs regeneration (e.g. old meta-template on non-Amazon hackathon)
    needs_regen = False
    if row:
        cached_reuse = row["reuse_compliance_note"] or ""
        cached_milestones_str = row["milestones_json"] or ""
        is_amazon = canonical_id == "0e18b6cf877bc442f9b92302350398fce6b84b05d01ac2f92ff5367616b259a8"
        if not is_amazon and (
            "devpost-hackathon-bot" in cached_reuse
            or "Scope & Upstream Data Bridge" in cached_milestones_str
            or ("Concept, WebXR" in cached_milestones_str and not any(k in (hackathon.get("title", "") + " " + " ".join(hackathon.get("tags", []))).lower() for k in ["vr", "ar", "xr", "meta", "quest"]))
            or "reuse" not in cached_reuse.lower()
        ):
            needs_regen = True
        elif row["hours_per_week"] != available_hours and not completed_milestone_id:
            needs_regen = True

    if row and not needs_regen:
        milestones = json.loads(row["milestones_json"])
        if completed_milestone_id:
            for m in milestones:
                if m.get("id") == completed_milestone_id:
                    m["completed"] = not m.get("completed", False)
                    break

        completed_count = sum(1 for m in milestones if m.get("completed"))
        total_milestones = len(milestones)
        progress_pct = int(round((completed_count / total_milestones) * 100)) if total_milestones else 0
        rem_hours = sum(m.get("hours", 0) for m in milestones if not m.get("completed"))

        with conn:
            conn.execute(
                """
                UPDATE sprint_plans SET
                    hackathon_id = ?,
                    title = ?,
                    hours_per_week = ?,
                    total_hours = ?,
                    milestones_json = ?,
                    updated_at = ?
                WHERE hackathon_id = ? OR hackathon_id = ?
                """,
                (canonical_id, title, available_hours, available_hours, json.dumps(milestones), now_iso, canonical_id, hackathon_id),
            )
        conn.close()

        return {
            "hackathon_id": canonical_id,
            "title": title,
            "hours_per_week": available_hours,
            "total_hours": available_hours,
            "remaining_hours": rem_hours,
            "completed_milestones": completed_count,
            "total_milestones": total_milestones,
            "progress_percent": progress_pct,
            "go_no_go_checkpoint": row["go_no_go_checkpoint"],
            "reuse_compliance_note": row["reuse_compliance_note"],
            "milestones": milestones,
            "updated_at": now_iso,
        }

    # Generate tailored milestones and checkpoint
    go_no_go, reuse_note, tailored_milestones = _build_tailored_sprint_plan(canonical_id, hackathon, available_hours)

    # If row existed and is regenerating (e.g. available_hours changed), preserve saved completions
    if row and not completed_milestone_id:
        try:
            old_ms = json.loads(row["milestones_json"])
            old_completed_ids = {m["id"] for m in old_ms if m.get("completed")}
            # Discard erroneous legacy default M1/M2 artifact on regeneration
            if old_completed_ids == {"M1", "M2"}:
                old_completed_ids = set()
            for m in tailored_milestones:
                if m["id"] in old_completed_ids:
                    m["completed"] = True
        except Exception:
            pass

    if completed_milestone_id:
        for m in tailored_milestones:
            if m.get("id") == completed_milestone_id:
                m["completed"] = not m.get("completed", False)
                break

    completed_count = sum(1 for m in tailored_milestones if m.get("completed"))
    rem_hours = sum(m.get("hours", 0) for m in tailored_milestones if not m.get("completed"))
    progress_pct = int(round((completed_count / len(tailored_milestones)) * 100))

    with conn:
        conn.execute(
            """
            INSERT INTO sprint_plans (
                hackathon_id, title, hours_per_week, total_hours,
                go_no_go_checkpoint, reuse_compliance_note, milestones_json, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(hackathon_id) DO UPDATE SET
                title = excluded.title,
                hours_per_week = excluded.hours_per_week,
                total_hours = excluded.total_hours,
                go_no_go_checkpoint = excluded.go_no_go_checkpoint,
                reuse_compliance_note = excluded.reuse_compliance_note,
                milestones_json = excluded.milestones_json,
                updated_at = excluded.updated_at
            """,
            (
                canonical_id,
                title,
                available_hours,
                available_hours,
                go_no_go,
                reuse_note,
                json.dumps(tailored_milestones),
                now_iso,
            ),
        )
    conn.close()

    return {
        "hackathon_id": canonical_id,
        "title": title,
        "hours_per_week": available_hours,
        "total_hours": available_hours,
        "remaining_hours": rem_hours,
        "completed_milestones": completed_count,
        "total_milestones": len(tailored_milestones),
        "progress_percent": progress_pct,
        "go_no_go_checkpoint": go_no_go,
        "reuse_compliance_note": reuse_note,
        "milestones": tailored_milestones,
        "updated_at": now_iso,
    }


def record_session(
    session_id: str,
    context_data: Optional[Dict[str, Any]] = None,
    db_path: Optional[Path | str] = None,
) -> None:
    conn = get_db_connection(db_path)
    now_iso = datetime.now(timezone.utc).isoformat()
    ctx_str = json.dumps(context_data or {})

    with conn:
        conn.execute(
            """
            INSERT INTO mcp_sessions (session_id, created_at, last_seen_at, context_data)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET
                last_seen_at = excluded.last_seen_at,
                context_data = CASE WHEN excluded.context_data != '{}' THEN excluded.context_data ELSE mcp_sessions.context_data END
            """,
            (session_id, now_iso, now_iso, ctx_str),
        )
    conn.close()


def save_rule_audit(
    hackathon_id: str,
    source_url: str,
    verification_status: str,
    raw_rules_text: str,
    eligibility: str,
    team_size: str,
    submission_requirements: str,
    hosting_requirements: str,
    content_hash: str,
    notes: str = "",
    db_path: Optional[Path | str] = None,
) -> int:
    conn = get_db_connection(db_path)
    now_iso = datetime.now(timezone.utc).isoformat()

    with conn:
        cursor = conn.execute(
            """
            INSERT INTO rule_audits (
                hackathon_id, source_url, retrieval_timestamp, verification_status,
                raw_rules_text, eligibility, team_size, submission_requirements,
                hosting_requirements, content_hash, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                hackathon_id,
                source_url,
                now_iso,
                verification_status,
                raw_rules_text,
                eligibility,
                team_size,
                submission_requirements,
                hosting_requirements,
                content_hash,
                notes,
            ),
        )
        audit_id = cursor.lastrowid
    conn.close()
    return audit_id


def get_rule_audits(
    hackathon_id: str,
    limit: int = 10,
    db_path: Optional[Path | str] = None,
) -> List[Dict[str, Any]]:
    conn = get_db_connection(db_path)
    rows = conn.execute(
        """
        SELECT * FROM rule_audits
        WHERE hackathon_id = ?
        ORDER BY id DESC LIMIT ?
        """,
        (hackathon_id, limit),
    ).fetchall()

    audits = [dict(r) for r in rows]
    conn.close()
    return audits
