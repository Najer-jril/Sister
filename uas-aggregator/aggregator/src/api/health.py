"""GET /health — liveness and readiness probe for Docker health checks."""

from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse

router = APIRouter()


@router.get("/health")
async def health(request: Request):
    """Return 200 when DB and Redis are reachable; 503 otherwise."""
    checks = {"database": False, "broker": False}

    try:
        await request.app.state.db_pool.fetchval("SELECT 1")
        checks["database"] = True
    except Exception:
        pass

    try:
        await request.app.state.redis.ping()
        checks["broker"] = True
    except Exception:
        pass

    ready = all(checks.values())
    code = status.HTTP_200_OK if ready else status.HTTP_503_SERVICE_UNAVAILABLE
    return JSONResponse({"status": "ok" if ready else "degraded", **checks}, status_code=code)
