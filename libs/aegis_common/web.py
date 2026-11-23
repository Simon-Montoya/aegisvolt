"""Piezas web comunes: middleware de correlation_id, /health y errores de negocio."""

from __future__ import annotations

import logging
import os
import time
import uuid

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .config import Settings
from .logs import correlation_id_var

CORRELATION_HEADER = "X-Correlation-ID"
TRACK_HEADER = "X-AegisVolt-Track"


class BusinessError(Exception):
    """Error de negocio con código legible para el orquestador de la SAGA."""

    def __init__(self, status_code: int, code: str, message: str, **details) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details


def install_common(app: FastAPI, settings: Settings) -> None:
    log = logging.getLogger(settings.service_name)

    @app.middleware("http")
    async def correlation_middleware(request: Request, call_next):
        correlation_id = request.headers.get(CORRELATION_HEADER) or str(uuid.uuid4())
        token = correlation_id_var.set(correlation_id)
        started = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            response.headers[CORRELATION_HEADER] = correlation_id
            response.headers[TRACK_HEADER] = settings.deploy_track
            return response
        finally:
            if request.url.path not in ("/health", "/ready"):
                level = logging.ERROR if status >= 500 else logging.INFO
                log.log(
                    level,
                    "http_request",
                    extra={
                        "method": request.method,
                        "path": request.url.path,
                        "status_code": status,
                        "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                    },
                )
            correlation_id_var.reset(token)

    @app.exception_handler(BusinessError)
    async def business_error_handler(_: Request, exc: BusinessError):
        return JSONResponse(status_code=exc.status_code, content={"code": exc.code, "message": exc.message, **exc.details})

    bad_release = os.getenv("SIMULATE_BAD_RELEASE", "false").lower() == "true"

    @app.get("/health")
    def health():
        if bad_release:
            # Solo para demostrar el rollback automático del Canary (variable del pipeline).
            log.error("health_check_failed", extra={"reason": "SIMULATE_BAD_RELEASE"})
            return JSONResponse(status_code=500, content={"status": "error", "track": settings.deploy_track, "version": settings.app_version})
        return {
            "status": "ok",
            "service": settings.service_name,
            "environment": settings.environment,
            "version": settings.app_version,
            "track": settings.deploy_track,
        }
