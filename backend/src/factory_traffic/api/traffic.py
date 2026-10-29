from typing import Annotated, cast

from fastapi import APIRouter, Depends, Query, Request, Response

from factory_traffic.api.schemas import (
    AuditEntryResponse,
    ErrorResponse,
    EventResponse,
    HistoryResponse,
    JunctionCreateRequest,
    JunctionId,
    JunctionResponse,
    JunctionStatusResponse,
    SensorEventRequest,
)
from factory_traffic.api.status import status_response
from factory_traffic.application.traffic import TrafficService

router = APIRouter(
    responses={
        404: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    }
)


def get_service(request: Request) -> TrafficService:
    return cast(TrafficService, request.app.state.traffic_service)


Service = Annotated[TrafficService, Depends(get_service)]


@router.get("/junctions", tags=["Junctions"])
def list_junctions(service: Service) -> list[JunctionResponse]:
    return [JunctionResponse.from_record(record) for record in service.list_junctions()]


@router.post(
    "/junctions", status_code=201, tags=["Junctions"], responses={409: {"model": ErrorResponse}}
)
def create_junction(body: JunctionCreateRequest, service: Service) -> JunctionResponse:
    return JunctionResponse.from_record(service.create_junction(body.to_domain()))


@router.get("/junctions/{junction_id}", tags=["Junctions"])
def get_junction(junction_id: JunctionId, service: Service) -> JunctionResponse:
    return JunctionResponse.from_record(service.get_junction(junction_id))


@router.get("/junctions/{junction_id}/status", tags=["Junctions"])
def get_status(junction_id: JunctionId, service: Service) -> JunctionStatusResponse:
    return status_response(service.get_snapshot(junction_id))


@router.post(
    "/sensor-events",
    tags=["Sensor events"],
    responses={404: {"model": EventResponse}, 409: {"model": EventResponse}},
)
def submit_sensor_event(
    body: SensorEventRequest, service: Service, response: Response
) -> EventResponse:
    receipt = service.process_sensor_event(body.to_domain())
    response.status_code = receipt.status_code
    return EventResponse.from_receipt(receipt)


@router.get("/junctions/{junction_id}/history", tags=["History"])
def get_history(
    junction_id: JunctionId,
    service: Service,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    before_id: Annotated[int | None, Query(ge=1)] = None,
) -> HistoryResponse:
    entries, next_id = service.get_history(junction_id, limit, before_id)
    return HistoryResponse(
        items=[AuditEntryResponse.model_validate(entry) for entry in entries],
        next_before_id=next_id,
    )
