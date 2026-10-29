# Implementation and review checkpoints

Work proceeds one checkpoint at a time. Each checkpoint ends with verification,
a focused commit, a push when a remote is available, and a pause for review.
Commit subjects describe the change. They do not include milestone numbering.

## 1. Backend foundation

Status: completed and reviewed.

- FastAPI application, health endpoint, and generated API documentation.
- Domain configuration and signal conflict validation.
- Development tooling, reproducible dependencies, and initial safety tests.
- README with setup, assumptions, architecture, and tool disclosure.

Review: run the API, inspect `/docs`, read the assumptions, and run the checks.
This checkpoint does not run traffic control or provide a dashboard.

## 2. Persistent junctions and vehicle events

Status: implemented and verified; awaiting review.

- SQLite migrations, seeded Junction A, and repository boundary.
- Junction APIs, sensor validation, queue arrivals/clearances, and history.
- Atomic deduplication, sequence ordering, rejected-event auditing, and per-junction
  serialization.

Review: arrival and clearance, duplicate/reused IDs, delayed/out-of-order events,
unknown input, concurrent updates, and queue/history survival after a restart.

## 3. Traffic engine and operator intents

- Deterministic state machine with explicit deadlines and an injected clock.
- Weighted scheduling, waiting-age protection, and safe timed transitions.
- Emergency preemption, conflicting emergencies, manual leases, and return to
  automatic control.

Review: domain tests for normal traffic, vehicle priority, starvation, manual
commands, emergency precedence, and every GREEN/YELLOW/ALL RED transition.

## 4. Controller simulation, faults, and recovery

- Durable commands through a controller interface and REST ACK simulation.
- Desired/actual separation, command correlation, timeouts, stale ACKs, and faults.
- Background ticking, restart recovery, and reconnect reconciliation.
- Device-status API and clear alerts.

Review: delayed, duplicate, missing, failed, and mismatched ACKs; offline sensor or
controller; restart during each transition and while a command is pending.

## 5. Angular dashboard

- Overview, junction detail, and intersection signals sourced from the backend.
- Queues, mode, emergency/failure alerts, and desired/actual comparison.
- Manual controls, vehicle simulation, device status, ACK simulation, and history.
- Polling with loading/error handling and no independent traffic decisions.

Review: demonstrate the main scenarios in the browser and verify backend errors
remain understandable to the operator.

## 6. Submission verification

- API examples and a repeatable guide for all nine assessment scenarios.
- End-to-end checks, clean setup verification, and final documentation.
- Record remaining limitations and check that the submission contains backend,
  frontend, migrations, tests, setup instructions, and both required README sections.

Review: follow the README from a clean checkout and explain the algorithm,
consistency strategy, safety sequence, recovery behavior, and architectural choices.
