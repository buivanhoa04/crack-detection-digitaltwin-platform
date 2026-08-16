"""
Health check router.
Monitors connectivity to all AI services.
"""
from fastapi import APIRouter, Request
from datetime import datetime, timezone
import time
import httpx

from app.config import settings

router = APIRouter()


@router.get("")
async def system_health(request: Request):
    """
    Check health of all connected services.
    Returns status of Crack API, RAGFlow Middleware, MongoDB, and Redis.
    """
    from app.database import db_instance
    import redis.asyncio as aioredis
    
    client: httpx.AsyncClient = request.app.state.http_client
    services = []

    # ── Check MongoDB Database ───────────────────────────
    try:
        start = time.monotonic()
        if db_instance.client is not None:
            await db_instance.client.admin.command('ping')
            elapsed = (time.monotonic() - start) * 1000
            services.append({
                "service": "Database",
                "status": "healthy",
                "response_time_ms": round(elapsed, 1),
                "details": {"name": settings.DATABASE_NAME}
            })
        else:
            raise Exception("Database client not initialized")
    except Exception as e:
        services.append({
            "service": "Database",
            "status": "unhealthy",
            "response_time_ms": None,
            "details": {"error": str(e)}
        })

    # ── Check Redis Cache ────────────────────────────────
    try:
        start = time.monotonic()
        r = aioredis.from_url(settings.REDIS_URL, socket_timeout=2.0)
        await r.ping()
        elapsed = (time.monotonic() - start) * 1000
        services.append({
            "service": "Redis Cache",
            "status": "healthy",
            "response_time_ms": round(elapsed, 1),
            "details": {}
        })
        await r.close()
    except Exception as e:
        services.append({
            "service": "Redis Cache",
            "status": "unhealthy",
            "response_time_ms": None,
            "details": {"error": str(e)}
        })

    # ── Check Crack Detection API ────────────────────────
    try:
        start = time.monotonic()
        resp = await client.get(
            f"{settings.CRACK_API_URL}/api/v1/health",
            headers={"Authorization": f"Bearer {settings.CRACK_API_TOKEN}"},
            timeout=5.0,
        )
        elapsed = (time.monotonic() - start) * 1000
        crack_payload = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
        crack_ok = resp.status_code == 200 and crack_payload.get("status") is True
        services.append({
            "service": "Crack Detection API",
            "status": "healthy" if crack_ok else "unhealthy",
            "response_time_ms": round(elapsed, 1),
            "details": {
                "url": settings.CRACK_API_URL,
                "status_code": resp.status_code,
                "mongodb": crack_payload.get("mongodb"),
            },
        })
    except Exception as e:
        services.append({
            "service": "Crack Detection API",
            "status": "unhealthy",
            "response_time_ms": None,
            "details": {"url": settings.CRACK_API_URL, "error": str(e)},
        })

    # ── Check RAGFlow Middleware ──────────────────────────
    try:
        start = time.monotonic()
        try:
            resp = await client.get(
                f"{settings.RAGFLOW_API_URL}/health",
                headers={"X-API-Token": settings.RAGFLOW_API_TOKEN},
                timeout=5.0,
            )
        except Exception:
            resp = await client.get(
                f"{settings.RAGFLOW_API_URL}/sessions",
                headers={"X-API-Token": settings.RAGFLOW_API_TOKEN},
                params={"user_id": "admin@digitaltwin.vn"},
                timeout=5.0,
            )
        elapsed = (time.monotonic() - start) * 1000
        rag_ok = resp.status_code in (200, 201, 401, 403)
        services.append({
            "service": "RAGFlow Middleware",
            "status": "healthy" if rag_ok else "unhealthy",
            "response_time_ms": round(elapsed, 1),
            "details": {"url": settings.RAGFLOW_API_URL, "status_code": resp.status_code},
        })
    except Exception as e:
        services.append({
            "service": "RAGFlow Middleware",
            "status": "unhealthy",
            "response_time_ms": None,
            "details": {"url": settings.RAGFLOW_API_URL, "error": str(e)},
        })

    # ── Overall status ───────────────────────────────────
    all_healthy = all(s["status"] == "healthy" for s in services)
    any_healthy = any(s["status"] == "healthy" for s in services)

    return {
        "status": "healthy" if all_healthy else ("degraded" if any_healthy else "unhealthy"),
        "services": services,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/ping")
async def ping():
    """Simple health ping - always returns OK if the Central Backend is running."""
    return {"status": "ok", "service": settings.APP_NAME}
