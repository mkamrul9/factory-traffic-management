import os
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI
from starlette.concurrency import run_in_threadpool

from factory_traffic import __version__
from factory_traffic.api.errors import register_error_handlers
from factory_traffic.api.health import router as health_router
from factory_traffic.api.traffic import router as traffic_router
from factory_traffic.application.traffic import TrafficService
from factory_traffic.persistence.sqlite import SQLiteRepository


def create_app(
    database_path: Path | None = None, *, clock: Callable[[], datetime] | None = None
) -> FastAPI:
    path = database_path or Path(
        os.environ.get("FACTORY_TRAFFIC_DB", "data/factory-traffic.sqlite3")
    )
    repository = SQLiteRepository(path)
    service = TrafficService(repository) if clock is None else TrafficService(repository, clock)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        await run_in_threadpool(service.initialize)
        yield

    app = FastAPI(
        title="Factory Traffic Management API",
        version=__version__,
        description="Junction traffic control and physical-controller simulation.",
        lifespan=lifespan,
    )
    app.state.traffic_service = service
    register_error_handlers(app)
    app.include_router(health_router, prefix="/api")
    app.include_router(traffic_router, prefix="/api")
    return app
