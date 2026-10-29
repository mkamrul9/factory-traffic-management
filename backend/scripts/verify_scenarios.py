"""Exercise the nine assessment scenarios against a disposable Uvicorn process.

Run with the backend's installed Python. Only the standard library is used by
this driver; the child process uses the installed FastAPI application. Default
30/5/2-second signal timings are retained, so the run takes about 90 seconds.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic, sleep
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener
from uuid import uuid4

Json = dict[str, Any]


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


class DemoServer:
    def __init__(self, directory: Path) -> None:
        self.database = directory / "assessment.sqlite3"
        self.log = directory / "server.log"
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            self.port = reservation.getsockname()[1]
        self.base = f"http://127.0.0.1:{self.port}/api"
        self.process: subprocess.Popen[bytes] | None = None

    def start(self) -> None:
        environment = {
            **{
                key: value
                for key, value in os.environ.items()
                if key not in {"DATABASE_URL", "FACTORY_TRAFFIC_REQUIRE_POSTGRES"}
            },
            "FACTORY_TRAFFIC_DB": str(self.database),
        }
        with self.log.open("ab") as output:
            self.process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "factory_traffic.main:app",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(self.port),
                    "--no-access-log",
                    "--log-level",
                    "warning",
                ],
                cwd=Path(__file__).resolve().parents[1],
                env=environment,
                stdout=output,
                stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        deadline = monotonic() + 15
        while monotonic() < deadline:
            require(self.process.poll() is None, "The disposable backend exited during startup.")
            try:
                self.request("GET", "/health")
                require(self.process.poll() is None, "The disposable backend failed to bind.")
                return
            except (URLError, TimeoutError, ConnectionError):
                sleep(0.05)
        raise AssertionError("The disposable backend did not become ready within 15 seconds.")

    def stop(self) -> None:
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)

    def request(
        self, method: str, path: str, body: Json | None = None, code: int | tuple[int, ...] = 200
    ) -> Any:
        data = None if body is None else json.dumps(body).encode()
        request = Request(
            self.base + path, data=data, method=method, headers={"Content-Type": "application/json"}
        )
        # Loopback traffic must not be routed through a machine's HTTP proxy.
        try:
            with build_opener(ProxyHandler({})).open(request, timeout=5) as response:
                actual_code, content = response.status, response.read()
        except HTTPError as error:
            actual_code, content = error.code, error.read()
        result = json.loads(content)
        expected = (code,) if isinstance(code, int) else code
        require(
            actual_code in expected,
            f"{method} {path}: expected {expected}, got {actual_code}: {result}",
        )
        return result

    def status(self, junction: str) -> Json:
        state: Json = self.request("GET", f"/junctions/{junction}/status")
        for field in ("desired_signals", "actual_signals"):
            signals = state[field]
            north_south = any(signals[d] in ("GREEN", "YELLOW") for d in ("NORTH", "SOUTH"))
            east_west = any(signals[d] in ("GREEN", "YELLOW") for d in ("EAST", "WEST"))
            require(not (north_south and east_west), f"Conflicting movements in {field}: {state}")
        require(all(n >= 0 for n in state["queues"].values()), "A queue became negative.")
        require(
            sum(state["queues"].values()) == len(state["queued_vehicles"]),
            "Queue counts and waiting vehicle records disagree.",
        )
        return state

    def wait(
        self,
        junction: str,
        predicate: Callable[[Json], bool],
        *,
        timeout: float = 12,
        stages: set[str] | None = None,
    ) -> Json:
        deadline = monotonic() + timeout
        while True:
            state = self.status(junction)
            if stages is not None:
                stages.add(state["stage"])
            if predicate(state):
                return state
            if monotonic() >= deadline:
                raise AssertionError(f"Timed out waiting for {junction}: {state}")
            sleep(0.05)

    def green(self, junction: str, direction: str, *, timeout: float = 12) -> Json:
        return self.wait(
            junction,
            lambda s: s["actual_signals"][direction] == "GREEN" and not s["awaiting_confirmation"],
            timeout=timeout,
        )

    def create(self, junction: str) -> None:
        self.request("POST", "/junctions", {"junction_id": junction, "name": junction}, code=201)

    def simulator(self, junction: str, enabled: bool = True) -> None:
        self.request("POST", f"/junctions/{junction}/simulator", {"auto_ack": enabled})

    def ready(self, junction: str) -> Json:
        self.simulator(junction)
        return self.wait(junction, lambda s: s["control_active"])

    def command(self, junction: str, command: str, direction: str | None = None) -> Json:
        body = {"command": command, **({"direction": direction} if direction else {})}
        return self.request("POST", f"/junctions/{junction}/commands", body)

    def arrival(self, junction: str, direction: str, kind: str = "TRUCK") -> Json:
        previous = self.status(junction)["sequence_numbers"][direction]
        event = {
            "event_id": f"event-{uuid4().hex}",
            "junction_id": junction,
            "direction": direction,
            "event_type": "VEHICLE_ARRIVED",
            "vehicle_id": f"vehicle-{uuid4().hex}",
            "vehicle_type": kind,
            "sequence_no": 0 if previous is None else previous + 1,
            "timestamp": datetime.now(UTC).isoformat(),
        }
        self.request("POST", "/sensor-events", event)
        return event

    def clear(self, arrival: Json) -> None:
        junction, direction = arrival["junction_id"], arrival["direction"]
        self.request(
            "POST",
            "/sensor-events",
            {
                **arrival,
                "event_id": f"clear-{uuid4().hex}",
                "event_type": "VEHICLE_CLEARED",
                "sequence_no": self.status(junction)["sequence_numbers"][direction] + 1,
                "timestamp": datetime.now(UTC).isoformat(),
            },
        )

    def switch(self, junction: str, direction: str, *, timeout: float = 12) -> Json:
        stages: set[str] = set()
        state = self.wait(
            junction,
            lambda s: s["actual_signals"][direction] == "GREEN" and not s["awaiting_confirmation"],
            timeout=timeout,
            stages=stages,
        )
        require({"YELLOW", "ALL_RED", "GREEN"} <= stages, f"Incomplete switch trace: {stages}")
        return state

    def history(self, junction: str) -> list[Json]:
        entries: list[Json] = []
        path = f"/junctions/{junction}/history?limit=100"
        while True:
            page = self.request("GET", path)
            entries.extend(page["items"])
            cursor = page["next_before_id"]
            if cursor is None:
                return entries
            path = f"/junctions/{junction}/history?limit=100&before_id={cursor}"


def normal(server: DemoServer) -> None:
    junction = "demo-normal"
    server.create(junction)
    north = [server.arrival(junction, "NORTH", "EMPLOYEE_VEHICLE") for _ in range(2)]
    server.arrival(junction, "EAST", "EMPLOYEE_VEHICLE")
    server.ready(junction)
    first = server.green(junction, "NORTH")
    green_deadline = datetime.fromisoformat(first["next_transition_at"])
    for event in north:
        server.clear(event)
    yellow = server.wait(junction, lambda s: s["stage"] == "YELLOW", timeout=35)
    require(
        datetime.fromisoformat(yellow["server_time"]) >= green_deadline,
        "Normal GREEN ended before its configured deadline.",
    )
    server.switch(junction, "EAST")
    require(server.status(junction)["queues"]["EAST"] == 1, "GREEN incorrectly cleared a vehicle.")


def priority(server: DemoServer) -> None:
    junction = "demo-priority"
    server.create(junction)
    for _ in range(2):
        server.arrival(junction, "NORTH", "EMPLOYEE_VEHICLE")
    server.arrival(junction, "EAST", "TRUCK")
    server.ready(junction)
    state = server.green(junction, "EAST")
    require(state["queues"]["NORTH"] == 2, "The priority comparison lost employee vehicles.")


def emergency(server: DemoServer) -> None:
    junction = "demo-emergency"
    server.create(junction)
    server.ready(junction)
    server.arrival(junction, "NORTH")
    server.green(junction, "NORTH")
    server.command(junction, "MANUAL_GREEN_REQUEST", "NORTH")
    ambulance = server.arrival(junction, "EAST", "EMERGENCY")
    state = server.status(junction)
    require(
        state["mode"] == "EMERGENCY" and state["manual_expires_at"] is None,
        "Emergency lost priority.",
    )
    server.switch(junction, "EAST")
    server.clear(ambulance)
    require(
        server.status(junction)["mode"] == "AUTOMATIC",
        "Emergency completion did not release control.",
    )


def manual(server: DemoServer) -> None:
    junction = "demo-manual"
    server.create(junction)
    server.ready(junction)
    server.arrival(junction, "NORTH")
    server.green(junction, "NORTH")
    server.command(junction, "MANUAL_GREEN_REQUEST", "WEST")
    state = server.switch(junction, "WEST")
    require(state["mode"] == "MANUAL", "Manual mode was not retained during execution.")
    server.command(junction, "RETURN_TO_AUTOMATIC")
    require(server.status(junction)["mode"] == "AUTOMATIC", "Return to automatic failed.")


def duplicate(server: DemoServer) -> None:
    server.create("demo-duplicate")
    event = server.arrival("demo-duplicate", "NORTH")
    retry = server.request("POST", "/sensor-events", event)
    require(retry["duplicate"] is True, "The duplicate receipt was not recognized.")
    require(
        server.status("demo-duplicate")["queues"]["NORTH"] == 1, "A duplicate changed the queue."
    )


def clearance(server: DemoServer) -> None:
    server.create("demo-clearance")
    event = server.arrival("demo-clearance", "SOUTH", "FORKLIFT")
    server.clear(event)
    require(
        server.status("demo-clearance")["queues"]["SOUTH"] == 0, "The clearance was not applied."
    )


def failure(server: DemoServer) -> None:
    junction = "demo-failure"
    server.create(junction)
    server.ready(junction)
    server.arrival(junction, "NORTH")
    server.green(junction, "NORTH")
    server.simulator(junction, False)
    server.command(junction, "MANUAL_GREEN_REQUEST", "WEST")
    server.wait(junction, lambda s: any(a["code"] == "ACK_TIMEOUT" for a in s["alerts"]))
    state = server.status(junction)
    require(
        state["mode"] == "DEGRADED" and not state["control_active"], "Timeout did not stop control."
    )
    require(set(state["desired_signals"].values()) == {"RED"}, "Timeout did not request safe RED.")
    require(
        set(state["actual_signals"].values()) == {"UNKNOWN"},
        "Missing ACKs were treated as evidence.",
    )
    for status in ("OFFLINE", "ONLINE"):
        server.request(
            "POST",
            "/device-events",
            {
                "event_id": f"device-{uuid4().hex}",
                "junction_id": junction,
                "device_type": "SIGNAL_CONTROLLER",
                "status": status,
                "timestamp": datetime.now(UTC).isoformat(),
            },
        )
        if status == "OFFLINE":
            require(
                server.status(junction)["controller_status"] == "OFFLINE",
                "Offline report was lost.",
            )
    server.ready(junction)


def restart(server: DemoServer) -> None:
    junction = "demo-restart"
    server.create(junction)
    server.ready(junction)
    event = server.arrival(junction, "NORTH")
    server.green(junction, "NORTH")
    server.simulator(junction, False)
    server.command(junction, "MANUAL_GREEN_REQUEST", "WEST")
    previous = server.status(junction)
    history_id = server.history(junction)[0]["id"]
    server.stop()
    server.start()
    state = server.status(junction)
    require(state["queues"] == previous["queues"], "Restart lost a queue.")
    require(
        state["sequence_numbers"] == previous["sequence_numbers"], "Restart lost sequence cursors."
    )
    require(
        state["mode"] == "DEGRADED" and not state["control_active"], "Restart resumed old control."
    )
    require(
        state["manual_expires_at"] is None and not state["simulator_auto_ack"],
        "Unsafe restart settings.",
    )
    require(
        set(state["actual_signals"].values()) == {"UNKNOWN"}, "Restart trusted old physical state."
    )
    require(state["batch_id"] != previous["batch_id"], "Restart reused a controller batch.")
    require(
        any(e["id"] == history_id for e in server.history(junction)), "Restart lost audit history."
    )
    require(
        server.request("POST", "/sensor-events", event)["duplicate"], "Restart lost event receipts."
    )
    old = previous["pending_commands"][0]
    rejected = server.request(
        "POST",
        "/controller-events",
        {
            "junction_id": junction,
            "command_id": old["command_id"],
            "status": "ACK",
            "actual_state": old["requested_state"],
        },
        code=409,
    )
    require(rejected["code"] == "STALE_ACK", "Restart accepted an interrupted command.")
    server.ready(junction)
    server.green(junction, "NORTH")


def concurrent(server: DemoServer) -> None:
    junction = "demo-concurrent"
    server.create(junction)
    initial = server.status(junction)
    server.request(
        "POST", f"/junctions/{junction}/simulator/acknowledge", {"batch_id": initial["batch_id"]}
    )
    server.wait(junction, lambda s: s["control_active"])
    first = server.arrival(junction, "NORTH")
    pending = server.status(junction)["pending_commands"]
    north_command = next(command for command in pending if command["direction"] == "NORTH")
    truck = {
        **first,
        "event_id": "concurrent-truck",
        "vehicle_id": "truck-2",
        "sequence_no": first["sequence_no"] + 1,
    }
    ambulance = {
        **first,
        "event_id": "concurrent-emergency",
        "vehicle_id": "ambulance",
        "vehicle_type": "EMERGENCY",
        "direction": "EAST",
        "sequence_no": 0,
    }
    requests = [
        ("/sensor-events", truck),
        ("/sensor-events", ambulance),
        (
            f"/junctions/{junction}/commands",
            {"command": "MANUAL_GREEN_REQUEST", "direction": "WEST"},
        ),
        ("/sensor-events", ambulance),
        (
            "/controller-events",
            {
                "junction_id": junction,
                "command_id": north_command["command_id"],
                "status": "ACK",
                "actual_state": "GREEN",
            },
        ),
    ]

    def submit(item: tuple[str, Json]) -> Json:
        path, body = item
        # Either order is valid: manual intent accepted before the emergency, or rejected after it.
        return server.request(
            "POST", path, body, code=(200, 409) if path.endswith("/commands") else 200
        )

    with ThreadPoolExecutor(max_workers=5) as executor:
        results = list(executor.map(submit, requests))
    require(
        sum(bool(r.get("duplicate")) for r in results) == 1, "Concurrent retry was applied twice."
    )
    state = server.status(junction)
    require(
        state["queues"]["NORTH"] == 2 and state["queues"]["EAST"] == 1,
        "Concurrent queues disagree.",
    )
    require(
        state["mode"] == "EMERGENCY" and state["manual_expires_at"] is None,
        "Manual intent overrode emergency.",
    )
    server.simulator(junction)
    server.switch(junction, "EAST")


def main() -> int:
    scenarios = (
        normal,
        priority,
        emergency,
        manual,
        duplicate,
        clearance,
        failure,
        restart,
        concurrent,
    )
    with TemporaryDirectory(prefix="factory-traffic-assessment-") as directory:
        server = DemoServer(Path(directory))
        print(
            "Nine live HTTP scenarios; disposable database and server; default signal timings.",
            flush=True,
        )
        try:
            server.start()
            for index, scenario in enumerate(scenarios, 1):
                started = monotonic()
                print(f"[{index}/9] {scenario.__name__} ...", flush=True)
                scenario(server)
                print(f"      PASS ({monotonic() - started:.1f}s)", flush=True)
        except Exception as error:
            print(f"FAIL: {error}", file=sys.stderr)
            server.stop()
            print(server.log.read_text(encoding="utf-8", errors="replace")[-8000:], file=sys.stderr)
            return 1
        finally:
            server.stop()
    print("All nine scenarios passed. The disposable server and database have been removed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
