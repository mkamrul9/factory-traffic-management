from typing import Annotated, cast

from fastapi import APIRouter, Depends, Query, Request, Response

from factory_traffic.api.schemas import (
    AlertResponse,
    AuditEntryResponse,
    ErrorResponse,
    EventResponse,
    HistoryResponse,
    JunctionCreateRequest,
    JunctionId,
    JunctionResponse,
    JunctionStatusResponse,
    SensorEventRequest,
    WaitingVehicleResponse,
)
from factory_traffic.application.traffic import TrafficService
from factory_traffic.domain.events import VehicleType
from factory_traffic.domain.models import Direction
from factory_traffic.domain.signals import all_red_signals

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
    waiting = service.get_waiting(junction_id)
    queues = {direction: 0 for direction in Direction}
    for vehicle in waiting:
        queues[vehicle.direction] += 1
    emergency_directions = {
        vehicle.direction for vehicle in waiting if vehicle.vehicle_type == VehicleType.EMERGENCY
    }
    return JunctionStatusResponse(
        junction_id=junction_id,
        desired_signals=all_red_signals(),
        actual_signals={direction: "UNKNOWN" for direction in Direction},
        queues=queues,
        queued_vehicles=[WaitingVehicleResponse.model_validate(vehicle) for vehicle in waiting],
        emergency_directions=[
            direction for direction in Direction if direction in emergency_directions
        ],
        alerts=[
            AlertResponse(
                code="CONTROL_NOT_READY",
                message="Traffic engine and controller confirmation are not active yet.",
            )
        ],
    )


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
