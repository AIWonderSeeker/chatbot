from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from app.agent.errors import ToolsUnavailableError
from app.api.deps import Services, get_services

router = APIRouter()


@router.get("/health")
async def health() -> dict[str, str]:
    """Liveness for the ALB target group: no dependencies touched."""
    return {"status": "ok"}


@router.get("/ready")
async def ready(services: Services = Depends(get_services)) -> JSONResponse:
    """Config is valid (or the app would not have started) and tools can be listed."""
    checks: dict[str, str] = {"config": "ok"}
    try:
        await services.registry.ensure_loaded()
        checks["tools"] = "ok"
    except ToolsUnavailableError:
        checks["tools"] = "unavailable"
    checks["store"] = "ok" if await services.store.ping() else "unavailable"
    ok = all(v == "ok" for v in checks.values())
    return JSONResponse(
        status_code=200 if ok else 503,
        content={
            "status": "ready" if ok else "not_ready",
            "checks": checks,
            "mock_bedrock": services.settings.mock_bedrock,
            "environment": services.settings.environment,
        },
    )
