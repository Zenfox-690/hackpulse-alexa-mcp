"""FastAPI ASGI application mounting the MCP 2025-11-25 Streamable HTTP endpoint,
CORS policies exposing Mcp-Session-Id, readiness /health monitoring,
and the Centre-Stage Alexa+ Multimodal Web Canvas.
"""

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict
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

# Mount static assets
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
def serve_index():
    """Serves the Amazon Echo Show 15 / Alexa+ Centre-Stage Web Interface."""
    index_file = STATIC_DIR / "index.html"
    if index_file.exists():
        return FileResponse(str(index_file), media_type="text/html")
    return {"message": "HackPulse Alexa+ Backend Online. (static/index.html not found)"}


@app.get("/health")
def healthcheck():
    conn = get_db_connection()
    count = conn.execute("SELECT COUNT(*) AS c FROM hackathons").fetchone()["c"]
    audits = conn.execute("SELECT COUNT(*) AS c FROM rule_audits").fetchone()["c"]
    conn.close()

    return {
        "status": "healthy",
        "service": SERVER_INFO["name"],
        "version": SERVER_INFO["version"],
        "mcp_protocol_version": PROTOCOL_VERSION,
        "total_hackathons": count,
        "total_rule_audits": audits,
    }


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
