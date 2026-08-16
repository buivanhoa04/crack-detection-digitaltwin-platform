"""
FastAPI main application entry point.
Central Backend that proxies requests to AI services.
"""
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from typing import Optional
import httpx

from app.config import settings
from app.database import connect_to_mongo, close_mongo_connection

# ── Shared HTTP client (connection pooling) ──────────────
class AppState:
    http_client: Optional[httpx.AsyncClient] = None

app_state = AppState()

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage shared resources lifecycle."""
    # 1. Start HTTP client
    try:
        app_state.http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(60.0, connect=10.0), # Higher timeouts for Tailscale latency
            limits=httpx.Limits(max_connections=500, max_keepalive_connections=100), # Handle many images simultaneously
        )
        app.state.http_client = app_state.http_client
    except Exception as e:
        print(f"[ERROR] Failed to start HTTP client: {e}")

    # 2. Defere Database Init to background to avoid blocking server start
    import asyncio
    async def bg_init():
        try:
            await connect_to_mongo()
            from app.models.user import init_admin
            await init_admin()
            print(f"[INFO] Backend services initialized successfully")
        except Exception as e:
            print(f"[ERROR] Background init failed: {e}")

    asyncio.create_task(bg_init())
    
    print(f"[STARTED] {settings.APP_NAME} v{settings.APP_VERSION}")
    yield
    
    # Shutdown
    if app_state.http_client:
        await app_state.http_client.aclose()
    await close_mongo_connection()
    print("[SHUTDOWN] Shutting down...")

# ── Create FastAPI app ───────────────────────────────────
app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="Central Backend API - He thong Ban sao so Cong trinh Giao thong",
    lifespan=lifespan,
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
)

# ── CORS Middleware ──────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class ASGIRequestLoggerMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            path = scope.get("path", "")
            query = scope.get("query_string", b"").decode("utf-8")
            if query:
                from urllib.parse import parse_qsl, urlencode
                query = urlencode([
                    (key, "[REDACTED]" if key.lower() in {"token", "access_token", "api_key"} else value)
                    for key, value in parse_qsl(query, keep_blank_values=True)
                ])
            full_path = f"{path}?{query}" if query else path
            if not "/static/" in path and not "/files/" in path:
                print(f"   [API] {scope['method']} {full_path}")
        await self.app(scope, receive, send)

app.add_middleware(ASGIRequestLoggerMiddleware)

# ── Delayed Router Imports to avoid Startup Deadlocks ────
from app.routers import (
    auth, crack, chatbot, health, archive, users, 
    incidents, audit_log, settings as settings_router,
    files, surveys, trash, alignment, segments
)

from fastapi.staticfiles import StaticFiles
import os

# ── Include Routers ──────────────────────────────────────
app.include_router(health.router, prefix="/api/health", tags=["Health"])
app.include_router(auth.router, prefix="/api/auth", tags=["Authentication"])
app.include_router(users.router, prefix="/api/users", tags=["User Management"])
app.include_router(crack.router, prefix="/api/crack", tags=["Crack Detection"])
app.include_router(chatbot.router, prefix="/api/chatbot", tags=["AI Chatbot"])
app.include_router(archive.router, prefix="/api/archive", tags=["Archive & Assets"])
app.include_router(incidents.router, prefix="/api/incidents", tags=["Incidents"])
app.include_router(alignment.router, prefix="/api/alignment", tags=["Alignment & CAD"])
app.include_router(audit_log.router, prefix="/api/audit", tags=["Audit Log"])
app.include_router(settings_router.router, prefix="/api/settings", tags=["System Settings"])
app.include_router(files.router, prefix="/api/v1/files", tags=["File Service"])
app.include_router(surveys.router, prefix="/api/surveys", tags=["Surveys/Campaigns"])
app.include_router(trash.router, prefix="/api/trash", tags=["Trash/Recycle"])
app.include_router(segments.router, prefix="/api/segments", tags=["Segments & Routes"])

# ── Static Files Serving now handled by files router for better CORS support ──
# CRACK_SOURCES = os.getenv("CRACK_SOURCES_DIR", r"g:\crack_api\sources")
# if os.path.exists(CRACK_SOURCES):
#     app.mount("/api/v1/files", StaticFiles(directory=CRACK_SOURCES), name="crack_sources")

@app.get("/api")
async def root():
    return {
        "name": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "docs": "/api/docs",
    }
