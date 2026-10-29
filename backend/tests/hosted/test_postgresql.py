"""Adapter parity tests against an explicitly configured, disposable PostgreSQL database."""

import os
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import uuid4

import psycopg
import pytest
from api import test_controller_runtime as controller_scenarios
from api import test_devices as device_scenarios
from api import test_sensor_events as sensor_scenarios
from conftest import NOW, sensor_event
from control_helpers import ControlHarness, FakeClock
from fastapi.testclient import TestClient
from psycopg import sql

from factory_traffic.api.app import create_app
from factory_traffic.application.ports import AuditEntry
from factory_traffic.domain.models import Direction
from factory_traffic.persistence.postgresql import PostgreSQLRepository, PostgreSQLTransaction


@pytest.fixture
def postgres_url() -> Iterator[str]:
    database_url = os.environ.get("FACTORY_TRAFFIC_TEST_POSTGRES")
    if not database_url:
        pytest.skip("Set FACTORY_TRAFFIC_TEST_POSTGRES to run PostgreSQL adapter tests.")
    schema = "assessment_test_" + uuid4().hex
    with psycopg.connect(database_url, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    parts = urlsplit(database_url)
    parameters = dict(parse_qsl(parts.query))
    parameters["options"] = f"-csearch_path={schema}"
    isolated_url = urlunsplit(parts._replace(query=urlencode(parameters)))
    try:
        yield isolated_url
    finally:
        with psycopg.connect(database_url, autocommit=True) as connection:
            connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


@pytest.fixture
def postgres_client(postgres_url: str) -> Iterator[TestClient]:
    with TestClient(
        create_app(database_url=postgres_url, clock=lambda: NOW, run_background_tasks=False)
    ) as client:
        yield client


@pytest.mark.parametrize(
    "scenario",
    [
        controller_scenarios.test_outbox_has_unique_ids_and_startup_needs_all_four_confirmations,
        controller_scenarios.test_normal_scheduling_and_manual_controls_run_through_the_api,
        controller_scenarios.test_emergency_preempts_manual_and_duplicate_detection_does_not_repeat_commands,
        controller_scenarios.test_late_acknowledgement_cannot_use_its_sensor_timestamp_to_avoid_timeout,
        controller_scenarios.test_duplicate_old_ack_cannot_overwrite_a_newer_physical_observation,
        controller_scenarios.test_enabling_simulator_recovers_and_acknowledges_each_committed_request,
        device_scenarios.test_controller_offline_cannot_acknowledge_or_be_silently_enabled,
        device_scenarios.test_additional_faults_preserve_best_effort_red_commands,
    ],
    ids=lambda scenario: scenario.__name__.removeprefix("test_"),
)
def test_controller_scenario_parity(
    postgres_url: str, scenario: Callable[[ControlHarness], None]
) -> None:
    clock = FakeClock()
    with TestClient(
        create_app(database_url=postgres_url, clock=clock, run_background_tasks=False)
    ) as client:
        scenario(ControlHarness(client, clock))


@pytest.mark.parametrize(
    "scenario",
    [
        sensor_scenarios.test_arrival_clearance_and_retries_change_the_queue_once,
        sensor_scenarios.test_reusing_event_id_with_changed_content_is_a_conflict,
        sensor_scenarios.test_equivalent_utc_timestamp_is_a_logical_duplicate,
    ],
    ids=lambda scenario: scenario.__name__.removeprefix("test_"),
)
def test_sensor_scenario_parity(
    postgres_client: TestClient, scenario: Callable[[TestClient], None]
) -> None:
    scenario(postgres_client)


def test_dispatch_reads_committed_outbox_without_confirming_lights(postgres_url: str) -> None:
    clock = FakeClock()
    with TestClient(
        create_app(database_url=postgres_url, clock=clock, run_background_tasks=False)
    ) as client:
        control = ControlHarness(client, clock)
        status = control.status()
        assert status["simulator_auto_ack"] is False

        class DeferredController:
            def execute(self, commands, now):
                with psycopg.connect(postgres_url) as connection:
                    for command in commands:
                        assert connection.execute(
                            "SELECT status FROM controller_commands WHERE command_id = %s",
                            (command.command_id,),
                        ).fetchone() == ("PENDING",)
                return ()

        client.app.state.traffic_service.controller = DeferredController()
        response = client.post(
            "/api/junctions/A/simulator/acknowledge", json={"batch_id": status["batch_id"]}
        )
        assert response.status_code == 200
        assert response.json()["code"] == "COMMANDS_DISPATCHED"
        assert control.status()["awaiting_confirmation"] is True
        assert set(control.status()["actual_signals"].values()) == {"UNKNOWN"}


def test_restart_retains_data_and_rejects_interrupted_ack(postgres_url: str) -> None:
    clock = FakeClock()
    with TestClient(
        create_app(database_url=postgres_url, clock=clock, run_background_tasks=False)
    ) as client:
        control = ControlHarness(client, clock)
        arrival = control.green()
        client.post(
            "/api/junctions/A/commands",
            json={"command": "MANUAL_GREEN_REQUEST", "direction": "WEST"},
        )
        previous = control.status()
        old_command = previous["pending_commands"][0]
        old_history = client.get("/api/junctions/A/history?limit=100").json()["items"]
    with TestClient(
        create_app(database_url=postgres_url, clock=clock, run_background_tasks=False)
    ) as client:
        control = ControlHarness(client, clock)
        state = control.status()
        assert state["queues"] == previous["queues"]
        assert state["sequence_numbers"] == previous["sequence_numbers"]
        assert state["mode"] == "DEGRADED"
        assert state["manual_expires_at"] is None
        assert state["simulator_auto_ack"] is False
        assert set(state["actual_signals"].values()) == {"UNKNOWN"}
        assert state["batch_id"] != previous["batch_id"]
        assert client.post("/api/sensor-events", json=arrival).json()["duplicate"] is True
        history_ids = {
            item["id"] for item in client.get("/api/junctions/A/history?limit=100").json()["items"]
        }
        assert {item["id"] for item in old_history} <= history_ids
        rejected = client.post(
            "/api/controller-events",
            json={
                "junction_id": "A",
                "command_id": old_command["command_id"],
                "status": "ACK",
                "actual_state": old_command["requested_state"],
            },
        )
        assert rejected.status_code == 409
        assert rejected.json()["code"] == "STALE_ACK"
        control.acknowledge()
        assert control.tick(1.999)["mode"] == "DEGRADED"
        control.tick(0.001)
        assert control.acknowledge()["actual_signals"]["NORTH"] == "GREEN"


def test_parallel_global_event_id_is_applied_once(postgres_url: str) -> None:
    with (
        TestClient(
            create_app(database_url=postgres_url, clock=lambda: NOW, run_background_tasks=False)
        ) as first,
        TestClient(
            create_app(database_url=postgres_url, clock=lambda: NOW, run_background_tasks=False)
        ) as second,
    ):
        clients = (first, second)

        def submit(index: int) -> dict:
            response = clients[index % 2].post("/api/sensor-events", json=sensor_event())
            assert response.status_code == 200
            return response.json()

        with ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(submit, range(16)))
        assert sum(not receipt["duplicate"] for receipt in results) == 1
        assert first.get("/api/junctions/A/status").json()["queues"]["NORTH"] == 1


def test_postgres_failure_rolls_back_every_event_write(
    postgres_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_audit(self: PostgreSQLTransaction, entry: AuditEntry) -> None:
        raise psycopg.OperationalError("simulated storage outage")

    with monkeypatch.context() as patch:
        patch.setattr(PostgreSQLTransaction, "add_audit", fail_audit)
        assert postgres_client.post("/api/sensor-events", json=sensor_event()).status_code == 503
    state = postgres_client.get("/api/junctions/A/status").json()
    assert state["queues"]["NORTH"] == 0
    assert state["sequence_numbers"]["NORTH"] is None
    retry = postgres_client.post("/api/sensor-events", json=sensor_event())
    assert retry.status_code == 200
    assert retry.json()["duplicate"] is False


def test_history_pagination_and_64_bit_sequences(postgres_client: TestClient) -> None:
    event = sensor_event(sequence_no=9_223_372_036_854_775_807)
    assert postgres_client.post("/api/sensor-events", json=event).status_code == 200
    first = postgres_client.get("/api/junctions/A/history?limit=2").json()
    assert first["next_before_id"] is not None
    second = postgres_client.get(
        f"/api/junctions/A/history?limit=2&before_id={first['next_before_id']}"
    ).json()
    assert {item["id"] for item in first["items"]}.isdisjoint(
        {item["id"] for item in second["items"]}
    )
    assert (
        postgres_client.get("/api/junctions/A/status").json()["sequence_numbers"]["NORTH"]
        == event["sequence_no"]
    )


def test_snapshot_reads_remain_coherent_across_writes(postgres_url: str) -> None:
    repository = PostgreSQLRepository(postgres_url)
    repository.initialize()
    # Seed through the application before reading two snapshots around a committed change.
    with TestClient(
        create_app(database_url=postgres_url, clock=lambda: NOW, run_background_tasks=False)
    ):
        with repository.transaction() as transaction:
            transaction.set_sequence("A", Direction.NORTH, 1)
        with repository.transaction(read_only=True) as snapshot:
            assert snapshot.get_sequence("A", Direction.NORTH) == 1
            with repository.transaction() as writer:
                writer.set_sequence("A", Direction.NORTH, 2)
            assert snapshot.get_sequence("A", Direction.NORTH) == 1
        with repository.transaction(read_only=True) as current:
            assert current.get_sequence("A", Direction.NORTH) == 2
    repository.close()


def test_junction_text_is_bound_as_data(postgres_client: TestClient) -> None:
    name = "Loading gate? %s"
    response = postgres_client.post("/api/junctions", json={"junction_id": "B", "name": name})
    assert response.status_code == 201
    assert postgres_client.get("/api/junctions/B").json()["name"] == name


def test_hosted_configuration_never_silently_falls_back(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("FACTORY_TRAFFIC_REQUIRE_POSTGRES", "1")
    with pytest.raises(ValueError, match="SQLite fallback is disabled"):
        create_app()
    # Explicit paths keep local tests isolated from a hosting environment.
    with TestClient(create_app(tmp_path / "local.sqlite3", run_background_tasks=False)) as client:
        assert client.get("/api/health").status_code == 200
