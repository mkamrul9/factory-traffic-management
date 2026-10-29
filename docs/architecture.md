# Architecture and safety notes

## Implemented foundation

`api/` owns HTTP routes. `domain/` owns junction configuration and validation of
individual desired signal snapshots. `main.py` creates the ASGI application.
There is no database or running traffic engine yet.

The default junction contains two compatible groups. Domain functions accept a
junction configuration, so another junction can use the same rules. Configuration
must partition all four directions, and timing values must be positive and finite.
The configuration is engineering input, not something sensor events may redefine.

`validate_signal_plan` rejects incomplete snapshots, unknown colors, concurrent
conflicting movements, and simultaneous GREEN/YELLOW requests. It intentionally
does not claim to validate transition order or actual controller state.

## Planned application flow

```text
Angular dashboard / REST sensor and controller simulator
                          |
                     FastAPI routes
                          |
          Application service (one lock per junction)
                          |
                  Traffic domain engine
                    /              \
        SQLite repository       Controller port
                                   |
                             REST simulator
```

HTTP validation checks incoming shape and identifiers. The application service
loads a junction under its lock, supplies the current time to the domain engine,
and commits state, deduplication records, pending commands, and audit entries in
one database transaction. Controller delivery happens after commit. Pending
commands remain durable if delivery is interrupted.

A periodic application task will advance due timers through the same application
service. Request handlers will not sleep while waiting for signal intervals or ACKs.
The domain will accept an explicit clock value so timer tests can advance time
without waiting 30 seconds.

The initial runtime will use one Uvicorn worker. An in-process lock cannot protect
the same junction across multiple processes. Scaling later requires database
locking, a single control owner, or a durable per-junction message consumer.

## Planned transition and acknowledgement rules

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

Late or duplicate acknowledgements must not overwrite a newer request. A failed,
mismatched, or timed-out command enters failure mode. Recovery requires fresh
all-red confirmation and clearance before returning to operation. A sensor failure
will also be reported and handled conservatively; its queue must never be silently
treated as empty. Detailed fault handling will be tested with the controller work.

## Planned persistence

SQLite migrations will introduce junction configuration and runtime state, waiting
vehicles, processed sensor events, sequence cursors, pending controller commands,
device state, and audit history. Queue counts will be derived from waiting vehicle
records. A unique vehicle key and nonnegative counts will prevent double arrival
and negative-queue errors.

All junction mutations, including timer ticks and controller acknowledgements,
will use the same serialized transaction path. A duplicate event will return its
recorded outcome without mutating the queue again. Rejected and duplicate events
will leave useful audit entries.

On startup, unresolved commands will be marked interrupted and prior physical
confirmations invalidated. Queues and history survive. Fresh controller recovery
will happen before the engine can issue a new green request. A missed deadline
will never be interpreted as an acknowledgement.
