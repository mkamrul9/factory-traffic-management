import json
from dataclasses import asdict
from datetime import datetime

from factory_traffic.domain.controller import DeviceStatus, JunctionRuntime
from factory_traffic.domain.engine import EngineState, ManualOverride, OperatingMode, SignalStage
from factory_traffic.domain.models import Direction, SignalColor
from factory_traffic.domain.scheduling import PhaseService


def _encode(value: object) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(f"Cannot encode {type(value).__name__}")


def encode_runtime(runtime: JunctionRuntime) -> str:
    return json.dumps(asdict(runtime), default=_encode)


def decode_runtime(payload: str) -> JunctionRuntime:
    data = json.loads(payload)
    state = data["engine"]
    manual = state["manual_override"]
    engine = EngineState(
        junction_id=state["junction_id"],
        mode=OperatingMode(state["mode"]),
        stage=SignalStage(state["stage"]),
        phase_id=state["phase_id"],
        target_phase_id=state["target_phase_id"],
        signal_version=state["signal_version"],
        awaiting_confirmation=state["awaiting_confirmation"],
        requested_at=datetime.fromisoformat(state["requested_at"]),
        confirmed_at=datetime.fromisoformat(state["confirmed_at"])
        if state["confirmed_at"]
        else None,
        deadline=datetime.fromisoformat(state["deadline"]) if state["deadline"] else None,
        observed_at=datetime.fromisoformat(state["observed_at"]),
        manual_override=ManualOverride(
            manual["phase_id"], datetime.fromisoformat(manual["expires_at"])
        )
        if manual
        else None,
        services=tuple(
            PhaseService(item["phase_id"], datetime.fromisoformat(item["served_at"]))
            for item in state["services"]
        ),
        emergency_event_ids=tuple(state["emergency_event_ids"]),
    )
    return JunctionRuntime(
        engine=engine,
        epoch=data["epoch"],
        actual_signals={
            Direction(key): SignalColor(value) if value else None
            for key, value in data["actual_signals"].items()
        },
        controller_status=DeviceStatus(data["controller_status"]),
        fault_code=data["fault_code"],
        fault_message=data["fault_message"],
        recovering=data["recovering"],
        batch_id=data["batch_id"],
        simulator_auto_ack=data["simulator_auto_ack"],
        sensors={Direction(key): DeviceStatus(value) for key, value in data["sensors"].items()},
        signals={Direction(key): DeviceStatus(value) for key, value in data["signals"].items()},
        device_timestamps={
            key: datetime.fromisoformat(value) for key, value in data["device_timestamps"].items()
        },
    )
