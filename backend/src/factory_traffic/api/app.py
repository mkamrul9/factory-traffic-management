import asyncio
import logging
import os
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager, suppress
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI
from starlette.concurrency import run_in_threadpool

from factory_traffic import __version__
from factory_traffic.api.controller import router as controller_router
from factory_traffic.api.errors import register_error_handlers
from factory_traffic.api.health import router as health_router
from factory_traffic.api.traffic import router as traffic_router
from factory_traffic.application.ports import TrafficRepository
from factory_traffic.application.traffic import TrafficService
from factory_traffic.persistence.postgresql import PostgreSQLRepository
from factory_traffic.persistence.sqlite import SQLiteRepository

logger = logging.getLogger(__name__)


def create_app(
    database_path: Path | None = None,
    *,
    database_url: str | None = None,
    clock: Callable[[], datetime] | None = None,
    run_background_tasks: bool = True,
    tick_interval: float = 0.1,
) -> FastAPI:
    path = database_path or Path(
        os.environ.get("FACTORY_TRAFFIC_DB", "data/factory-traffic.sqlite3")
    )
    hosted_url = database_url or os.environ.get("DATABASE_URL")
    if (
        database_path is None
        and os.environ.get("FACTORY_TRAFFIC_REQUIRE_POSTGRES") == "1"
        and not hosted_url
    ):
        raise ValueError("Hosted deployment requires DATABASE_URL; SQLite fallback is disabled.")
    repository: TrafficRepository
    if database_path is None and hosted_url:
        if not hosted_url.startswith(("postgresql://", "postgres://")):
            raise ValueError("DATABASE_URL must be a PostgreSQL connection URL.")
        repository = PostgreSQLRepository(hosted_url)
    else:
        repository = SQLiteRepository(path)
    service = TrafficService(repository) if clock is None else TrafficService(repository, clock)

    async def tick_loop() -> None:
        while True:
            try:
                await run_in_threadpool(service.tick_all)
            except Exception:
                logger.exception("Traffic runtime tick failed; commands remain durable")
                await asyncio.sleep(1)
            await asyncio.sleep(tick_interval)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        task = None
        try:
            await run_in_threadpool(service.initialize)
            task = asyncio.create_task(tick_loop()) if run_background_tasks else None
            yield
        finally:
            if task is not None:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
            await run_in_threadpool(repository.close)

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
    app.include_router(controller_router, prefix="/api")
    return app
