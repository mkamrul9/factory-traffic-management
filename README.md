# Factory Traffic Management System

A factory junction controller built for the CSI Smart Tech backend assessment.
The backend uses Python and FastAPI. SQLite stores vehicle queues and audit
history, and an Angular dashboard will provide the demonstration interface.

## Current status

The backend supports persistent junctions, vehicle arrivals and clearances,
idempotent sensor events, per-direction sequence ordering, and paginated audit
history. Junction A is created automatically. Domain safety checks validate
signal snapshots independently of the API and database.

The running backend now controls traffic through the domain engine, persists its
runtime state and outgoing commands, processes controller acknowledgements, and
handles timeouts, device failures, and restart recovery. Manual and emergency
intents use the same safe transition sequence as normal scheduling.

Controllers are simulated through REST. Simulation starts disabled. Startup is
DEGRADED with desired ALL RED and actual UNKNOWN until fresh confirmations and
clearance complete. Enable the explicit simulator for a continuous demonstration;
no real hardware or MQTT connection is configured. The Angular dashboard is next.

## Run locally

Requirements: Python 3.12 or newer and [uv](https://docs.astral.sh/uv/getting-started/installation/).
Development has been checked with Python 3.14 on Windows.

From the repository root:

```sh
cd backend
uv sync --extra dev --locked
uv run uvicorn factory_traffic.main:app --reload --host 127.0.0.1 --port 8000
```

Open [API documentation](http://127.0.0.1:8000/docs), or request
[GET /api/health](http://127.0.0.1:8000/api/health). The health endpoint reports
process liveness; it makes no claim about controller availability or traffic safety.

On startup, migrations run automatically and create the database at
`backend/data/factory-traffic.sqlite3` when launched from `backend/`. Set the
`FACTORY_TRAFFIC_DB` environment variable to use another database path. Relative
paths are resolved from the process working directory. The database and its WAL
files are local runtime data and are excluded from Git. Use one Uvicorn worker.

To enable the REST controller simulation, run in PowerShell:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/junctions/A/simulator `
    -Method Post -ContentType "application/json" -Body '{"auto_ack":true}'
```

The simulator confirms a fresh ALL RED batch, then the backend observes the
two-second clearance before automatic control becomes active. Without simulation
or explicit controller ACKs, the default three-second command deadline will fail
safely. See [controller demonstration](docs/controller-demo.md) and
[API examples](docs/api.md) for the rest of the walkthrough.

If uv is unavailable, use a virtual environment and pip instead:

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[dev]"
.\.venv\Scripts\python -m uvicorn factory_traffic.main:app --reload
```

The pip alternative uses the dependency ranges in `pyproject.toml`; uv uses the
exact dependency versions recorded in `uv.lock`. On Linux/macOS, the virtual
environment's Python executable is `.venv/bin/python`.

## Run the traffic engine walkthrough

From `backend/`:

```sh
uv run python -m factory_traffic.simulation
```

This advances a simulated clock through normal traffic, emergency preemption,
manual control, and return to automatic scheduling. Confirmations are explicitly
simulated. It does not change the database or running API, and it does not wait
for real time to pass. See [traffic-control rules](docs/traffic-control.md) for the
algorithm, transition timing, and operator policies.

## Check the backend

Run from `backend/`:

```sh
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy
```

The safety tests enumerate every combination of the four physical signals and
check for conflicting movements and overlapping GREEN/YELLOW requests. These
are snapshot checks. Engine tests verify confirmation gates, full clearance
intervals, normal scheduling, starvation protection, manual control, and emergency
handling. Three deterministic mixed-event runs exercise 1,000 actions each while
checking every emitted signal request. API integration tests also cover input
validation, transactional rollback, concurrent events across service instances,
partial/late/failed ACKs, device reconnects, actual/desired separation, migration
from the earlier schema, and restart during every signal stage. API tests use
temporary databases; domain tests need no database.

## Project layout

```text
backend/
  src/factory_traffic/
    api/             HTTP routes and response models
    application/     Junction/event use cases and repository interfaces
    controllers/     REST simulator implementing the controller port
    domain/          Configuration, scheduling, and the traffic state machine
    persistence/     SQLite adapter and versioned SQL migrations
    main.py          ASGI entry point
    simulation.py    Deterministic domain walkthrough
  tests/
    api/             Request, concurrency, and persistence scenarios
    domain/          Signal, scheduling, and engine tests independent of HTTP
docs/
  api.md             Endpoint reference and runnable request examples
  traffic-control.md Scheduling, transition, and operator policies
  controller-demo.md Live REST demonstration and recovery instructions
  architecture.md    Boundaries, safety decisions, and persistence design
  implementation-plan.md
```

The Angular `frontend/` will be added in the next checkpoint.
The domain package imports only the Python standard library. Schema definitions
are in `backend/src/factory_traffic/persistence/migrations/`.

## Major architectural decisions

- **FastAPI:** request validation and generated OpenAPI documentation at the API
  boundary. HTTP handlers call application services rather than decide signals.
- **Pure Python domain:** traffic decisions remain testable without a running web
  server, database, or controller. Compatible directions come from configuration.
- **SQLite:** a local transactional database keeps assessment setup small. Each
  event commits its queue mutation, receipt, sequence cursor, and audit together.
  `BEGIN IMMEDIATE` and unique keys protect concurrent writes, including global
  event IDs. Per-junction locks provide the application serialization boundary.
  The control runtime uses one process; database write serialization alone
  does not coordinate timer ownership or controller delivery across workers.
- **REST controller adapter:** unique command and batch IDs, connection epochs,
  and acknowledgements separate desired signals from physical confirmation.
  Commands are committed before dispatch through the controller port. MQTT can
  later call the same application acknowledgement/device interfaces.
- **Background control:** one nonblocking task ticks all junctions every 100 ms.
  A monotonic clock supplies elapsed intervals; request handlers never sleep to
  advance traffic signals.
- **Angular, planned:** the dashboard will poll the API and send operator intents.
  It will display backend decisions rather than run its own signal timers.

See [architecture notes](docs/architecture.md) for the intended control flow and
[implementation plan](docs/implementation-plan.md) for review checkpoints.

## Assumptions / Questions / Requirement Issues

The backend implements the policies below. Physical execution is represented by
the REST simulator or explicit controller reports; no hardware is connected.

- **Compatible movements:** NORTH/SOUTH and EAST/WEST are the two groups for
  Junction A. Turning movements and pedestrians are outside this model. Each
  direction belongs to exactly one configured group; configuration declares
  which movements are physically compatible.
- **Signal timing:** use 30 seconds GREEN, 5 seconds YELLOW, and 2 seconds ALL RED.
  The all-red interval resolves the unspecified clearance duration. Timers start
  after the relevant controller confirmations, rather than when a request is sent.
- **Safety precedence:** failure/recovery takes precedence over emergencies,
  emergencies over manual control, and manual control over automatic scheduling.
  All operational changes must use the same safe transition logic.
- **Event identity and ordering:** persist `event_id` for idempotency. Reusing an
  ID with different content is a conflict. With no sensor ID in the specification,
  sequence numbers are assumed to belong to a junction/direction stream. Stale
  or equal sequence numbers are recorded as IGNORED without changing queues.
  A fresh, well-formed event advances the stream even if its queue operation is
  rejected, such as an unmatched clearance. This prevents an older arrival from
  reviving a vehicle already reported cleared. Events are not buffered or reordered;
  missed events need reconciliation. Real deployments need a sensor identity and
  a sequence-reset/reconciliation policy.
- **Time and queues:** server receipt time determines waiting duration; sensor
  time is retained for diagnostics. Non-emergency events may arrive late when their
  sequence is fresh. Timestamps over 30 seconds in the future and emergency arrivals
  over 120 seconds old are rejected without advancing the sequence cursor. An
  unmatched, wrong-direction, or wrong-type clearance is rejected and audited.
  Vehicle IDs identify waiting vehicles within a junction; repeated arrivals cannot
  create multiple waiting entries for that vehicle. Clearances may omit vehicle type.
- **Retry semantics:** event IDs are globally unique. Equivalent UTC timestamps
  count as the same payload. Validly shaped requests retain their original outcome,
  including rejection, across retries and restarts. Creating a previously unknown
  junction does not change an old event's rejection; submit a new event ID. Malformed
  requests are audited but do not reserve IDs. Unidentifiable requests are retained
  in global audit storage; there is no global audit API yet.
- **Automatic priority:** score each group using TRUCK=4, FORKLIFT=2, and
  EMPLOYEE_VEHICLE=1, plus one point per 10 seconds without service. At normal
  decision points, groups waiting 120 seconds override the score. This is a
  scheduling threshold; finishing the current service interval, clearance, and
  confirmations adds delay. Ties retain the current green, then configuration order.
- **Manual control:** requests are serialized per junction. The latest accepted
  manual request wins and expires after 120 seconds. An emergency cancels the
  manual lease; automatic scheduling resumes after emergency completion. Manual
  requests during an emergency, degraded state, or recovery are rejected.
- **Emergencies:** explicit clearance normally completes a request. A 120-second
  expiry prevents an abandoned detection from holding the junction indefinitely.
  Conflicting emergencies begin with the oldest detection, then rotate to a
  competing group after each 30-second confirmed green interval. The selected
  group stays fixed through clearance while its emergency remains active. Expiry
  removes emergency priority, not the queued vehicle, which remains at truck
  weight until explicitly cleared. Repeated observations do not renew detection age.
- **Physical state:** no acknowledgement means no confirmation. Use a 3-second
  command timeout, mark uncertain signals UNKNOWN, enter failure mode, and request
  all red where communication allows. Initial scope has no automatic command retry.
  A failure latches control off even if the fail-safe RED request is subsequently
  acknowledged. There is no infinite fail-safe retry loop. An offline controller
  cannot be forced into a physical safe state by software.
- **Recovery:** restart or reconnect invalidates earlier physical confirmations.
  Retain queues, history, and event IDs, reconcile fresh ALL RED acknowledgements,
  and observe clearance before allowing traffic. Persisted GREEN is never trusted
  as evidence of current physical state.
  Pending commands become INTERRUPTED; manual leases are cancelled and automatic
  simulated ACKs reset to disabled.
- **Device status:** one controller covers the junction, even when its event
  includes a direction. Sensors and signals require a direction. OFFLINE/UNKNOWN
  sensor or signal status stops control without discarding queues. The REST demo
  assumes sensors and signals available until a fault report arrives; production
  device discovery and heartbeat expiry are outside scope. Older device timestamps
  cannot overwrite newer reports; equal timestamps use server processing order.
- **Failure transitions:** degraded control requests ALL RED immediately as a
  best-effort fallback. This is a failure action; manual and emergency requests
  cannot invoke it to skip normal YELLOW/clearance. Recovery on a healthy junction
  is rejected. Report all unavailable devices ONLINE before requesting recovery.
- **Scope:** REST simulation and polling satisfy the assessment. MQTT, production
  authentication, and deployment infrastructure are deferred until the core works.

## AI / Tool Usage

OpenAI Codex assisted with assessment review, project structure, backend
code, documentation, and test execution. The candidate is responsible for reviewing
the implementation and being able to explain, debug, and modify the submitted work.
