# Submission notes

The source contains the required backend, frontend, migrations, tests, and
documentation. Use the root README to install and start both applications. No
pre-existing database or environment file is required; startup applies the SQL
migrations and seeds Junction A.

## Where to review each deliverable

- Backend source: `backend/src/factory_traffic/`.
- Frontend source: `frontend/src/`.
- Setup and run instructions: root `README.md` and `frontend/README.md`.
- Database schema/migrations: `backend/src/factory_traffic/persistence/migrations/`.
- API documentation: `docs/api.md`, plus `/docs` and `/openapi.json` on FastAPI.
- Algorithm and state transitions: `docs/traffic-control.md`.
- All nine assessment scenarios: `docs/assessment-demo.md` and
  `backend/scripts/verify_scenarios.py`.
- Controller ACKs and recovery: `docs/controller-demo.md`.
- Browser controls and error behavior: `docs/dashboard.md`.
- Tests: `backend/tests/` and the `*.spec.ts` files under `frontend/src/app/`.
- Architectural decisions: the README and `docs/architecture.md`.
- Required **Assumptions / Questions / Requirement Issues** and **AI / Tool Usage**
  sections: root README, using the exact requested headings.

Both dependency lockfiles are included: `backend/uv.lock` and
`frontend/package-lock.json`. Runtime SQLite files, virtual environments,
`node_modules`, caches, local verification copies, and generated frontend bundles
are excluded from Git and the source archive. Install dependencies after extracting
the archive, then run the two development servers.

## Verification on 29 October 2026

The checks used Windows, Python 3.14.0, Node.js 24.11.1, and npm 11.6.2. A source
archive was extracted into a separate directory without the existing virtual
environment, node_modules, database, build output, or Git metadata. Backend
dependencies were installed with the committed uv lockfile and frontend
dependencies with `npm ci`. Existing package download caches were reused; the
environment and build outputs were newly created.

Verification results:

- Backend: 292 tests passed; Ruff lint and format checks passed; mypy passed.
- Frontend: the clean copy passed its original 26 tests and optimized build. The
  subsequent confirmation-label and readability update passed all 28 frontend
  tests, formatting, and an optimized build without budget warnings.
- Both lockfiles installed successfully into newly created dependency directories.

The live nine-scenario driver also passed all nine cases against its own temporary
database and Uvicorn process using default signal timings. That run used the
working checkout's installed backend; the clean copy independently ran the full
backend/API suite. The subsequent dashboard update did not change backend logic.

Earlier browser verification covered vehicle arrival/clearance, duplicate retries,
manual and emergency control, device failure and recovery, partial and batch ACKs,
junction creation/conflicts, unknown routes, backend disconnection/reconnection,
and mobile/desktop layouts. Those are manual browser checks, not an automated
browser end-to-end suite.

## Current limits and next work

- The supported demo is a single backend process with SQLite and localhost
  development servers. Multiple workers need timer ownership and coordination;
  SQLite write locking alone does not provide them.
- REST simulates physical controllers. MQTT, real hardware, heartbeat expiry,
  hardware conflict interlocks, and a watchdog remain future integration work.
  ACK handling cannot guarantee a physical safe state when a controller is
  unreachable. UNKNOWN remains visible until fresh evidence arrives.
- There is no production authentication/authorization, deployment configuration,
  automatic command retry, or history/receipt retention policy. Do not expose the
  demo directly to an untrusted network.
- The frontend shows the most recent 30 events; older events are available through
  the paginated history API. It uses one-second polling rather than SSE/WebSocket.
- Additional junction instances are supported, but the creation API currently
  uses the two default compatible groups. A different road geometry requires a
  validated configuration entry point; the domain already consumes configuration.
- Sensor sequences belong to junction/direction streams. Production sensors need
  identity, sequence reset, and reconciliation protocols for missed events.
- Normal fairness uses a 120-second scheduling threshold, not a guaranteed physical
  departure deadline. Manual control, emergencies, clearance, and ACK delays can
  extend waiting time.
- Verification was performed on Windows with Python 3.14 and Node.js 24. Other
  supported runtime versions and operating systems have not been exercised here.

With more time, prioritize authenticated operator actions, controller/device
protocols with fencing and heartbeat expiry, ownership coordination, and deployment
checks before adding analytics or more frontend polish.

## Technical review tour

Trace an arrival from `api/traffic.py` through `application/traffic.py` to the
receipt, queue, and transaction interfaces. Then read `domain/scheduling.py` and
`domain/engine.py` for selection, manual/emergency precedence, and transitions.
`application/control.py` maps signal requests to durable commands, ACK correlation,
timeouts, and recovery. `persistence/sqlite.py` owns transactional storage.

Explain why accepted intent differs from physical execution, why all four matching
ACKs are needed, why stage timers start after confirmation, and why restart cannot
reuse old physical state. For consistency, distinguish a per-junction application
lock from SQLite's transaction and uniqueness guarantees.

On the frontend, trace `core/traffic-api.ts` into `core/dashboard-store.ts` and the
intersection/control components. Be ready to explain stale-response ordering,
write disabling when observations age or fail, and why the browser never makes
traffic decisions. Use the existing domain/API tests when changing a policy or
failure rule.

Submit the [repository URL](https://github.com/mkamrul9/factory-traffic-management)
or the source archive through the assessment form. Publishing to GitHub does not
submit the assessment form.
