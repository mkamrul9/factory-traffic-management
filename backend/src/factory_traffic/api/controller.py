from fastapi import APIRouter, Response

from factory_traffic.api.schemas import (
    ControllerCommandResponse,
    ControllerEventRequest,
    DeviceEventRequest,
    ErrorResponse,
    EventResponse,
    JunctionId,
    OperationResponse,
    OperatorCommandRequest,
    SimulatorBatchRequest,
    SimulatorSettingsRequest,
)
from factory_traffic.api.traffic import Service

router = APIRouter(
    responses={
        404: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    }
)


@router.post(
    "/junctions/{junction_id}/commands",
    tags=["Control"],
    responses={409: {"model": OperationResponse}},
)
def operator_command(
    junction_id: JunctionId, body: OperatorCommandRequest, service: Service, response: Response
) -> OperationResponse:
    result = service.execute_command(junction_id, body.command, body.direction)
    response.status_code = result.status_code
    return OperationResponse.model_validate(result)


@router.get("/junctions/{junction_id}/controller-commands", tags=["Controllers"])
def pending_commands(junction_id: JunctionId, service: Service) -> list[ControllerCommandResponse]:
    return [
        ControllerCommandResponse.model_validate(item)
        for item in service.pending_commands(junction_id)
    ]


@router.post(
    "/controller-events",
    tags=["Controllers"],
    responses={404: {"model": OperationResponse}, 409: {"model": OperationResponse}},
)
def controller_event(
    body: ControllerEventRequest, service: Service, response: Response
) -> OperationResponse:
    result = service.process_acknowledgement(body.to_domain())
    response.status_code = result.status_code
    return OperationResponse.model_validate(result)


@router.post(
    "/device-events",
    tags=["Devices"],
    responses={404: {"model": EventResponse}, 409: {"model": EventResponse}},
)
def device_event(body: DeviceEventRequest, service: Service, response: Response) -> EventResponse:
    receipt = service.process_device_event(body.to_domain())
    response.status_code = receipt.status_code
    return EventResponse.from_receipt(receipt)


@router.post(
    "/junctions/{junction_id}/simulator",
    tags=["Controller simulator"],
    responses={409: {"model": OperationResponse}},
)
def configure_simulator(
    junction_id: JunctionId, body: SimulatorSettingsRequest, service: Service, response: Response
) -> OperationResponse:
    result = service.configure_simulator(junction_id, body.auto_ack)
    response.status_code = result.status_code
    return OperationResponse.model_validate(result)


@router.post(
    "/junctions/{junction_id}/simulator/acknowledge",
    tags=["Controller simulator"],
    responses={409: {"model": OperationResponse}},
)
def simulate_batch(
    junction_id: JunctionId, body: SimulatorBatchRequest, service: Service, response: Response
) -> OperationResponse:
    result = service.simulate_batch(junction_id, body.batch_id)
    response.status_code = result.status_code
    return OperationResponse.model_validate(result)
