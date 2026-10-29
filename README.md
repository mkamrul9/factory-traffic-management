# Factory Traffic Management System

A factory junction controller built for the CSI Smart Tech backend assessment.
The backend uses Python and FastAPI. SQLite stores vehicle queues and audit
history, and an Angular dashboard will provide the demonstration interface.

## Current status

The backend supports persistent junctions, vehicle arrivals and clearances,
idempotent sensor events, per-direction sequence ordering, and paginated audit
history. Junction A is created automatically. Domain safety checks validate
signal snapshots independently of the API and database.

The traffic state machine, controller simulator, and Angular dashboard are still
to be implemented. Status reports desired ALL RED and actual UNKNOWN, with
`control_active: false`. No physical commands are sent in the current version.

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

See [API examples](docs/api.md) for an arrival, retry, and clearance walkthrough.

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
are snapshot checks; timed transitions and controller acknowledgements will need
their own tests as the engine is added. API integration tests also cover input
validation, transactional rollback, concurrent events across service instances,
and persistence after restarting the application. Each test uses a temporary database.

## Project layout

```text
backend/
  src/factory_traffic/
    api/             HTTP routes and response models
    application/     Junction/event use cases and repository interfaces
    domain/          Configuration and traffic safety rules
    persistence/     SQLite adapter and versioned SQL migrations
    main.py          ASGI entry point
  tests/
    api/             Request, concurrency, and persistence scenarios
    domain/          Signal rules independent of HTTP and hardware
docs/
  api.md             Endpoint reference and runnable request examples
  architecture.md    Boundaries, safety decisions, and persistence design
  implementation-plan.md
```

Controller adapters and `frontend/` will be added with their respective features.
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
  The control runtime will use one process; database write serialization alone
  does not coordinate future timer ownership or controller delivery across workers.
- **REST controller adapter, planned:** explicit command IDs and acknowledgements
  will separate desired signals from physical confirmation. MQTT can later use
  the same application interface.
- **Angular, planned:** the dashboard will poll the API and send operator intents.
  It will display backend decisions rather than run its own signal timers.

See [architecture notes](docs/architecture.md) for the intended control flow and
[implementation plan](docs/implementation-plan.md) for review checkpoints.

## Assumptions / Questions / Requirement Issues

Junction configuration, queue/event policies, and signal snapshot validation
are implemented. Scheduling, manual/emergency control, and physical-controller
policies below remain planned until their respective features are implemented.

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
- **Automatic priority:** a weighted queue score and waiting age will select the
  next group after its normal service interval. A maximum-wait rule will bound
  starvation during automatic operation. Exact weights will accompany the engine.
- **Manual control:** requests will be serialized per junction. The latest accepted
  manual request wins and expires after 120 seconds. An emergency cancels the
  manual lease; automatic scheduling resumes after emergency completion. Manual
  requests during an emergency or failure will be rejected.
- **Emergencies:** explicit clearance normally completes a request. A 120-second
  expiry prevents an abandoned detection from holding the junction indefinitely.
  Conflicting emergencies will be served by waiting age with bounded service
  intervals; repeated detections must not renew an existing request indefinitely.
- **Physical state:** no acknowledgement means no confirmation. Use a 3-second
  command timeout, mark uncertain signals UNKNOWN, enter failure mode, and request
  all red where communication allows. Initial scope has no automatic command retry.
  An offline controller cannot be forced into a physical safe state by software.
- **Recovery:** restart or reconnect invalidates earlier physical confirmations.
  Retain queues, history, and event IDs, reconcile fresh ALL RED acknowledgements,
  and observe clearance before allowing traffic. Persisted GREEN is never trusted
  as evidence of current physical state.
- **Scope:** REST simulation and polling satisfy the assessment. MQTT, production
  authentication, and deployment infrastructure are deferred until the core works.

## AI / Tool Usage

OpenAI Codex assisted with assessment review, project structure, backend
code, documentation, and test execution. The candidate is responsible for reviewing
the implementation and being able to explain, debug, and modify the submitted work.
