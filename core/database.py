"""Database layer for HackPulse Alexa MCP.
Handles SQLite persistence, schema initialization, upstream JSON sync,
HTML/prize sanitization, and session state tracking.
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
        """)
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

    # Extract digits and decimal points
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


def query_hackathons(
    query: Optional[str] = None,
    source: Optional[str] = None,
    status: Optional[str] = None,
    location: Optional[str] = None,
    tag: Optional[str] = None,
    limit: int = 20,
    db_path: Optional[Path | str] = None,
) -> List[Dict[str, Any]]:
    conn = get_db_connection(db_path)
    sql = ["SELECT * FROM hackathons WHERE 1=1"]
    params: List[Any] = []

    if query:
        q_term = f"%{query.strip()}%"
        sql.append("AND (title LIKE ? OR description LIKE ? OR tags LIKE ?)")
        params.extend([q_term, q_term, q_term])

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
    results = []
    for r in rows:
        results.append({
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
        })
    conn.close()
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

    result = {
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
    return result


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
