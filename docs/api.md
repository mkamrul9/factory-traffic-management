# Backend API

Start the backend as described in the README. Interactive schemas and request
forms are available at [Swagger UI](http://127.0.0.1:8000/docs), and the generated
OpenAPI document is at [openapi.json](http://127.0.0.1:8000/openapi.json).

## Available endpoints

- `GET /api/health`: process liveness.
- `GET /api/junctions`: list persisted junction configurations.
- `POST /api/junctions`: create a junction using the default compatible groups.
- `GET /api/junctions/{id}`: configuration and creation timestamp.
- `GET /api/junctions/{id}/status`: queue counts, waiting vehicles, and current
  mode/stage, desired/confirmed signals, device health, alerts, sequence cursors,
  manual lease, pending commands, and simulator configuration.
- `POST /api/sensor-events`: process one arrival or clearance.
- `GET /api/junctions/{id}/history?limit=50&before_id=123`: newest-first audit
  history. Follow `next_before_id` until it is null. Limit is between 1 and 100.

- `POST /api/junctions/{id}/commands`: manual intent, return to automatic, or recovery.
- `GET /api/junctions/{id}/controller-commands`: committed pending direction commands.
- `POST /api/controller-events`: correlate one physical ACK/FAILED report.
- `POST /api/device-events`: controller, directional signal, or sensor health.
- `POST /api/junctions/{id}/simulator`: enable/disable automatic simulated execution.
- `POST /api/junctions/{id}/simulator/acknowledge`: explicitly execute one pending batch.

See [controller simulation and acknowledgements](#controller-simulation-and-acknowledgements)
below for request bodies and the ACK, fault, and recovery policy. Simulation starts disabled after
every restart. Queue ingestion continues even when control is degraded; it never
silently treats an unavailable sensor's queue as empty.

## Create a junction

```json
{
  "junction_id": "B",
  "name": "Loading bay",
  "timings": {
    "green_seconds": 30,
    "yellow_seconds": 5,
    "all_red_seconds": 2,
    "acknowledgement_timeout_seconds": 3
  }
}
```

The `timings` object and its individual fields are optional. Values must be finite
positive numbers. Identifiers are case sensitive. Junction IDs allow letters,
digits, underscores, and hyphens, up to 32 characters, starting with a letter or
digit. Names contain 1–100 characters after trimming. Unknown input properties
are rejected. Creating an existing junction returns 409 without replacing it.

## Demonstrate arrivals, duplicate handling, and clearance

Run these commands in PowerShell while the backend is running. A new run ID and
a current sequence number keep repeated demonstrations separate. Use these
sequence values consistently if also submitting events through Swagger UI.

```powershell
$baseUrl = "http://127.0.0.1:8000/api"
$runId = [guid]::NewGuid().ToString("N")
$sequence = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()
$arrival = @{
    event_id = "arrival-$runId"
    junction_id = "A"
    direction = "NORTH"
    event_type = "VEHICLE_ARRIVED"
    vehicle_id = "VH-$runId"
    vehicle_type = "TRUCK"
    sequence_no = $sequence
    timestamp = [DateTimeOffset]::UtcNow.ToString("o")
}
$body = $arrival | ConvertTo-Json

# First request accepts the vehicle; the second returns duplicate=true.
Invoke-RestMethod "$baseUrl/sensor-events" -Method Post -ContentType "application/json" -Body $body
Invoke-RestMethod "$baseUrl/sensor-events" -Method Post -ContentType "application/json" -Body $body
Invoke-RestMethod "$baseUrl/junctions/A/status" | ConvertTo-Json -Depth 6

$clearance = @{
    event_id = "clearance-$runId"
    junction_id = "A"
    direction = "NORTH"
    event_type = "VEHICLE_CLEARED"
    vehicle_id = "VH-$runId"
    sequence_no = $sequence + 1
    timestamp = [DateTimeOffset]::UtcNow.ToString("o")
}
Invoke-RestMethod "$baseUrl/sensor-events" -Method Post -ContentType "application/json" `
    -Body ($clearance | ConvertTo-Json)
Invoke-RestMethod "$baseUrl/junctions/A/history?limit=10" | ConvertTo-Json -Depth 6
```

The NORTH queue increases by one, stays unchanged on retry, and decreases by one
on clearance. Other queued vehicles remain untouched. Restart the backend with
the same database path to check persistence, then retry the same event body to
check durable idempotency. Restarting does not reset sequence cursors.

## Sensor event contract

Required fields are `event_id`, `junction_id`, `direction`, `event_type`,
`vehicle_id`, `sequence_no`, and `timestamp`. Arrivals also require `vehicle_type`.
Clearances may omit type; when provided, it must match the waiting vehicle.

- Directions: `NORTH`, `SOUTH`, `EAST`, `WEST`.
- Event types: `VEHICLE_ARRIVED`, `VEHICLE_CLEARED`.
- Vehicle types: `FORKLIFT`, `TRUCK`, `EMPLOYEE_VEHICLE`, `EMERGENCY`.
- Event/vehicle IDs: 1–128 ASCII letters, digits, underscores, dots, colons, or
  hyphens, starting with a letter or digit. Surrounding whitespace is trimmed.
- Sequence numbers: JSON integers from 0 through 9223372036854775807. Strings,
  booleans, and negative values are rejected.
- Timestamps: ISO 8601 strings with time and timezone, normalized to UTC. Server
  receipt time determines waiting age. Sensor time does not determine queue order.

A request with a fresh sequence applies the queue operation. A lower or equal
sequence is recorded as IGNORED. New event IDs do not bypass sequence ordering.
Non-emergency events may be delayed; emergency arrivals older than 120 seconds
are rejected. Sensor time more than 30 seconds ahead of the server is rejected.
These timestamp failures do not advance the stream cursor. Other fresh, validly
shaped events advance the cursor even when the queue operation is rejected.

## Responses and errors

A validly shaped sensor request always returns an event receipt:

```json
{
  "event_id": "evt-10001",
  "junction_id": "A",
  "outcome": "ACCEPTED",
  "code": "VEHICLE_ARRIVED",
  "message": "Vehicle added to the queue.",
  "received_at": "2026-10-29T04:00:00Z",
  "duplicate": false
}
```

HTTP 200 means ACCEPTED or IGNORED; inspect `outcome` and `code`. HTTP 409 means
a queue conflict, stale emergency/future timestamp, or reused ID with different
content. HTTP 404 means unknown junction. Retrying an identical event returns the
original outcome and status with `duplicate: true`, even for a rejected event.
A conflicting reuse does not overwrite the original receipt. Use a new event ID
for a new or corrected logical event.

Malformed requests return 422 with a separate validation error envelope, and do
not reserve an event ID. The same envelope format is used for junction lookup,
configuration, and storage errors:

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Request validation failed.",
    "details": [
      {"location": "body.direction", "message": "Invalid direction", "type": "enum"}
    ]
  }
}
```

Storage failures return 503 and roll back the complete sensor transaction; retrying
the event is safe. Error details from the database are logged by the server and
are not returned to the client.

Accepted, ignored, rejected, and duplicate attempts appear in history. If a request
cannot identify a junction, its validation audit is stored with a null junction ID
and is only available through database inspection in the current version.

## Controller simulation and acknowledgements

Start the backend and open [Swagger UI](http://127.0.0.1:8000/docs). No hardware is
required. These controls are also available in the Angular dashboard; see the
[assessment walkthrough](assessment-demo.md).

### Enable automatic simulated execution

Run in PowerShell:

```powershell
$baseUrl = "http://127.0.0.1:8000/api"
Invoke-RestMethod "$baseUrl/junctions/A/simulator" -Method Post `
    -ContentType "application/json" -Body '{"auto_ack":true}'
```

This starts recovery if control already timed out, executes fresh ALL RED commands,
and sends their matching simulated ACKs. After two seconds of confirmed clearance,
automatic control is ready. The simulator executes subsequent batches through the
same ACK processing as explicit controller requests. It does not change engine rules.

```powershell
Invoke-RestMethod "$baseUrl/junctions/A/status" | ConvertTo-Json -Depth 8
```

Expect `mode: AUTOMATIC`, `control_active: true`, and controller ONLINE after
recovery. An empty junction can remain ALL RED. Follow the vehicle walkthrough in
[vehicle examples above](#demonstrate-arrivals-duplicate-handling-and-clearance) to create traffic. Signal GREEN never removes queued
vehicles; submit a clearance event to report actual departure.

For emergency preemption, first make NORTH/SOUTH GREEN, then submit a fresh EAST
arrival with `vehicle_type: EMERGENCY`. Use a new event ID, a sequence greater than
`status.sequence_numbers.EAST`, and the current timestamp. The backend requests
NORTH/SOUTH YELLOW, waits for confirmation and five seconds, requests ALL RED,
waits for confirmation and two seconds, then requests EAST/WEST GREEN.

### Manual control

```powershell
Invoke-RestMethod "$baseUrl/junctions/A/commands" -Method Post `
    -ContentType "application/json" `
    -Body '{"command":"MANUAL_GREEN_REQUEST","direction":"WEST"}'

Invoke-RestMethod "$baseUrl/junctions/A/commands" -Method Post `
    -ContentType "application/json" -Body '{"command":"RETURN_TO_AUTOMATIC"}'
```

Manual requests expire after 120 seconds. An active emergency rejects manual
override, and degraded/recovering control rejects both normal operator intents.
Changing intent during a transition does not skip its clearance intervals.

### Explicit individual acknowledgement

Disable automatic simulation to inspect the actual request/confirmation boundary:

```powershell
Invoke-RestMethod "$baseUrl/junctions/A/simulator" -Method Post `
    -ContentType "application/json" -Body '{"auto_ack":false}'
Invoke-RestMethod "$baseUrl/junctions/A/controller-commands" | ConvertTo-Json -Depth 5
```

Each pending direction command includes a unique `command_id`, `batch_id`,
connection `epoch`, local `signal_version`, requested state, and server deadline.
Send one response using its actual ID and observed state:

```json
{
  "junction_id": "A",
  "command_id": "cmd-COPY_FROM_PENDING_COMMAND",
  "status": "ACK",
  "actual_state": "RED"
}
```

POST that body to `/api/controller-events`. `actual_state` must match what the
controller actually did; do not report RED for a GREEN request unless demonstrating
a mismatch. `status: FAILED` may omit `actual_state`. Optional `timestamp` is an
ISO 8601 source timestamp; server receipt time remains authoritative for timeouts.

All four directions must confirm before the engine starts the stage's timer.
Partial confirmation leaves the other directions UNKNOWN. Matching repeated ACKs
return IGNORED with `duplicate: true`; stale command IDs return 409 and cannot
overwrite current state. A missing command ID returns 404. A mismatched or failed
active ACK latches DEGRADED and creates a best-effort ALL RED batch.

The default ACK deadline is three seconds. For manual exploration, either use the
automatic simulator or create another junction with a larger
`timings.acknowledgement_timeout_seconds`. Timed-out batches require recovery.

To explicitly execute all pending directions in one action, POST this body to
`/api/junctions/A/simulator/acknowledge`:

```json
{"batch_id":"batch-COPY_FROM_STATUS"}
```

This is an explicit simulator action. A stale batch is rejected, and offline
controllers/signals cannot be silently simulated as available.

### Demonstrate timeout and recovery

With NORTH/SOUTH GREEN, disable automatic simulation and request manual WEST.
Leave its YELLOW commands unacknowledged. After three seconds, expect DEGRADED,
an ACK_TIMEOUT alert, actual UNKNOWN, and new best-effort ALL RED commands.
If those commands also time out, the backend stops generating new ones. Even
confirming fallback RED does not remove the failure latch.

```powershell
Invoke-RestMethod "$baseUrl/junctions/A/commands" -Method Post `
    -ContentType "application/json" -Body '{"command":"RECOVER_CONTROLLER"}'
```

Confirm the fresh ALL RED batch and wait for clearance, or enable the automatic
simulator to perform that sequence. Recovery is rejected on a healthy junction
and while any required device remains unavailable.

### Device failure and reconnect

POST `/api/device-events` using a new event ID and current ISO timestamp:

```json
{
  "event_id": "status-301",
  "junction_id": "A",
  "device_type": "SENSOR",
  "direction": "SOUTH",
  "status": "OFFLINE",
  "timestamp": "2026-10-29T06:00:00Z"
}
```

Device types are `SIGNAL_CONTROLLER`, `SIGNAL`, and `SENSOR`. Signals/sensors
require a direction. A controller covers the entire junction; its optional
direction does not identify a separate directional controller. ONLINE, OFFLINE,
and UNKNOWN are supported. Any unavailable required device stops traffic control
without removing queued vehicles. Sensor events can still be recorded for
reconciliation, but do not silently mark that sensor ONLINE.

Report the device ONLINE with a new ID and a non-older timestamp. Reconnect starts
fresh ALL RED recovery only when all required devices are available. Repeated
ONLINE heartbeats do not reset a healthy junction or restart clearance. Old
reports cannot override newer ones; equal timestamps use server processing order.
Event IDs share the sensor/device idempotency namespace and survive restarts.

### Restart during a transition

Stop and restart the backend with the same database path, including while a
command is pending. Queues, receipts, cursors, and audit history remain. Old pending
commands become INTERRUPTED, prior physical states become UNKNOWN, and fresh RED
commands use new IDs and a new epoch. Manual leases are cancelled. Automatic
simulated execution resets to disabled, even if previously enabled.

Enable simulation or explicitly acknowledge the new RED commands, then observe
clearance. An old ACK cannot restore persisted GREEN or advance the new transition.
Recent history records queued commands, ACKs, failures, recovery, mode changes,
and transitions so the sequence can be explained during technical review.
