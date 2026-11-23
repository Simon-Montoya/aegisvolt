"""graphql-gateway: capa de agregación GraphQL + orquestador de la SAGA."""

from __future__ import annotations

import os
import sys

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from strawberry.fastapi import GraphQLRouter

from aegis_common.logs import configure_logging
from aegis_common.web import install_common

from . import state
from .schema import schema

log = configure_logging(state.settings)
app = FastAPI(title="AegisVolt · graphql-gateway", version=state.settings.app_version)
install_common(app, state.settings)
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "*").split(","),
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Correlation-ID", "X-AegisVolt-Track"],
)
app.include_router(GraphQLRouter(schema), prefix="/graphql")


@app.get("/ready")
def ready():
    return {"status": "ok", "downstream": state.clients().health()}


if __name__ == "__main__" and len(sys.argv) > 1 and sys.argv[1] == "print-schema":
    # Usado por CI (lint & build) para validar que schema.graphql está al día.
    print(schema.as_str())
