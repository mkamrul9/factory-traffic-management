from fastapi import FastAPI

from factory_traffic import __version__
from factory_traffic.api.health import router as health_router


def create_app() -> FastAPI:
    app = FastAPI(
        title="Factory Traffic Management API",
        version=__version__,
        description="Junction traffic control and physical-controller simulation.",
    )
    app.include_router(health_router, prefix="/api")
    return app
