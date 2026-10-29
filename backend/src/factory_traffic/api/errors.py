import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse

from factory_traffic.api.traffic import get_service
from factory_traffic.application.ports import StorageUnavailable
from factory_traffic.application.traffic import JunctionAlreadyExists, JunctionNotFound

logger = logging.getLogger(__name__)


def error_response(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}})


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(JunctionNotFound)
    async def junction_not_found(request: Request, error: JunctionNotFound) -> JSONResponse:
        return error_response(404, "UNKNOWN_JUNCTION", f"Junction {error} does not exist.")

    @app.exception_handler(JunctionAlreadyExists)
    async def junction_exists(request: Request, error: JunctionAlreadyExists) -> JSONResponse:
        return error_response(409, "JUNCTION_ALREADY_EXISTS", f"Junction {error} already exists.")

    @app.exception_handler(StorageUnavailable)
    async def storage_unavailable(request: Request, error: StorageUnavailable) -> JSONResponse:
        logger.error("Persistence operation failed", exc_info=error)
        return error_response(503, "STORAGE_UNAVAILABLE", "Database temporarily unavailable.")

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, error: HTTPException) -> JSONResponse:
        response = error_response(error.status_code, "HTTP_ERROR", str(error.detail))
        if error.headers:
            response.headers.update(error.headers)
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, error: RequestValidationError) -> JSONResponse:
        details: list[dict[str, object]] = [
            {
                "location": ".".join(str(part) for part in item["loc"]),
                "message": item["msg"],
                "type": item["type"],
            }
            for item in error.errors()[:16]
        ]
        if request.url.path == "/api/sensor-events":
            body = error.body if isinstance(error.body, dict) else {}

            def identifier(key: str) -> str | None:
                value = body.get(key)
                return value.strip() if isinstance(value, str) and len(value) <= 128 else None

            try:
                await run_in_threadpool(
                    get_service(request).audit_invalid_event,
                    identifier("junction_id"),
                    identifier("event_id"),
                    details,
                )
            except StorageUnavailable:
                logger.exception("Could not audit an invalid sensor request")
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "VALIDATION_ERROR",
                    "message": "Request validation failed.",
                    "details": details,
                }
            },
        )
