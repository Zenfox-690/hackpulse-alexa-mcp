"""MCP 2025-11-25 Streamable HTTP Protocol Handler.
Supports JSON-RPC 2.0 initialization, session management,
the 7 core HackPulse tools, structuredContent stage views, resources, and prompt templates.
"""

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import httpx

from core.database import (
    generate_or_update_sprint_plan,
    get_builder_profile,
    get_hackathon,
    load_seed_if_empty,
    query_hackathons,
    record_session,
    sync_upstream_json,
    update_builder_profile,
)
from core.rule_auditor import RuleAuditor

PROTOCOL_VERSION = "2025-11-25"
SERVER_INFO = {
    "name": "hackpulse-alexa-mcp",
    "version": "1.0.0",
}


class MCPServer:
    def __init__(self, db_path: Optional[Path | str] = None):
        self.db_path = db_path
        self.auditor = RuleAuditor(db_path=db_path)

    # -------------------------------------------------------------------------
    # Core Tools Definition (7 Tools)
    # -------------------------------------------------------------------------
    @staticmethod
    def get_tool_definitions() -> List[Dict[str, Any]]:
        return [
            {
                "name": "search_hackathons",
                "description": "Search and filter the hackathon catalog by keyword, source, status, location/format, and tags, enriched with profile feasibility score.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Search keyword matching title, description, or tags.",
                        },
                        "source": {
                            "type": "string",
                            "description": "Platform source filter: 'Devpost', 'Devfolio', or 'Unstop'.",
                        },
                        "status": {
                            "type": "string",
                            "description": "Status filter: 'open', 'ended', or 'upcoming'.",
                        },
                        "location": {
                            "type": "string",
                            "description": "Location / format filter, e.g. 'Online' or 'In-Person'.",
                        },
                        "tag": {
                            "type": "string",
                            "description": "Specific topic tag filter, e.g. 'ai', 'cloud', 'blockchain'.",
                        },
                        "sort_by_feasibility": {
                            "type": "boolean",
                            "description": "If true, sorts results descending by feasibility score for the active profile.",
                            "default": False,
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Maximum number of results to return (default 10, max 50).",
                            "default": 10,
                        },
                    },
                },
            },
            {
                "name": "get_hackathon_details",
                "description": "Retrieve comprehensive details, prize breakdowns, timeline dates, and URLs for a specific hackathon.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "identifier": {
                            "type": "string",
                            "description": "Hackathon ID, fingerprint, URL, or partial title.",
                        },
                    },
                    "required": ["identifier"],
                },
            },
            {
                "name": "sync_upstream_catalog",
                "description": "Sync catalog from upstream URL or local fallback seed (data/upstream_seed.json) into persistent SQLite storage.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "upstream_url": {
                            "type": "string",
                            "description": "Optional remote catalog JSON URL. If omitted or fails, offline seed is safely used.",
                        },
                        "force_refresh": {
                            "type": "boolean",
                            "description": "Whether to force reload even if catalog has items.",
                            "default": False,
                        },
                    },
                },
            },
            {
                "name": "audit_rules",
                "description": "Inspect and extract official hackathon rules, FAQs, eligibility, submission requirements, and post-submission hosting constraints with provenance tracking.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "identifier": {
                            "type": "string",
                            "description": "Hackathon ID, fingerprint, URL, or title.",
                        },
                        "force_live": {
                            "type": "boolean",
                            "description": "Attempt live web fetch even if previously audited.",
                            "default": False,
                        },
                    },
                    "required": ["identifier"],
                },
            },
            {
                "name": "diff_rules",
                "description": "Compare rule revisions or candidate requirements against baseline audits to detect changes in deadlines, eligibility, or hosting constraints.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "identifier": {
                            "type": "string",
                            "description": "Hackathon ID, fingerprint, URL, or title.",
                        },
                        "new_rule_text": {
                            "type": "string",
                            "description": "Optional new candidate rules text to compare against latest audit.",
                        },
                    },
                    "required": ["identifier"],
                },
            },
            {
                "name": "generate_sprint_plan",
                "description": "Generate or update a persistent 15-hour time-boxed sprint plan calibrated to the builder profile with Go/No-Go checkpoint and milestone toggles.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "hackathon_id": {
                            "type": "string",
                            "description": "Target hackathon ID or fingerprint.",
                        },
                        "available_hours": {
                            "type": "integer",
                            "description": "Weekly available builder hours (default 15).",
                            "default": 15,
                        },
                        "completed_milestone_id": {
                            "type": "string",
                            "description": "Optional milestone ID (M1-M5) to toggle completion status.",
                        },
                    },
                    "required": ["hackathon_id"],
                },
            },
            {
                "name": "manage_builder_profile",
                "description": "Retrieve or update the persistent cross-session builder profile, budget, hours, skills, and bookmark toggles.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "description": "Action to perform: 'get' or 'update'.",
                            "default": "get",
                        },
                        "hours_per_week": {
                            "type": "integer",
                            "description": "Weekly available hours.",
                        },
                        "budget_inr": {
                            "type": "number",
                            "description": "Available budget in INR.",
                        },
                        "prefer_online_only": {
                            "type": "boolean",
                            "description": "Preference for online-only hackathons.",
                        },
                        "skills": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Builder technical skills.",
                        },
                        "toggle_bookmark_id": {
                            "type": "string",
                            "description": "Hackathon ID to toggle in bookmarks list.",
                        },
                    },
                },
            },
        ]

    # -------------------------------------------------------------------------
    # Resources Definition
    # -------------------------------------------------------------------------
    @staticmethod
    def get_resource_definitions() -> List[Dict[str, Any]]:
        return [
            {
                "uri": "hackpulse://catalog/summary",
                "name": "Catalog Summary",
                "description": "High-level summary of active hackathons across Devpost, Devfolio, and Unstop.",
                "mimeType": "application/json",
            },
            {
                "uri": "hackpulse://rules/provenance-schema",
                "name": "Rule Provenance Schema",
                "description": "Specification of rule audit verification statuses and provenance fields.",
                "mimeType": "text/markdown",
            },
            {
                "uri": "hackpulse://profile/active",
                "name": "Active Builder Profile",
                "description": "Current builder profile constraints, skills, and bookmarks.",
                "mimeType": "application/json",
            },
        ]

    # -------------------------------------------------------------------------
    # Prompts Definition
    # -------------------------------------------------------------------------
    @staticmethod
    def get_prompt_definitions() -> List[Dict[str, Any]]:
        return [
            {
                "name": "hackathon_eligibility_check",
                "description": "Evaluate participant qualifications against audited hackathon rules.",
                "arguments": [
                    {
                        "name": "identifier",
                        "description": "Hackathon title or ID",
                        "required": True,
                    },
                    {
                        "name": "developer_profile",
                        "description": "User background: student/pro, country, team size",
                        "required": True,
                    },
                ],
            },
            {
                "name": "hackathon_submission_readiness",
                "description": "Check project readiness against official deliverables and hosting requirements.",
                "arguments": [
                    {
                        "name": "identifier",
                        "description": "Hackathon title or ID",
                        "required": True,
                    },
                    {
                        "name": "project_artifacts",
                        "description": "Available artifacts: github repo, demo video, deployed url",
                        "required": True,
                    },
                ],
            },
        ]

    # -------------------------------------------------------------------------
    # Tool Execution Handlers
    # -------------------------------------------------------------------------
    async def call_tool(self, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        try:
            if name == "search_hackathons":
                results = query_hackathons(
                    query=arguments.get("query"),
                    source=arguments.get("source"),
                    status=arguments.get("status"),
                    location=arguments.get("location"),
                    tag=arguments.get("tag"),
                    sort_by_feasibility=bool(arguments.get("sort_by_feasibility", False)),
                    limit=int(arguments.get("limit", 10)),
                    db_path=self.db_path,
                )
                data = {"count": len(results), "hackathons": results}
                top = results[0] if results else None
                top_str = f"Top match is '{top['title']}' ({top.get('feasibility_score', 0)}% fit)." if top else "No matches found."
                voice = f"I found {len(results)} hackathons. {top_str}"

                return {
                    "content": [{"type": "text", "text": json.dumps(data, indent=2)}],
                    "structuredContent": {
                        "stageView": "CAROUSEL",
                        "voiceSummary": voice,
                        "data": data,
                    },
                    "_meta": {"ui": {"resourceUri": "ui://hackpulse/stage-view"}},
                    "isError": False,
                }

            elif name == "get_hackathon_details":
                ident = arguments.get("identifier", "")
                result = get_hackathon(ident, db_path=self.db_path)
                if not result:
                    return {
                        "content": [{"type": "text", "text": f"Hackathon not found matching '{ident}'"}],
                        "structuredContent": {
                            "stageView": "DETAIL",
                            "voiceSummary": f"Could not find any hackathon matching '{ident}'.",
                            "data": {},
                        },
                        "isError": True,
                    }
                voice = f"Here are the details for '{result.get('title')}'. Prize is {result.get('prize')}, location is {result.get('location')}."
                return {
                    "content": [{"type": "text", "text": json.dumps(result, indent=2)}],
                    "structuredContent": {
                        "stageView": "DETAIL",
                        "voiceSummary": voice,
                        "data": result,
                    },
                    "_meta": {"ui": {"resourceUri": "ui://hackpulse/stage-view"}},
                    "isError": False,
                }

            elif name == "sync_upstream_catalog":
                url = arguments.get("upstream_url")
                data = None
                is_fallback = False

                if url:
                    try:
                        async with httpx.AsyncClient(timeout=8.0) as client:
                            resp = await client.get(url)
                            if resp.status_code == 200:
                                data = resp.json()
                    except Exception:
                        data = None

                if not data:
                    is_fallback = True
                    seed_path = Path(__file__).parent.parent / "data" / "upstream_seed.json"
                    if seed_path.exists():
                        with open(seed_path, "r", encoding="utf-8") as f:
                            data = json.load(f)

                if not data:
                    return {
                        "content": [{"type": "text", "text": "Failed to sync: no remote or fallback seed available."}],
                        "structuredContent": {
                            "stageView": "CAROUSEL",
                            "voiceSummary": "Sync failed: no remote catalog or offline seed accessible.",
                            "data": {},
                        },
                        "isError": True,
                    }

                sync_res = sync_upstream_json(data, is_fallback=is_fallback, db_path=self.db_path)
                voice = f"Catalog synchronized. {sync_res.get('total_hackathons')} hackathons active in database ({sync_res.get('source_type')})."
                return {
                    "content": [{"type": "text", "text": json.dumps(sync_res, indent=2)}],
                    "structuredContent": {
                        "stageView": "CAROUSEL",
                        "voiceSummary": voice,
                        "data": sync_res,
                    },
                    "_meta": {"ui": {"resourceUri": "ui://hackpulse/stage-view"}},
                    "isError": False,
                }

            elif name == "audit_rules":
                ident = arguments.get("identifier", "")
                force_live = bool(arguments.get("force_live", False))
                audit_res = await self.auditor.audit_rules(ident, force_live=force_live)
                is_err = "error" in audit_res
                status = audit_res.get("verification_status", "unknown")
                voice = f"Rule audit completed for '{audit_res.get('title', ident)}'. Verification status is {status}."
                return {
                    "content": [{"type": "text", "text": json.dumps(audit_res, indent=2)}],
                    "structuredContent": {
                        "stageView": "RULE_AUDIT",
                        "voiceSummary": voice,
                        "data": audit_res,
                    },
                    "_meta": {"ui": {"resourceUri": "ui://hackpulse/stage-view"}},
                    "isError": is_err,
                }

            elif name == "diff_rules":
                ident = arguments.get("identifier", "")
                new_text = arguments.get("new_rule_text")
                diff_res = self.auditor.diff_rules(ident, new_rule_text=new_text)
                is_err = "error" in diff_res
                risk = diff_res.get("risk_level", "NONE")
                voice = f"Rule diff analysis for '{diff_res.get('title', ident)}': Risk level is {risk}."
                return {
                    "content": [{"type": "text", "text": json.dumps(diff_res, indent=2)}],
                    "structuredContent": {
                        "stageView": "RULE_DIFF",
                        "voiceSummary": voice,
                        "data": diff_res,
                    },
                    "_meta": {"ui": {"resourceUri": "ui://hackpulse/stage-view"}},
                    "isError": is_err,
                }

            elif name == "generate_sprint_plan":
                h_id = arguments.get("hackathon_id", "")
                hours = int(arguments.get("available_hours", 15))
                milestone_id = arguments.get("completed_milestone_id")
                plan = generate_or_update_sprint_plan(
                    hackathon_id=h_id,
                    available_hours=hours,
                    completed_milestone_id=milestone_id,
                    db_path=self.db_path,
                )
                voice = f"Sprint plan for '{plan.get('title')}': {plan.get('progress_percent')}% completed with {plan.get('remaining_hours')} hours remaining."
                return {
                    "content": [{"type": "text", "text": json.dumps(plan, indent=2)}],
                    "structuredContent": {
                        "stageView": "SPRINT_PLAN",
                        "voiceSummary": voice,
                        "data": plan,
                    },
                    "_meta": {"ui": {"resourceUri": "ui://hackpulse/stage-view"}},
                    "isError": False,
                }

            elif name == "manage_builder_profile":
                action = arguments.get("action", "get").lower()
                if action == "update" or any(k in arguments for k in ["hours_per_week", "budget_inr", "prefer_online_only", "skills", "toggle_bookmark_id"]):
                    profile = update_builder_profile(
                        hours_per_week=arguments.get("hours_per_week"),
                        budget_inr=arguments.get("budget_inr"),
                        prefer_online_only=arguments.get("prefer_online_only"),
                        skills=arguments.get("skills"),
                        toggle_bookmark_id=arguments.get("toggle_bookmark_id"),
                        db_path=self.db_path,
                    )
                else:
                    profile = get_builder_profile(db_path=self.db_path)

                voice = f"Builder profile updated for {profile.get('name')}: {profile.get('hours_per_week')}h per week, INR {profile.get('budget_inr')} budget."
                return {
                    "content": [{"type": "text", "text": json.dumps(profile, indent=2)}],
                    "structuredContent": {
                        "stageView": "PROFILE",
                        "voiceSummary": voice,
                        "data": profile,
                    },
                    "_meta": {"ui": {"resourceUri": "ui://hackpulse/stage-view"}},
                    "isError": False,
                }

            else:
                return {
                    "content": [{"type": "text", "text": f"Unknown tool: '{name}'"}],
                    "structuredContent": {
                        "stageView": "CAROUSEL",
                        "voiceSummary": f"Tool '{name}' is not recognized.",
                        "data": {},
                    },
                    "isError": True,
                }
        except Exception as e:
            return {
                "content": [{"type": "text", "text": f"Tool execution failed: {type(e).__name__}: {str(e)}"}],
                "structuredContent": {
                    "stageView": "CAROUSEL",
                    "voiceSummary": f"An error occurred executing tool: {type(e).__name__}.",
                    "data": {},
                },
                "isError": True,
            }

    # -------------------------------------------------------------------------
    # Resource Read Handler
    # -------------------------------------------------------------------------
    def read_resource(self, uri: str) -> Dict[str, Any]:
        if uri == "hackpulse://catalog/summary":
            hackathons = query_hackathons(limit=10, db_path=self.db_path)
            return {
                "contents": [
                    {
                        "uri": uri,
                        "mimeType": "application/json",
                        "text": json.dumps({
                            "summary": "Catalog overview",
                            "preview_count": len(hackathons),
                            "preview": hackathons,
                        }, indent=2),
                    }
                ]
            }
        elif uri == "hackpulse://rules/provenance-schema":
            return {
                "contents": [
                    {
                        "uri": uri,
                        "mimeType": "text/markdown",
                        "text": (
                            "# Rule Provenance Schema\n\n"
                            "- `verified_live`: Confirmed via direct HTTP fetch to official hackathon URL.\n"
                            "- `fallback_unverified`: Extracted from curated offline catalog metadata; live text unverified.\n"
                            "- `curated_offline`: Seed-verified offline snapshot.\n"
                        ),
                    }
                ]
            }
        elif uri == "hackpulse://profile/active":
            profile = get_builder_profile(db_path=self.db_path)
            return {
                "contents": [
                    {
                        "uri": uri,
                        "mimeType": "application/json",
                        "text": json.dumps(profile, indent=2),
                    }
                ]
            }
        else:
            raise ValueError(f"Resource not found: {uri}")

    # -------------------------------------------------------------------------
    # Prompts Handler
    # -------------------------------------------------------------------------
    def get_prompt(self, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        if name == "hackathon_eligibility_check":
            ident = arguments.get("identifier", "")
            profile = arguments.get("developer_profile", "")
            return {
                "description": f"Eligibility audit for {ident}",
                "messages": [
                    {
                        "role": "user",
                        "content": {
                            "type": "text",
                            "text": (
                                f"Please review the audited eligibility rules for hackathon '{ident}' "
                                f"and determine if the following profile qualifies:\n\n"
                                f"Profile: {profile}\n\n"
                                f"Use audit_rules to check verified age, region, and student requirements."
                            ),
                        },
                    }
                ],
            }
        elif name == "hackathon_submission_readiness":
            ident = arguments.get("identifier", "")
            artifacts = arguments.get("project_artifacts", "")
            return {
                "description": f"Submission readiness for {ident}",
                "messages": [
                    {
                        "role": "user",
                        "content": {
                            "type": "text",
                            "text": (
                                f"Verify if the following project deliverables satisfy the official "
                                f"submission criteria for hackathon '{ident}':\n\n"
                                f"Artifacts: {artifacts}\n\n"
                                f"Pay close attention to video duration, source code visibility, and hosting requirements."
                            ),
                        },
                    }
                ],
            }
        else:
            raise ValueError(f"Unknown prompt: {name}")

    # -------------------------------------------------------------------------
    # Main Streamable JSON-RPC Dispatcher
    # -------------------------------------------------------------------------
    async def handle_request(
        self,
        request_body: Dict[str, Any],
        session_id: Optional[str] = None,
    ) -> Tuple[Dict[str, Any], str]:
        """Processes JSON-RPC 2.0 messages, managing session tokens."""
        sess_id = session_id or str(uuid.uuid4())
        record_session(sess_id, {"last_method": request_body.get("method")}, db_path=self.db_path)

        req_id = request_body.get("id")
        method = request_body.get("method")
        params = request_body.get("params", {})

        # Handle notifications (no id)
        if req_id is None:
            if method == "notifications/initialized":
                return {}, sess_id
            return {}, sess_id

        # Handle Ping
        if method == "ping":
            return {"jsonrpc": "2.0", "id": req_id, "result": {}}, sess_id

        # Handle Initialize
        if method == "initialize":
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {
                        "tools": {"listChanged": False},
                        "resources": {"subscribe": False, "listChanged": False},
                        "prompts": {"listChanged": False},
                    },
                    "serverInfo": SERVER_INFO,
                },
            }, sess_id

        # Handle tools/list
        if method == "tools/list":
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"tools": self.get_tool_definitions()},
            }, sess_id

        # Handle tools/call
        if method == "tools/call":
            tool_name = params.get("name", "")
            args = params.get("arguments", {})
            call_res = await self.call_tool(tool_name, args)
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": call_res,
            }, sess_id

        # Handle resources/list
        if method == "resources/list":
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"resources": self.get_resource_definitions()},
            }, sess_id

        # Handle resources/read
        if method == "resources/read":
            uri = params.get("uri", "")
            try:
                res_content = self.read_resource(uri)
                return {"jsonrpc": "2.0", "id": req_id, "result": res_content}, sess_id
            except Exception as e:
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32602, "message": str(e)},
                }, sess_id

        # Handle prompts/list
        if method == "prompts/list":
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"prompts": self.get_prompt_definitions()},
            }, sess_id

        # Handle prompts/get
        if method == "prompts/get":
            prompt_name = params.get("name", "")
            p_args = params.get("arguments", {})
            try:
                prompt_res = self.get_prompt(prompt_name, p_args)
                return {"jsonrpc": "2.0", "id": req_id, "result": prompt_res}, sess_id
            except Exception as e:
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32602, "message": str(e)},
                }, sess_id

        # Method not found
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": -32601, "message": f"Method '{method}' not implemented."},
        }, sess_id
