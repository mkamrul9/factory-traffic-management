# Architecture and safety notes

## Implemented components

`api/` owns HTTP routes and validation models. `domain/` owns vehicle-event rules,
junction configuration, queue scheduling, signal validation, and the standalone
traffic state machine.
`application/` owns junction/event use cases and repository interfaces.
`persistence/` implements those interfaces using SQLite. `main.py` creates the ASGI
application; initialization and migrations run during its lifespan startup.
The domain engine is not connected to the HTTP runtime or physical controllers yet.

The default junction contains two compatible groups. Domain functions accept a
junction configuration, so another junction can use the same rules. Configuration
must partition all four directions, and timing values must be positive and finite.
The configuration is engineering input, not something sensor events may redefine.

`validate_signal_plan` rejects incomplete snapshots, unknown colors, concurrent
conflicting movements, and simultaneous GREEN/YELLOW requests. It intentionally
does not claim to validate transition order or actual controller state.

## Application flow

```text
Angular dashboard / REST sensor and controller simulator
                          |
                     FastAPI routes
                          |
          Application service (one lock per junction)
                          |
              Domain event rules / future traffic engine
                    /              \
        SQLite repository       Controller port
                                   |
                             REST simulator
```

HTTP validation checks incoming shape and identifiers. The application service
loads a junction under its lock and supplies server receipt time to the domain
event rules. Queue changes, deduplication receipts, sequence cursors, and audit
entries commit in one write transaction. Each transaction owns its SQLite
connection; connections are not shared between request threads. Database reads
use snapshot transactions, while writes use `BEGIN IMMEDIATE` with a 10-second
lock timeout. WAL mode allows readers while another connection writes.

Controller delivery and persisted runtime state are planned. Delivery will happen
after the transaction commits, with durable pending commands if interrupted.

The domain engine accepts immutable state, waiting vehicles, and an explicit time.
It returns the next state, audit-worthy events, and at most one new signal request.
There are no timers, database calls, HTTP imports, or hardware assumptions inside
it. Complete matching confirmations start signal intervals. Local signal versions
reject stale or duplicate confirmations; the transport adapter will additionally
need unique, durable physical command IDs across restarts.

A periodic application task will advance due timers through the same application
service. Request handlers will not sleep while waiting for signal intervals or ACKs.
The domain will accept an explicit clock value so timer tests can advance time
without waiting 30 seconds.

The initial runtime will use one Uvicorn worker. An in-process lock cannot protect
the same junction across multiple processes. Scaling later requires database
locking, a single control owner, or a durable per-junction message consumer.

## Domain transition rules and planned controller integration

Normal switching, manual requests, and emergency preemption will all follow:

```text
GREEN confirmed
    -> request YELLOW, await confirmations
    -> hold YELLOW for 5 seconds
    -> request ALL RED, await confirmations
    -> hold ALL RED for 2 seconds
    -> request next GREEN, await confirmations
```

Each direction command will have a unique ID correlated with its acknowledgement.
A group transition cannot advance until all required directions are confirmed.
GREEN service timing starts only after the group is confirmed. Desired and actual
signals remain separate; unknown physical state is represented explicitly rather
than treated as RED.

The transition rules and whole-snapshot confirmation gate are implemented in
`domain/engine.py`. Per-direction command IDs, ACK collection, timeout handling,
device state, and runtime persistence are the next integration step. Until that
step, the engine waits indefinitely for a missing confirmation; it never assumes
an elapsed timeout means a signal changed. The console simulation supplies mock
confirmations explicitly and does not update physical state in the running API.

Late or duplicate acknowledgements must not overwrite a newer request. A failed,
mismatched, or timed-out command enters failure mode. Recovery requires fresh
all-red confirmation and clearance before returning to operation. A sensor failure
will also be reported and handled conservatively; its queue must never be silently
treated as empty. Detailed fault handling will be tested with the controller work.

## Persistence and event processing

Versioned SQL migrations currently create junction configuration, waiting vehicles,
sensor-event receipts, sequence cursors, and audit history. The migration runner
applies each file and its version record atomically under a write lock. Junction A
is seeded once with its own audit entry. Migrations never delete existing data.

Queue counts are derived from waiting vehicle records. A `(junction_id, vehicle_id)`
primary key prevents double arrivals; there is no mutable count that can become
negative. Event IDs have a global unique key. The original canonical payload and
outcome are retained even when an otherwise valid event is rejected. A retry
returns the stored outcome and adds a duplicate audit entry. A conflicting payload
is rejected without overwriting the original receipt.

Sequence cursors are per junction/direction. A higher sequence is processed;
lower/equal sequences are ignored and recorded. A rejected queue operation still
advances a fresh stream cursor, preventing delayed detections from reviving an
already-cleared vehicle. Timestamp freshness failures do not advance the cursor.
There is no reordering buffer or sensor sequence-reset protocol in this scope.

Malformed requests are audited separately because they cannot always be converted
into domain events. These audit entries contain sanitized validation errors rather
than the full request body. Unknown-junction and unidentifiable attempts are stored
without a foreign key to the junction table. Valid sensor transactions fail with
503 if persistence fails; their queue, receipt, cursor, and audit writes roll back
together. If the database itself is unavailable while auditing malformed input,
the validation response remains 422 and an application error is logged.

History uses decreasing audit IDs for cursor pagination. New entries cannot shift
older pages. There is currently no retention/cleanup policy; receipts must not be
deleted casually because they protect against repeated events.

Future timer ticks and controller acknowledgements will use the same serialized
transaction path. Pending commands, device state, and runtime mode/deadlines will
be added with that work.

Currently, restart restores configuration, queues, receipts, cursors, and history.
Status always reports desired ALL RED, actual UNKNOWN, and control inactive.
These values do not imply that a physical device received an all-red command.

Once controller support exists, unresolved commands will be marked interrupted
and prior physical confirmations invalidated. Queues and history survive. Fresh controller recovery
will happen before the engine can issue a new green request. A missed deadline
will never be interpreted as an acknowledgement.
