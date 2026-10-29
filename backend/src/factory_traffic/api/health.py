from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

from factory_traffic import __version__

router = APIRouter(tags=["Health"])


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    service: Literal["factory-traffic-api"] = "factory-traffic-api"
    version: str


@router.get("/health", response_model=HealthResponse)
def get_health() -> HealthResponse:
    """Process liveness only; this does not report physical-controller health."""
    return HealthResponse(version=__version__)
