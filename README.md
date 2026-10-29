# Factory Traffic Management System

A factory junction controller built for the CSI Smart Tech backend assessment.
The backend uses Python and FastAPI, with SQLite locally and PostgreSQL for the
hosted demo. An Angular dashboard provides the demonstration interface.

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
no real hardware or MQTT connection is configured. The dashboard shows confirmed
and requested signals, queues, alerts, manual controls, simulator inputs, and
recent history. It supports creating and switching between junctions.

All nine assessment scenarios have passed a live HTTP walkthrough. Clean-source
installation, 292 core backend tests, 19 additional hosting/storage tests,
28 frontend tests, and the optimized Angular build have been verified.
See [submission notes](docs/submission.md) for the
deliverable checklist, verification scope, limitations, and technical review tour.

## Run locally

Requirements: Python 3.12 or newer, [uv](https://docs.astral.sh/uv/getting-started/installation/),
and a Node.js version supported by Angular 21: 20.19+, 22.12+, or 24.x.
Development has been checked with Python 3.14 and Node.js 24.11.1 on Windows.

From the repository root:

```sh
cd backend
uv sync --extra dev --locked
uv run uvicorn factory_traffic.main:app --reload --host 127.0.0.1 --port 8000
```

Open [API documentation](http://127.0.0.1:8000/docs), or request
[GET /api/health](http://127.0.0.1:8000/api/health). The health endpoint reports
process liveness; it makes no claim about controller availability or traffic safety.

In a second terminal, from the repository root:

```sh
cd frontend
npm ci
npm start
```

Open the [dashboard](http://127.0.0.1:4200/junctions/A). Enable **Automatic
acknowledgements** in the Controller simulator card. After fresh ALL RED
confirmation and the two-second clearance, the junction becomes active. Use the
Simulation workbench to add and clear vehicles. See the
[dashboard walkthrough](docs/dashboard.md) for manual, emergency, and fault demos.
Both development servers bind to localhost. Angular proxies `/api/**` requests to
the backend; there is no additional frontend environment file to configure.

For a hosted demo, see [deployment setup](docs/deployment.md). It uses Vercel for
the dashboard, Render Free for the backend, and Neon Free PostgreSQL for durable
storage. Free Render services sleep when idle; recovery is required after waking.

On startup, migrations run automatically and create the database at
`backend/data/factory-traffic.sqlite3` when launched from `backend/`. Set the
`FACTORY_TRAFFIC_DB` environment variable to use another database path. Relative
paths are resolved from the process working directory. The database and its WAL
files are local runtime data and are excluded from Git. Use one Uvicorn worker.

Set `DATABASE_URL` to a PostgreSQL connection string to select the hosted adapter.
Keep credentials outside the repository. Both adapters use the same application
and domain logic; see the deployment guide for configuration and parity tests.

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

## Run all nine assessment scenarios

From `backend/`, after dependency installation:

```sh
uv run python scripts/verify_scenarios.py
```

This starts a separate localhost backend with a temporary database and checks
normal/priority traffic, emergency preemption, manual control, duplicate events,
clearance, controller failure, a real process restart, and concurrent requests.
It retains the default signal timings and takes about 90 seconds. Your running
dashboard and its database are not used. See the
[assessment walkthrough](docs/assessment-demo.md) for expected outcomes and steps
to demonstrate the same scenarios in the browser.

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

## Check the dashboard

Run from `frontend/`:

```sh
npm test
npm run build
npm run format:check
```

The Vitest tests cover stale observations, failed and overlapping polls, route
changes, rejected operations, exact duplicate-event payloads, response validation,
and rendering UNKNOWN signals separately from requested GREEN. The production
build is written to `frontend/dist/frontend/browser/`. Serving that bundle requires
a static server with route fallback and an `/api` reverse proxy; FastAPI currently
serves the API only. The development setup above is the supported local demo.

## Project layout

```text
backend/
  src/factory_traffic/
    api/             HTTP routes and response models
    application/     Junction/event use cases and repository interfaces
    controllers/     REST simulator implementing the controller port
    domain/          Configuration, scheduling, and the traffic state machine
    persistence/     SQLite/PostgreSQL adapters, shared SQL, and migrations
    main.py          ASGI entry point
    simulation.py    Deterministic domain walkthrough
  tests/
    api/             Request, concurrency, and persistence scenarios
    domain/          Signal, scheduling, and engine tests independent of HTTP
    hosted/          Optional PostgreSQL parity and deployment configuration checks
  scripts/
    verify_scenarios.py  Nine live HTTP scenarios using a disposable backend
frontend/
  src/app/
    core/            API client, response guards, polling store, and DTOs
    dashboard/       Signals, queues, simulator, controls, and activity components
    shared/          Navigation header, junction dialog, and SVG icons
  proxy.conf.json    Local API proxy configuration
docs/
  api.md             Endpoint reference and runnable request examples
  traffic-control.md Scheduling, transition, and operator policies
  controller-demo.md Live REST demonstration and recovery instructions
  dashboard.md       Browser walkthrough and frontend behavior
  assessment-demo.md All nine required scenarios and expected outcomes
  submission.md      Deliverable checklist, verification, limits, and review tour
  architecture.md    Boundaries, safety decisions, and persistence design
  deployment.md      Free hosting, environment variables, and public verification
```

The domain package imports only the Python standard library. Schema definitions
are in `backend/src/factory_traffic/persistence/migrations/` and
`backend/src/factory_traffic/persistence/postgresql_migrations/`.

## Major architectural decisions

- **FastAPI:** request validation and generated OpenAPI documentation at the API
  boundary. HTTP handlers call application services rather than decide signals.
- **Pure Python domain:** traffic decisions remain testable without a running web
  server, database, or controller. Compatible directions come from configuration.
- **SQLite:** a local transactional database keeps assessment setup small. Each
  event commits its queue mutation, receipt, sequence cursor, and audit together.
  `BEGIN IMMEDIATE` and unique keys protect concurrent writes, including global
  event IDs. Per-junction locks provide the application serialization boundary.
  The PostgreSQL adapter uses a transaction-scoped advisory write lock and
  repeatable-read snapshots, preserving the same transaction boundaries.
  The control runtime uses one process; database write serialization alone
  does not coordinate timer ownership or controller delivery across workers.
- **REST controller adapter:** unique command and batch IDs, connection epochs,
  and acknowledgements separate desired signals from physical confirmation.
  Commands are committed before dispatch through the controller port. MQTT can
  later call the same application acknowledgement/device interfaces.
- **Background control:** one nonblocking task ticks all junctions every 100 ms.
  A monotonic clock supplies elapsed intervals; request handlers never sleep to
  advance traffic signals.
- **Angular:** a signal-based store polls status and recent history every second
  and sends operator intents through a typed HTTP client. Components display
  backend decisions. Failed or stale observations disable writes and visibly mark
  the retained snapshot. The browser does not schedule traffic or confirm lights
  optimistically.

See [architecture notes](docs/architecture.md) for the control flow and
[submission notes](docs/submission.md) for verification and review guidance.

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
- **Scope:** REST simulation and polling satisfy the assessment. MQTT and
  production authentication remain outside scope. The hosted demo uses free
  services with idle sleep and usage limits; it is not continuously available
  factory control.

## AI / Tool Usage

This project was developed with assistance from OpenAI Codex for planning,
implementation, tests, documentation, debugging, and verification. The submitter
is responsible for reviewing and understanding the code and being able to explain,
debug, and modify the implementation.
