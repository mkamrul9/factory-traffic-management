# Live controller demonstration

Start the backend and open [Swagger UI](http://127.0.0.1:8000/docs). No hardware is
required. The Angular dashboard will provide these controls in the next checkpoint.

## Enable automatic simulated execution

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
[API examples](api.md) to create traffic. Signal GREEN never removes queued
vehicles; submit a clearance event to report actual departure.

For emergency preemption, first make NORTH/SOUTH GREEN, then submit a fresh EAST
arrival with `vehicle_type: EMERGENCY`. Use a new event ID, a sequence greater than
`status.sequence_numbers.EAST`, and the current timestamp. The backend requests
NORTH/SOUTH YELLOW, waits for confirmation and five seconds, requests ALL RED,
waits for confirmation and two seconds, then requests EAST/WEST GREEN.

## Manual control

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

## Explicit individual acknowledgement

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

## Demonstrate timeout and recovery

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

## Device failure and reconnect

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

## Restart during a transition

Stop and restart the backend with the same database path, including while a
command is pending. Queues, receipts, cursors, and audit history remain. Old pending
commands become INTERRUPTED, prior physical states become UNKNOWN, and fresh RED
commands use new IDs and a new epoch. Manual leases are cancelled. Automatic
simulated execution resets to disabled, even if previously enabled.

Enable simulation or explicitly acknowledge the new RED commands, then observe
clearance. An old ACK cannot restore persisted GREEN or advance the new transition.
Recent history records queued commands, ACKs, failures, recovery, mode changes,
and transitions so the sequence can be explained during technical review.
