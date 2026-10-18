"""Rule inspection, provenance tracking, and diff engine for HackPulse Alexa MCP.
Strictly separates live verified results from deterministic fallback data,
recording retrieval timestamps, source URLs, and rule categories.
"""

import difflib
import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
import httpx
from bs4 import BeautifulSoup

from core.database import get_hackathon, get_rule_audits, save_rule_audit


class RuleAuditor:
    def __init__(self, db_path: Optional[Path | str] = None):
        self.db_path = db_path

    @staticmethod
    def _extract_rules_sections(soup: BeautifulSoup) -> str:
        """Finds likely rules / guidelines / FAQ elements in HTML."""
        rules_texts: List[str] = []

        # Common rule selectors across Devpost, Devfolio, Unstop
        selectors = [
            "#rules",
            ".rules",
            "#faq",
            ".faq",
            "#guidelines",
            ".guidelines",
            "#eligibility",
            ".eligibility",
            "section[data-tab='rules']",
            "section[data-tab='faq']",
        ]

        for sel in selectors:
            elements = soup.select(sel)
            for el in elements:
                t = el.get_text(separator="\n").strip()
                if len(t) > 30 and t not in rules_texts:
                    rules_texts.append(t)

        if not rules_texts:
            # Fallback: search headings containing 'rule', 'guideline', 'faq'
            for h in soup.find_all(["h2", "h3", "h4"]):
                heading_text = h.get_text().strip().lower()
                if any(w in heading_text for w in ["rule", "guideline", "eligibility", "submission", "faq", "criteria"]):
                    # Gather sibling paragraphs
                    content = []
                    curr = h.find_next_sibling()
                    count = 0
                    while curr and count < 6:
                        if curr.name in ["p", "ul", "ol", "div"]:
                            content.append(curr.get_text(separator=" ").strip())
                        elif curr.name in ["h2", "h3"]:
                            break
                        curr = curr.find_next_sibling()
                        count += 1
                    if content:
                        rules_texts.append(h.get_text() + "\n" + "\n".join(content))

        # If still nothing, extract main text
        if not rules_texts:
            main = soup.find("main") or soup.find("article") or soup.find("body")
            if main:
                rules_texts.append(main.get_text(separator="\n")[:4000])

        combined = "\n\n---\n\n".join(rules_texts)
        return re.sub(r"\n{3,}", "\n\n", combined).strip()

    @staticmethod
    def _parse_rule_categories(rules_text: str, default_desc: str = "") -> Dict[str, str]:
        """Categorizes rules into eligibility, team size, submission, and hosting requirements."""
        full_text = f"{rules_text}\n{default_desc}"

        # 1. Eligibility
        elig_match = re.search(
            r"(?:eligib(?:le|ility)|who can participate|open to)[^\.\n]+(?:[\.\n][^\.\n]+){1,3}",
            full_text,
            re.IGNORECASE,
        )
        eligibility = elig_match.group(0).strip() if elig_match else "Open to developers globally (verify specific age/location terms on hackathon site)."

        # 2. Team Size
        team_match = re.search(
            r"(?:team size|team of|individual or team|members per team)[^\.\n]+(?:[\.\n][^\.\n]+){0,2}",
            full_text,
            re.IGNORECASE,
        )
        team_size = team_match.group(0).strip() if team_match else "Teams of 1-4 members permitted unless otherwise specified."

        # 3. Submission Requirements
        sub_match = re.search(
            r"(?:submission requirements?|what to submit|must submit|deliverables?)[^\.\n]+(?:[\.\n][^\.\n]+){1,3}",
            full_text,
            re.IGNORECASE,
        )
        submission = sub_match.group(0).strip() if sub_match else "Public source repository (e.g., GitHub), demonstration video (2-3 mins), and architecture description required."

        # 4. Hosting & Deployment Requirements (Critical Guardrail)
        hosting_match = re.search(
            r"(?:host(?:ed|ing)|deploy(?:ed|ment)|cloud|aws|serverless|live demo|endpoint|alexa skill)[^\.\n]+(?:[\.\n][^\.\n]+){1,3}",
            full_text,
            re.IGNORECASE,
        )
        if hosting_match:
            hosting = hosting_match.group(0).strip()
        else:
            hosting = "Note: No mandatory post-submission persistent hosting requirement detected. Ensure demo URL or test credentials remain accessible during judging period."

        return {
            "eligibility": eligibility,
            "team_size": team_size,
            "submission_requirements": submission,
            "hosting_requirements": hosting,
        }

    async def audit_rules(
        self,
        identifier: str,
        force_live: bool = False,
    ) -> Dict[str, Any]:
        """Audits hackathon rules with verifiable provenance.
        Attempts live fetch if URL is present. If offline or failed, falls back
        transparently to curated/fallback metadata with unverified status.
        """
        hackathon = get_hackathon(identifier, db_path=self.db_path)
        if not hackathon:
            return {
                "error": f"Hackathon not found matching '{identifier}'",
                "identifier": identifier,
                "verification_status": "not_found",
            }

        h_id = hackathon["id"]
        source_url = hackathon["url"]
        now_iso = datetime.now(timezone.utc).isoformat()

        live_text = ""
        verification_status = "fallback_unverified"
        notes = "Curated offline fallback rules extracted from catalog metadata. Live fetch not verified."

        if source_url and source_url.startswith("http"):
            try:
                headers = {
                    "User-Agent": "HackPulse-Alexa-MCP/1.0 (RuleAuditor/ProvenanceEngine)"
                }
                async with httpx.AsyncClient(timeout=6.0, follow_redirects=True) as client:
                    resp = await client.get(source_url, headers=headers)
                    if resp.status_code == 200 and resp.text:
                        soup = BeautifulSoup(resp.text, "html.parser")
                        extracted = self._extract_rules_sections(soup)
                        if len(extracted) > 60:
                            live_text = extracted
                            verification_status = "verified_live"
                            notes = f"Live inspection verified via HTTP 200 from {source_url}."
            except Exception as e:
                notes = f"Live HTTP fetch failed ({type(e).__name__}); falling back to catalog metadata."

        final_rules_text = live_text if live_text else (
            f"Catalog Description & Baseline:\n{hackathon['description']}\n\n"
            f"Tags: {', '.join(hackathon.get('tags', []))}\n"
            f"Prize: {hackathon.get('prize', 'Unknown')}\n"
            f"Deadline: {hackathon.get('deadline', 'N/A')}\n"
            f"Location: {hackathon.get('location', 'Online')}"
        )

        categories = self._parse_rule_categories(final_rules_text, hackathon.get("description", ""))
        content_hash = hashlib.sha256(final_rules_text.encode("utf-8")).hexdigest()

        audit_id = save_rule_audit(
            hackathon_id=h_id,
            source_url=source_url,
            verification_status=verification_status,
            raw_rules_text=final_rules_text,
            eligibility=categories["eligibility"],
            team_size=categories["team_size"],
            submission_requirements=categories["submission_requirements"],
            hosting_requirements=categories["hosting_requirements"],
            content_hash=content_hash,
            notes=notes,
            db_path=self.db_path,
        )

        return {
            "audit_id": audit_id,
            "hackathon_id": h_id,
            "title": hackathon["title"],
            "source_url": source_url,
            "retrieval_timestamp": now_iso,
            "verification_status": verification_status,
            "notes": notes,
            "categories": categories,
            "content_hash": content_hash,
            "raw_rules_excerpt": final_rules_text[:1200] + ("..." if len(final_rules_text) > 1200 else ""),
        }

    def diff_rules(
        self,
        identifier: str,
        new_rule_text: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Calculates differences between rule revisions or against candidate text."""
        hackathon = get_hackathon(identifier, db_path=self.db_path)
        if not hackathon:
            return {
                "error": f"Hackathon not found matching '{identifier}'",
                "identifier": identifier,
            }

        h_id = hackathon["id"]
        audits = get_rule_audits(h_id, limit=5, db_path=self.db_path)

        if not audits:
            return {
                "hackathon_id": h_id,
                "title": hackathon["title"],
                "has_changes": False,
                "message": "No historical audits found. Run audit_rules first.",
                "diff_unified": "",
                "risk_level": "NONE",
            }

        latest_audit = audits[0]
        base_text = latest_audit["raw_rules_text"]

        if new_rule_text is not None:
            compare_text = new_rule_text
            target_desc = "Provided Candidate Text"
        elif len(audits) > 1:
            compare_text = audits[1]["raw_rules_text"]
            target_desc = f"Previous Audit (ID {audits[1]['id']})"
        else:
            return {
                "hackathon_id": h_id,
                "title": hackathon["title"],
                "has_changes": False,
                "message": "Single baseline audit recorded; no previous revision or candidate text to diff against.",
                "diff_unified": "",
                "risk_level": "NONE",
                "latest_audit_id": latest_audit["id"],
            }

        # Generate line-based unified diff
        base_lines = base_text.splitlines(keepends=True)
        compare_lines = compare_text.splitlines(keepends=True)
        diff_lines = list(difflib.unified_diff(
            compare_lines,
            base_lines,
            fromfile=target_desc,
            tofile=f"Audit {latest_audit['id']}",
            n=2,
        ))

        has_changes = len(diff_lines) > 0
        diff_unified = "".join(diff_lines)

        risk_level = "NONE"
        if has_changes:
            diff_lower = diff_unified.lower()
            if any(k in diff_lower for k in ["deadline", "date", "must submit", "hosting", "disqualif", "deploy"]):
                risk_level = "HIGH"
            elif any(k in diff_lower for k in ["eligib", "team", "prize", "guideline"]):
                risk_level = "MEDIUM"
            else:
                risk_level = "LOW"

        return {
            "hackathon_id": h_id,
            "title": hackathon["title"],
            "has_changes": has_changes,
            "compared_against": target_desc,
            "risk_level": risk_level,
            "diff_unified": diff_unified,
            "latest_audit_timestamp": latest_audit["retrieval_timestamp"],
            "verification_status": latest_audit["verification_status"],
        }
