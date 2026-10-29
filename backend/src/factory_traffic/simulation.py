"""A deterministic domain walkthrough; no API, database, or hardware is used."""

from datetime import UTC, datetime, timedelta

from factory_traffic.domain.engine import ControlCommand, EngineResult, TrafficEngine
from factory_traffic.domain.events import VehicleType, WaitingVehicle
from factory_traffic.domain.models import DEFAULT_JUNCTION, Direction


def main() -> None:
    print("Domain simulation: confirmations are simulated. The running API is unchanged.\n")
    engine = TrafficEngine(DEFAULT_JUNCTION)
    origin = datetime(2026, 10, 29, 5, 0, tzinfo=UTC)
    now = origin
    state = engine.start(now).state
    waiting: list[WaitingVehicle] = []

    def display(label: str, result: EngineResult) -> None:
        nonlocal state
        state = result.state
        elapsed = (now - origin).total_seconds()
        confirmation = "awaiting confirmation" if state.awaiting_confirmation else "confirmed"
        phase = state.phase_id or "-"
        print(
            f"t+{elapsed:5.1f}s  {label:<30} "
            f"{state.mode:<9} {state.stage:<7} {phase:<12} {confirmation}"
        )

    def tick(seconds: float, label: str) -> None:
        nonlocal now
        now += timedelta(seconds=seconds)
        display(label, engine.tick(state, waiting, now))

    def confirm(label: str) -> None:
        display(
            label,
            engine.confirm_signals(
                state, state.signal_version, engine.desired_signals(state), waiting, now
            ),
        )

    def arrive(vehicle_id: str, direction: Direction, kind: VehicleType) -> None:
        waiting.append(
            WaitingVehicle(
                "A",
                vehicle_id,
                direction,
                kind,
                now,
                now,
                f"arrival-{vehicle_id}",
                len(waiting) + 1,
            )
        )

    display("startup all-red request", engine.start(now))
    arrive("truck", Direction.NORTH, VehicleType.TRUCK)
    confirm("simulated all-red confirmation")
    tick(2, "clearance completed")
    confirm("simulated north/south green")
    tick(1, "normal traffic")
    arrive("ambulance", Direction.EAST, VehicleType.EMERGENCY)
    tick(0, "emergency from EAST")
    confirm("simulated yellow confirmation")
    tick(5, "yellow interval completed")
    confirm("simulated all-red confirmation")
    tick(2, "clearance completed")
    confirm("simulated east/west green")
    waiting[:] = [vehicle for vehicle in waiting if vehicle.vehicle_id != "ambulance"]
    tick(0, "emergency vehicle cleared")
    display(
        "manual SOUTH requested",
        engine.execute_command(
            state, ControlCommand.MANUAL_GREEN_REQUEST, waiting, now, Direction.SOUTH
        ),
    )
    confirm("simulated yellow confirmation")
    tick(5, "yellow interval completed")
    confirm("simulated all-red confirmation")
    tick(2, "clearance completed")
    confirm("simulated north/south green")
    display(
        "return to automatic",
        engine.execute_command(state, ControlCommand.RETURN_TO_AUTOMATIC, waiting, now),
    )
    print("\nAll times are advanced by the simulation; no wall-clock waits are used.")


if __name__ == "__main__":
    main()
