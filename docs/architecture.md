# Architecture and safety notes

## Boundaries

`api/` owns HTTP validation and serialization. `application/traffic.py` coordinates
transactions and per-junction locks. `application/control.py` coordinates the
domain engine, device observations, recovery, and the durable command outbox.
`domain/` contains configuration, event rules, scheduling, and the state machine;
it imports only the Python standard library. `persistence/` implements repository
interfaces using SQLite. `controllers/` contains the explicit REST simulator.

```text
Angular dashboard / REST sensor and controller simulator
                            |
                       FastAPI routes
                            |
           Application service (one lock per junction)
                            |
                 Domain rules and traffic engine
                            |
         SQLite: queues, runtime, commands, receipts, history
                            |
                 committed command outbox
                            |
                      Controller port
                            |
                   REST simulator adapter
                            |
                application ACK processing
```

The domain engine accepts state, queues, and time. It returns immutable state,
domain events, and at most one signal request. It contains no timer, transport,
or database calls. Configured compatible groups partition all four directions.
Configuration declares physical compatibility; sensor input cannot redefine it.

The Angular HTTP client validates incoming status data before a shared signal-based
store applies it. It polls status and the latest history together once a second.
Request ordering prevents late replies from replacing newer observations or a
different junction's state. A failed poll or observation older than four seconds
disables writes and labels the retained snapshot as stale. The UI sends intents,
sensor/device events, and explicit simulated ACKs through the API; it does not
decide signal sequences or assume successful physical execution.

## Transactions and consistency

Queue changes, event receipts, sequence cursors, engine state, outgoing commands,
and audit entries commit atomically. Each transaction owns its SQLite connection.
Read operations use snapshot transactions. Writes use `BEGIN IMMEDIATE`, a
10-second lock timeout, database uniqueness constraints, and a junction lock.
WAL mode allows readers while another connection writes.

The same mutation path handles sensor events, operator intents, device reports,
acknowledgements, and timer ticks. IDs are unique across sensor/device events.
Duplicate payloads return the original receipt and add an audit entry; conflicting
payloads never overwrite that receipt. Queues derive from unique waiting vehicles,
not mutable counters. See the README for ordering and freshness policies.

Commands are read from committed outbox records before calling `ControllerPort`.
An adapter may return explicit acknowledgements or no immediate reply. Dispatch
alone never updates actual signals. The REST adapter returns simulated execution
evidence only after an explicit batch action or opt-in automatic simulation.
An asynchronous MQTT adapter can publish those commands and deliver later ACKs
through the same application interface, without changing the traffic engine.

## Signal evidence and failures

Each requested snapshot creates four commands sharing a batch ID and connection
epoch. Each direction has a globally unique command ID. Sending the batch marks
its physical observations UNKNOWN. Individual matching ACKs update only their
directions. All four must be confirmed before the engine starts that stage's timer.
Versions correlate domain requests; command IDs, batch IDs, and epochs protect
against responses from older transitions or recovery sessions.

Normal, manual, and emergency switching follows confirmed GREEN, confirmed YELLOW
held for five seconds, confirmed ALL RED held for two seconds, then next GREEN.
Missing ACKs time out after three seconds by default. Failed/mismatched ACKs,
controller unavailability, or an OFFLINE/UNKNOWN sensor or signal latch DEGRADED.
The coordinator invalidates physical observations, cancels obsolete commands,
cancels manual control, and requests best-effort ALL RED once. A second fault
preserves pending safe RED commands. If the fallback also times out, no endless
retry loop is started. Acknowledging fallback RED alone cannot restart traffic.

Recovery requires available devices, fresh ALL RED commands with a new epoch,
all four confirmations, and a new clearance interval. A healthy junction rejects
the recovery command so an operator cannot use it as a shortcut between greens.
Matching duplicate ACKs are ignored. Old, cancelled, interrupted, or timed-out
commands cannot overwrite newer observations. ACK source timestamps are audit
data; server receipt time controls deadlines.

## Runtime and clock

FastAPI lifespan startup applies migrations and initializes each junction. A
single background task ticks control every 100 ms through the application service,
then executes pending simulated commands when explicitly enabled. No handler
sleeps while waiting for GREEN, YELLOW, clearance, or controller evidence.

`MonotonicClock` anchors UTC at process start and advances by elapsed monotonic
time, so operating-system clock changes cannot shorten physical intervals. Tests
inject a clock and disable the task for deterministic execution. A separate
integration test verifies actual background ticking. The periodic task logs errors
and retries after a short backoff; failed transactions leave no partial commands.

Run one Uvicorn worker. In-process locks do not coordinate multiple control owners
or timer tasks. SQLite serializes writes but does not implement leader election.
Scaling requires a single owner per junction, durable message processing, or
additional distributed coordination.

## Persistence and restart

Versioned migrations store configuration, waiting vehicles, global event receipts,
sequence cursors, runtime state, controller commands, and audit history. Each
migration and version record is atomic. The second migration renames the earlier
sensor receipt table to `event_receipts`, retaining all existing data.

Restart retains queues, receipts, cursors, device observations, and history. It
marks pending commands INTERRUPTED, discards prior physical confirmations, cancels
manual leases, disables automatic simulation, creates a new epoch, and requests
fresh ALL RED. Persisted GREEN, a pending ACK, or an elapsed deadline cannot resume
traffic. Known sensor/signal faults remain blocking until reported ONLINE.

History uses decreasing audit IDs for stable pagination. Sensor validation errors
are stored with sanitized messages rather than full malformed bodies; unknown
junction attempts can be recorded without a junction foreign key. Storage errors
return 503 and roll back the transaction. If storage itself is unavailable while
auditing malformed sensor input, the response stays 422 and the audit failure is
logged. Receipts and history currently have no retention policy.

## Limits of the simulation

There is no real controller transport or hardware watchdog. Sensors/signals are
assumed available in the REST demo until a failure report arrives. There is no
heartbeat expiry protocol. A physical implementation must fence superseded
commands, enforce electrical conflict interlocks, and fail safely on communication
loss; backend acknowledgement handling cannot guarantee those physical behaviors.
Production authentication and authorization are outside this assessment's scope.
