"""FastAPI ASGI application mounting the MCP 2025-11-25 Streamable HTTP endpoint,
CORS policies exposing Mcp-Session-Id, readiness /health monitoring,
and the Centre-Stage Alexa+ Multimodal Web Canvas.
"""

from contextlib import asynccontextmanager
import os
from pathlib import Path
import secrets
from typing import Any, Dict, Optional
from fastapi import FastAPI, Header, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from core.database import get_db_connection, init_db, load_seed_if_empty
from core.mcp_server import MCPServer, PROTOCOL_VERSION, SERVER_INFO

server = MCPServer()
STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: ensure tables exist and seed catalog if empty
    init_db()
    load_seed_if_empty()
    yield


app = FastAPI(
    title="HackPulse Alexa MCP",
    description="Streamable HTTP MCP Server (2025-11-25), Persistent Builder Profile, Sprint Planner, and Alexa+ Ambient Display",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS configuration explicitly exposing Mcp-Session-Id
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Mcp-Session-Id", "Content-Type"],
)

@app.middleware("http")
async def add_anti_caching_headers(request: Request, call_next):
    """Ensures dynamic UI and script changes are always fresh in browser runtime."""
    response = await call_next(request)
    path = request.url.path
    if path == "/" or path.startswith("/static"):
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response

# Mount static assets
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
def serve_index():
    """Serves the Amazon Echo Show 15 / Alexa+ Centre-Stage Web Interface."""
    index_file = STATIC_DIR / "index.html"
    if index_file.exists():
        return FileResponse(
            str(index_file),
            media_type="text/html",
            headers={
                "Cache-Control": "no-cache, no-store, must-revalidate",
                "Pragma": "no-cache",
                "Expires": "0",
            },
        )
    return {"message": "HackPulse Alexa+ Backend Online. (static/index.html not found)"}


def verify_refresh_token(authorization: Optional[str]) -> bool:
    """Securely verifies the Bearer refresh token against CATALOG_REFRESH_TOKEN env var.
    Fails closed if the token is unconfigured, missing, or mismatched.
    Uses constant-time comparison to prevent timing attacks.
    """
    expected = os.environ.get("CATALOG_REFRESH_TOKEN", "").strip()
    if not expected:
        # Fail closed: No token configured on server
        return False

    if not authorization:
        return False

    parts = authorization.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return False

    provided = parts[1].strip()
    if not provided:
        return False

    return secrets.compare_digest(provided, expected)


@app.get("/health")
def healthcheck():
    from core.database import get_catalog_freshness, get_refresh_status
    conn = get_db_connection()
    count = conn.execute("SELECT COUNT(*) AS c FROM hackathons").fetchone()["c"]
    audits = conn.execute("SELECT COUNT(*) AS c FROM rule_audits").fetchone()["c"]
    conn.close()

    freshness = get_catalog_freshness()
    return {
        "status": "healthy",
        "service": SERVER_INFO["name"],
        "version": SERVER_INFO["version"],
        "mcp_protocol_version": PROTOCOL_VERSION,
        "total_hackathons": count,
        "total_rule_audits": audits,
        "catalog_freshness": {
            "is_fresh": freshness.get("is_fresh", False),
            "overall_status": freshness.get("overall_status", "unknown"),
            "fresh_sources": freshness.get("fresh_sources", []),
            "stale_sources": freshness.get("stale_sources", []),
        },
        "refresh_status": get_refresh_status(),
    }


@app.get("/sync/status")
def sync_status():
    """Returns operational refresh timestamps, per-source status, and freshness evaluation."""
    from core.database import get_catalog_freshness, get_refresh_status
    return {
        "status": "ok",
        "freshness": get_catalog_freshness(),
        "refresh_logs": get_refresh_status(),
    }


@app.get("/catalog/freshness")
def catalog_freshness(threshold_hours: int = 24):
    """Evaluates catalog freshness and source-level health against a configurable threshold."""
    from core.database import get_catalog_freshness
    return get_catalog_freshness(threshold_hours=threshold_hours)


@app.post("/sync/refresh")
def sync_refresh(
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
    retention_days: int = 30,
):
    """Triggers authenticated multi-source discovery, status refresh, and rolling 30-day retention purge."""
    if not verify_refresh_token(authorization):
        return JSONResponse(
            status_code=401,
            content={
                "status": "unauthorized",
                "error": "Unauthorized",
                "detail": "Invalid or missing catalog refresh token",
            },
        )

    try:
        from core.ingestion import ConcurrencyError, refresh_discovery_sources
        res = refresh_discovery_sources(retention_days=retention_days)
        return {
            "status": "ok",
            "overall_status": res.get("overall_status", "unknown"),
            "refresh_summary": res,
        }
    except ConcurrencyError as ce:
        return JSONResponse(
            status_code=409,
            content={
                "status": "conflict",
                "error": "Conflict",
                "detail": str(ce),
            },
        )


@app.get("/mcp")
def mcp_get_info():
    """Returns endpoint metadata for clients probing /mcp via GET."""
    return {
        "endpoint": "/mcp",
        "transport": "streamable_http",
        "protocol_version": PROTOCOL_VERSION,
        "server_info": SERVER_INFO,
    }


@app.post("/mcp")
async def mcp_post_endpoint(
    request: Request,
    response: Response,
    mcp_session_id: str | None = Header(default=None, alias="Mcp-Session-Id"),
):
    """Main JSON-RPC 2.0 streamable endpoint."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(
            status_code=400,
            content={"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error: Invalid JSON"}},
        )

    res_body, active_session_id = await server.handle_request(body, session_id=mcp_session_id)
    response.headers["Mcp-Session-Id"] = active_session_id
    response.headers["Content-Type"] = "application/json"
    return res_body


@app.delete("/mcp")
def mcp_delete_endpoint(
    response: Response,
    mcp_session_id: str | None = Header(default=None, alias="Mcp-Session-Id"),
):
    """Allows clients to cleanly terminate an MCP session."""
    if mcp_session_id:
        response.headers["Mcp-Session-Id"] = mcp_session_id
    return {"status": "session_terminated"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="127.0.0.1", port=8000, reload=True)
