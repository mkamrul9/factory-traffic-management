from factory_traffic.api.schemas import (
    AlertResponse,
    ControllerCommandResponse,
    JunctionStatusResponse,
    WaitingVehicleResponse,
)
from factory_traffic.application.ports import JunctionSnapshot
from factory_traffic.domain.controller import DeviceStatus
from factory_traffic.domain.engine import TrafficEngine
from factory_traffic.domain.models import Direction


def status_response(snapshot: JunctionSnapshot) -> JunctionStatusResponse:
    runtime = snapshot.runtime
    engine = TrafficEngine(snapshot.configuration)
    desired = engine.desired_signals(runtime.engine)
    queues = {direction: 0 for direction in Direction}
    for vehicle in snapshot.waiting:
        queues[vehicle.direction] += 1
    emergency_directions = {
        v.direction for v in engine.active_emergencies(snapshot.waiting, snapshot.timestamp)
    }
    alerts = []
    if runtime.fault_code:
        alerts.append(
            AlertResponse(
                code=runtime.fault_code, message=runtime.fault_message or "Control stopped."
            )
        )
    if runtime.controller_status != DeviceStatus.ONLINE:
        alerts.append(
            AlertResponse(
                code=f"CONTROLLER_{runtime.controller_status}",
                message="Controller availability is not confirmed ONLINE.",
            )
        )
    for kind, devices in (("SENSOR", runtime.sensors), ("SIGNAL", runtime.signals)):
        for direction, status in devices.items():
            if status != DeviceStatus.ONLINE:
                alerts.append(
                    AlertResponse(
                        code=f"{kind}_{status}", message=f"{direction} {kind.lower()} is {status}."
                    )
                )
    if any(value is None for value in runtime.actual_signals.values()):
        alerts.append(
            AlertResponse(
                code="UNKNOWN_PHYSICAL_STATE",
                message="Some physical states await fresh confirmation.",
            )
        )
    if any(
        value is not None and value != desired[direction]
        for direction, value in runtime.actual_signals.items()
    ):
        alerts.append(
            AlertResponse(
                code="DESIRED_ACTUAL_MISMATCH",
                message="Desired and confirmed signal states differ.",
            )
        )
    manual = runtime.engine.manual_override
    return JunctionStatusResponse(
        junction_id=runtime.engine.junction_id,
        mode="DEGRADED" if runtime.fault_code else runtime.engine.mode.value,
        phase=runtime.engine.phase_id or "ALL_RED",
        stage=runtime.engine.stage,
        target_phase_id=runtime.engine.target_phase_id,
        control_active=runtime.fault_code is None,
        controller_status=runtime.controller_status,
        desired_signals=desired,
        actual_signals={
            direction: value if value is not None else "UNKNOWN"
            for direction, value in runtime.actual_signals.items()
        },
        queues=queues,
        queued_vehicles=[WaitingVehicleResponse.model_validate(v) for v in snapshot.waiting],
        emergency_directions=[d for d in Direction if d in emergency_directions],
        alerts=alerts,
        awaiting_confirmation=runtime.engine.awaiting_confirmation,
        next_transition_at=runtime.engine.deadline
        if runtime.fault_code is None or runtime.recovering
        else None,
        manual_expires_at=manual.expires_at if manual else None,
        batch_id=runtime.batch_id,
        pending_commands=[
            ControllerCommandResponse.model_validate(c) for c in snapshot.pending_commands
        ],
        simulator_auto_ack=runtime.simulator_auto_ack,
        sensor_statuses=runtime.sensors,
        signal_statuses=runtime.signals,
        sequence_numbers=snapshot.sequence_numbers,
        server_time=snapshot.timestamp,
    )
