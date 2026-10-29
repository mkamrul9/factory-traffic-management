"""ASGI entry point: uvicorn factory_traffic.main:app."""

from factory_traffic.api.app import create_app

app = create_app()
