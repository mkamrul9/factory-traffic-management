# Factory Traffic Management System

A factory junction controller built for the CSI Smart Tech backend assessment.
The application receives vehicle and device events, maintains directional queues,
and coordinates traffic through a deterministic state machine. Python/FastAPI
provides the backend, Angular provides the dashboard, and SQL storage preserves
traffic data across application restarts.

[Live dashboard](https://factory-traffic-management-pi.vercel.app/junctions/A)
· [API documentation](https://factory-traffic-management-pi.vercel.app/docs)

The demo uses free hosting. Its first response after inactivity can take about a
minute. After a backend restart, enable **Automatic acknowledgements** to complete
fresh all-red recovery. See [deployment and hosting limits](docs/deployment.md).

## Features

- Vehicle arrivals and clearances, persistent queues, duplicate-event protection,
  per-direction sequence ordering, and paginated audit history.
- Automatic scheduling based on vehicle weights, queue demand, and waiting time.
- Manual control and emergency preemption through the same safe signal transitions.
- Separate requested and controller-confirmed signals, with command correlation,
  acknowledgement deadlines, device-fault handling, and restart recovery.
- A live dashboard with a visual intersection, operator controls, alerts, recent
  activity, and REST-based vehicle, device, and controller simulation.
- Configurable junction instances using a shared traffic engine. Junction A is
  created automatically.

## Quickstart

Requirements: Python 3.12 or newer, [uv](https://docs.astral.sh/uv/getting-started/installation/),
and Node.js 20.19+, 22.12+, or 24.x. Development was verified with Python 3.14 and
Node.js 24.11.1 on Windows, with additional Linux build checks.

### Backend

From the repository root:

```sh
cd backend
uv sync --extra dev --locked
uv run uvicorn factory_traffic.main:app --reload --host 127.0.0.1 --port 8000
```

Open [Swagger UI](http://127.0.0.1:8000/docs) for endpoint schemas and request forms.
[GET /api/health](http://127.0.0.1:8000/api/health) reports process liveness;
controller readiness and physical signal confirmation are reported separately.

If uv is unavailable, use a virtual environment instead:

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[dev]"
.\.venv\Scripts\python -m uvicorn factory_traffic.main:app --reload --host 127.0.0.1 --port 8000
```

The pip alternative resolves the ranges in `pyproject.toml`; uv uses the exact
versions in `uv.lock`. On Linux/macOS, use `.venv/bin/python`.

### Dashboard

In a second terminal, from the repository root:

```sh
cd frontend
npm ci
npm start
```

Open [Junction A](http://127.0.0.1:4200/junctions/A). In **Controller simulator**,
enable **Automatic acknowledgements** and wait for fresh ALL RED confirmation,
two seconds of clearance, and **Control active**. Simulation starts disabled
after every backend restart; physical signals remain UNKNOWN until confirmed.

Use **Simulation workbench → Vehicles** to add traffic, resend an event, and
report a vehicle's clearance. **Devices** and **Controller** expose fault and ACK
simulation. Follow the [nine-scenario walkthrough](docs/assessment-demo.md) for
normal, manual, emergency, failure, restart, and concurrency demonstrations.

Both development servers bind to localhost. Angular proxies `/api`, `/docs`, and
`/openapi.json` to FastAPI. Restart the development server after changing its proxy.
The browser displays backend decisions and does not schedule traffic itself.

## Configuration and persistence

Local startup applies versioned migrations and creates
`backend/data/factory-traffic.sqlite3` when launched from `backend/`. No existing
database or environment file is required.

- `FACTORY_TRAFFIC_DB`: an alternative SQLite path, resolved from the process's
  working directory when relative.
- `DATABASE_URL`: selects PostgreSQL for the hosted backend. Keep its connection
  string in hosting secrets and retain the provider's TLS parameters.
- `FACTORY_TRAFFIC_REQUIRE_POSTGRES=1`: prevents a hosted deployment from silently
  falling back to a temporary SQLite file when its database URL is missing.
- `PORT`: the production Docker image's listening port; defaults to 8000.

Use **one Uvicorn worker and one backend replica**. Database write serialization
does not coordinate competing timer owners or controller delivery across workers.
Local databases, credentials, dependencies, caches, and build output are excluded
from Git and the submission archive.

The hosted demo uses Vercel Hobby for Angular, Render Free for FastAPI, and Neon
Free PostgreSQL. See [deployment instructions](docs/deployment.md) for the Docker
image, environment variables, provider limits, and public verification steps.

## Major architectural decisions

- **HTTP boundary:** FastAPI validates requests and generates OpenAPI schemas.
  Routes call application services; they cannot directly overwrite signal states.
- **Independent domain:** `domain/` imports only the Python standard library.
  Configuration, scheduling, and immutable state transitions can be tested without
  HTTP, a database, a browser, or a physical controller.
- **Transactional coordination:** per-junction locks serialize application
  operations. A queue mutation, event receipt, sequence cursor, runtime update,
  outgoing command, and audit entry commit together. SQLite uses `BEGIN IMMEDIATE`
  and uniqueness constraints; PostgreSQL uses an advisory write lock and
  repeatable-read snapshots. Both adapters share the same storage operations.
- **Durable controller boundary:** commands are committed before dispatch through
  `ControllerPort`. Unique command IDs, batch IDs, and connection epochs correlate
  ACKs and reject obsolete observations. The REST simulator can later be joined
  or replaced by an MQTT adapter without changing the traffic engine.
- **Nonblocking timing:** one background task ticks junctions every 100 ms. A
  monotonic clock measures elapsed intervals; request handlers never sleep to
  advance signals. Each stage's interval starts after all four matching ACKs.
- **Frontend state:** Angular polls status and recent history every second through
  a typed client with response guards. Late responses cannot replace newer or
  different-junction observations. Failed polls or observations older than four
  seconds disable writes. UNKNOWN signals are displayed separately from requests.

Restart retains queues, receipts, sequence cursors, device observations, and
history. Pending commands become INTERRUPTED, physical confirmation becomes
UNKNOWN, manual leases are cancelled, and automatic simulation is disabled.
Fresh ALL RED confirmation and clearance are required before traffic resumes.

## Traffic-control rules

The default compatible groups are NORTH/SOUTH and EAST/WEST. Normal service uses
30 seconds GREEN, 5 seconds YELLOW, and 2 seconds ALL RED. Conflicting switching
follows confirmed GREEN → confirmed YELLOW → confirmed ALL RED → next GREEN.
Manual and emergency requests can end normal service early but cannot shorten
the YELLOW or clearance intervals.

Automatic selection scores each waiting group using TRUCK=4, FORKLIFT=2, and
EMPLOYEE_VEHICLE=1, plus one point per ten seconds without service. A group waiting
at least 120 seconds takes precedence at a normal scheduling boundary. Ties retain
the current GREEN where possible, then use configuration order.

Failure/recovery takes precedence over emergency control, emergency over manual,
and manual over automatic scheduling. Manual leases expire after 120 seconds.
Emergencies cancel manual leases and retain priority until clearance or expiry.
See [traffic-control design](docs/traffic-control.md) for the complete scheduling,
competing-emergency, timing, and transition policies.

## Verification

Run backend checks from `backend/`:

```sh
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy
```

Run frontend checks from `frontend/`:

```sh
npm test
npm run build
npm run format:check
```

The production frontend bundle is written to `frontend/dist/frontend/browser/`.
Its server must provide route fallback and an API proxy; `frontend/vercel.json`
configures these for the hosted demo.

Run all nine assessment scenarios from `backend/`:

```sh
uv run python scripts/verify_scenarios.py
```

This driver uses its own temporary database and Uvicorn process. It checks normal
and priority traffic, emergency preemption, manual control, duplicate events,
clearance, controller failure, a real process restart, and concurrent requests.
Default timings are retained, so the run takes about 90 seconds. The running
dashboard and its database are not used.

For a deterministic domain walkthrough without a database or real-time delays:

```sh
uv run python -m factory_traffic.simulation
```

Verification completed on 29 October 2026 included 292 core backend tests,
19 additional hosting/storage tests, 28 frontend tests, Ruff, mypy, and production
builds. PostgreSQL parity tests require an explicitly configured disposable
database; see [PostgreSQL verification](docs/deployment.md#postgresql-verification).
Tests cover concurrency, rollback, ordering, confirmation gates, clearance timing,
failure recovery, and restart. Domain checks also exercise 1,000 mixed actions in
each of three deterministic runs.

Clean-source dependency installation, Linux production builds, and the nine
live HTTP scenarios passed. Public-demo checks covered the API proxy, Swagger,
direct-route refresh, vehicle addition/retry/clearance, manual and emergency
control, and persistence after a hosted restart. Browser checks were manual;
there is no automated browser end-to-end suite.

## Project structure

```text
backend/
  src/factory_traffic/
    api/             HTTP routes, validation, and response models
    application/     Use cases, coordination, and repository/controller ports
    controllers/     REST simulator
    domain/          Configuration, scheduling, and traffic state machine
    persistence/     SQL adapters and versioned migrations
    main.py          ASGI entry point
    simulation.py    Deterministic domain demonstration
  tests/             Domain, API, and optional PostgreSQL checks
  scripts/           Live nine-scenario verification
  Dockerfile         Hosted backend image
frontend/
  src/app/
    core/            API client, DTOs, response guards, and polling store
    dashboard/       Intersection, queues, controls, simulation, and activity
    shared/          Navigation, junction dialog, and icons
  vercel.json        Static hosting and API proxy routes
deploy/render.yaml   Free backend service configuration
docs/
  api.md             Endpoint reference and runnable controller examples
  traffic-control.md Scheduling and signal-transition policies
  assessment-demo.md Instructions for all nine assessment scenarios
  deployment.md      Hosting, configuration, and storage verification
```

SQLite migrations are in `persistence/migrations/`; PostgreSQL migrations are in
`persistence/postgresql_migrations/`. Both directories are under
`backend/src/factory_traffic/` and are packaged with the backend.

## Limitations and next work

Physical devices are simulated through REST. MQTT, hardware conflict interlocks,
a watchdog, heartbeat expiry, and command fencing remain integration work. The
backend cannot guarantee a physical safe state when a controller is unreachable.

There is no production authentication, automatic command retry, history/receipt
retention policy, or distributed timer ownership. The public demo exposes
simulated operator controls and should contain demonstration data only. Free
hosting sleeps when idle and is unsuitable for continuous factory control.

Additional junctions currently use the default two compatible groups. A different
road geometry needs a validated configuration entry point. Real sensor deployments
also need sensor identity, sequence-reset rules, and missed-event reconciliation.
With more time, prioritize authenticated operator actions, device protocols,
ownership coordination, and continuous hosting.

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

OpenAI Codex assisted with requirements analysis, backend and frontend
implementation, tests, debugging, documentation, and deployment verification.
The submitter selected the stack, requested incremental checkpoints and UI
revisions, and chose the repository and free-hosting approach.

The submitter is responsible for understanding the submitted implementation and
being able to explain, debug, and modify it during the technical review.
