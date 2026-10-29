# Traffic-control algorithm

The engine in `domain/engine.py` is a standalone state machine. The application
supplies a junction configuration, its waiting vehicles, operator commands,
controller confirmations, and the current time. Each operation returns a new
immutable state, domain events, and at most one signal request. Nothing in the
engine imports FastAPI, SQLite, MQTT, or a timer implementation.

## Integration boundary

The API runs the engine through a transactional coordinator, persists its
state and outgoing commands, and handles real-time ticking, REST acknowledgements,
timeouts, and device failures. Physical confirmations come from explicit controller
messages or opt-in simulated execution. Startup and recovery remain DEGRADED
until fresh ALL RED confirmation and clearance. See [controller API reference](api.md#controller-simulation-and-acknowledgements).

To inspect the engine without a database or hardware, run from `backend/`:

```sh
uv run python -m factory_traffic.simulation
```

The walkthrough prints 17 seconds of simulated traffic, advancing the clock
without sleeping. Each confirmation is explicitly marked as simulated.

## State and timing

An engine state records its junction, operating mode, requested signal stage,
current and target traffic groups, signal version, confirmation status, interval
deadline, manual lease, active emergency identities, and recent group service times.
ALL RED is an internal stage, not a fourth physical signal color.

Startup requests ALL RED. It does not assume any controller state. Once the whole
snapshot is confirmed, the two-second clearance interval begins. Traffic can then
be selected. Switching between conflicting groups always follows:

```text
GREEN requested -> confirmation -> normal service
YELLOW requested -> confirmation -> 5 seconds YELLOW
ALL RED requested -> confirmation -> 2 seconds clearance
next GREEN requested -> confirmation -> normal service
```

Normal service is 30 seconds. Configured timings replace these defaults per junction.
An emergency or manual request may end normal service early, but cannot shorten
YELLOW or ALL RED. Changing the target during YELLOW never reverses directly to
GREEN, even when the target becomes the group that was already clearing.

The engine emits no new signal request while waiting for a confirmation. A
confirmation must match the current version and every requested direction/color.
Partial, mismatched, stale, and duplicate confirmations cannot start an interval.
If an emergency arrives while a GREEN request is unconfirmed, its intent is retained.
After that GREEN is confirmed, the engine can immediately request its YELLOW stage.

Each call advances at most one signal stage. A late tick never skips multiple
intervals to catch up. Time inputs must include a timezone and cannot move
backwards. The runtime uses an elapsed monotonic timeline anchored to UTC, so a
forward change to the operating-system wall clock cannot shorten clearance.
Restart reconciles fresh ALL RED instead of replaying old deadlines. The application
task advances the engine every 100 ms without blocking request handlers.

## Automatic scheduling

Both NORTH and SOUTH contribute to NORTH_SOUTH; EAST and WEST contribute to
EAST_WEST. The scheduler works from the configured compatible groups rather than
containing a separate decision tree for Junction A.

For each group with waiting vehicles:

```text
score = sum(vehicle weights) + floor(seconds without service / 10)
TRUCK = 4, FORKLIFT = 2, EMPLOYEE_VEHICLE = 1
```

Queue size influences the sum. Several ordinary vehicles can outweigh one truck.
A group's unserved age begins at its oldest queued arrival or its latest service,
whichever is later. Its age is zero while it currently has GREEN. When it leaves
GREEN, its service timestamp is updated so a vehicle that has not yet sent a
clearance does not retain a false starvation age from before its service.

At a normal service boundary, any group that has waited at least 120 seconds takes
precedence over the score; the longest-waiting group wins. Otherwise, choose the
highest score. Ties keep the current GREEN where possible, then use configuration
order. If there is no alternate demand, keep the current GREEN and evaluate again
at the next service boundary. An idle ALL RED remains ALL RED until demand appears.

The 120-second setting is a scheduling threshold, not a hard physical clearance
deadline. Up to one normal service interval, YELLOW, ALL RED, and controller
confirmation delays can add to it. Emergencies and manual leases take precedence;
the automatic fairness rule cannot promise service while those modes are active.

## Manual intents

The domain accepts `MANUAL_GREEN_REQUEST` with a known direction and
`RETURN_TO_AUTOMATIC` without a direction. The direction selects its configured
compatible group. Clients cannot supply individual signal colors.

A manual lease lasts 120 seconds from acceptance. The latest accepted manual
request wins and renews the lease. Requests are applied in the order supplied by
the serialized application service. A new request during clearance can update the
target but cannot bypass or restart completed safety intervals. No browser
connection or disconnect callback is required to expire a lease.

Emergency mode rejects manual requests. Emergency detection cancels an existing
manual lease. After all emergencies clear or expire, the engine returns to
automatic scheduling without restoring a previous manual override. A return-to-
automatic command during an emergency cannot cancel that emergency's priority.
Degraded or recovering control rejects manual and return-to-automatic intents.
Only the explicit recovery command can start reconciliation, and it is rejected
when devices remain unavailable or the junction is already healthy.

## Emergency selection and expiry

An EMERGENCY waiting vehicle has priority until explicit clearance or 120 seconds
after its original server-recorded arrival. Queue ingestion already rejects stale
emergency detections and repeated physical arrivals. Observing the same queued
vehicle again does not reset its timeout or repeatedly extend its GREEN interval.

The first selection uses the oldest active emergency. Equal arrival times use the
arrival event ID as a deterministic tie-breaker. If its group is already GREEN,
avoid unnecessary switching. With emergencies in conflicting groups, the current
group gets at most one confirmed 30-second service interval before the oldest
competing group is selected. A selected emergency group remains the target through
YELLOW and ALL RED while it still has an active emergency, preventing repeated
selection changes from starving the opposing group.

If emergency priority starts while its compatible group is already confirmed
GREEN, that detection starts a new 30-second priority window. An opposing emergency
can then wait at most the remaining window before safe clearance begins, provided
controller confirmations continue to arrive.

If the selected emergency clears during a transition, choose the remaining active
priority after completing clearance. When an emergency expires, its priority ends
but its physical waiting record remains; ordinary scheduling treats it at truck
weight until a valid clearance arrives. Expiry never pretends that a vehicle left.

## Verification

Domain tests cover startup reconciliation, timing from confirmation, full clearance,
weighted scheduling, waiting-age protection, competing emergencies, repeated and
expired emergencies, manual expiry, target changes during transitions, invalid
commands, and rejected confirmations. Three seeded runs each exercise 1,000 mixed
actions and check every emitted signal snapshot and stage transition. These tests
advance supplied clock values rather than sleeping.
