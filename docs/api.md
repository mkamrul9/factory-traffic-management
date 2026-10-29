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
  control availability. Physical states remain UNKNOWN in this version.
- `POST /api/sensor-events`: process one arrival or clearance.
- `GET /api/junctions/{id}/history?limit=50&before_id=123`: newest-first audit
  history. Follow `next_before_id` until it is null. Limit is between 1 and 100.

Manual commands and controller-event endpoints are not implemented yet.

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
