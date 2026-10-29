# Assessment scenarios

This guide follows the nine scenarios in section 15 of the assessment. All use the
same default NORTH/SOUTH and EAST/WEST groups as Junction A, with 30-second GREEN,
5-second YELLOW, 2-second ALL RED, and a 3-second ACK deadline.

## Run all nine automatically

From `backend/`, after installing the dependencies:

```sh
uv run python scripts/verify_scenarios.py
```

With the pip setup on Windows:

```powershell
.\.venv\Scripts\python scripts/verify_scenarios.py
```

The driver starts its own Uvicorn process on a free localhost port and creates a
temporary SQLite database. It makes real HTTP requests, lets the real background
task advance signals, and restarts that process during the restart scenario.
The normal scenario waits for the real 30-second green deadline; nothing shortens
the configured signal intervals. Expect roughly 90 seconds for the whole run.
Each scenario prints PASS or a diagnostic failure; failure exits with code 1.
The driver stops its own process and removes the temporary database at the end.
It does not use or reset the dashboard's database or backend on port 8000.

Every sampled status is checked for conflicting GREEN/YELLOW movements, negative
queues, and agreement between queue counts and waiting vehicle records. Switching
scenarios must observe YELLOW, ALL RED, and the final confirmed GREEN. Sampling is
not an exhaustive proof: the deterministic domain tests check every emitted
request in their generated event sequences.

## Demonstrate the same behavior in the dashboard

Start both servers using the [README](../README.md). Open the
[dashboard](http://127.0.0.1:4200/junctions/A). Enable **Automatic acknowledgements**
in **Controller simulator** and wait for fresh all-red confirmation and clearance.
Vehicle, device, and individual controller inputs are in **Simulation workbench**;
manual intents are in **Manual control**. Use a fresh junction for
each independent scenario, or clear existing vehicles in Junction A first. New
junction IDs must be unique; all created junctions use the same engine.

### 1. Normal traffic

Before enabling automatic acknowledgements, add two NORTH employee vehicles and
one EAST employee vehicle. Enable acknowledgements and wait for recovery.
NORTH/SOUTH receives the first green because it has more demand. Clear both NORTH
vehicles. After the confirmed green service interval, observe NORTH/SOUTH YELLOW,
ALL RED clearance, then EAST/WEST GREEN. The EAST queue remains one until its
departure is explicitly reported.

Automated check: `normal()` in the driver checks the green deadline, safe switch,
and absence of invented departures.

### 2. Priority traffic

On another fresh junction, add two NORTH employee vehicles and one EAST truck
before enabling acknowledgements. The EAST truck contributes four points; the two
employees contribute two points in total. EAST/WEST receives the initial green.
A forklift contributes two points. Queue size and waiting age can outweigh one
higher-priority vehicle; the policy is a weighted score, not strict FIFO by type.
See [scheduling](traffic-control.md#automatic-scheduling) for ties and fairness.

Automated check: `priority()` verifies that a truck wins this comparison while the
employee vehicles remain queued.

### 3. Emergency preemption

With NORTH/SOUTH confirmed GREEN, add an EAST emergency vehicle. Observe emergency
mode and its direction, then NORTH/SOUTH YELLOW, ALL RED, and EAST/WEST GREEN.
Manual control cannot override the emergency. Clear the emergency vehicle to
return to automatic mode. If a manual lease existed, it stays cancelled.

Automated check: `emergency()` also starts a manual NORTH lease before detection
and checks its cancellation and emergency completion.

### 4. Manual override

With NORTH/SOUTH confirmed GREEN, request WEST under Manual control. Observe the
same safe switch to EAST/WEST and the Manual mode indicator. Click Return to
automatic and verify Automatic mode. A manual request expires after 120 seconds;
the browser does not have to remain connected for expiry to work.

Automated check: `manual()` checks safe execution and return to automatic. Manual
expiry and competing requests are also covered in the domain/API tests.

### 5. Duplicate event

Add a vehicle, note the queue, and click Resend last event. The notification must
say it was already processed and the queue must stay unchanged. This button sends
the exact original ID, timestamp, sequence number, and payload. A new ID is a
different event and does not demonstrate the same idempotency contract.

Automated check: `duplicate()` checks `duplicate: true` and one waiting vehicle.

### 6. Vehicle clearance

Add a SOUTH forklift. Select it under Waiting vehicle to clear and click Clear
vehicle. The queue decreases by one, and the clearance appears in history.
GREEN does not report a departure. Unmatched or wrong-direction clearances are
rejected by the backend and cannot make a queue negative.

Automated check: `clearance()` verifies the arrival/clearance round trip. API tests
cover repeated, unmatched, wrong-direction, and wrong-type clearances.

### 7. Controller failure

With a green active, disable Automatic acknowledgements and request a conflicting
manual direction. Do not acknowledge the commands. After the three-second
deadline, expect DEGRADED mode, an ACK timeout alert, desired ALL RED, and unknown
physical confirmation. In Devices, report the controller Offline, then Online.
Enable automatic acknowledgements to obtain fresh all-red confirmation and
clearance before traffic resumes. Merely reporting Online does not confirm lights.

Automated check: `failure()` covers missing ACKs, the safe request, unknown actual
signals, controller OFFLINE/ONLINE, and recovery. Separate API tests cover partial,
failed, delayed, mismatched, duplicate, and stale ACKs plus directional faults.

### 8. Restart with persisted data

Keep a vehicle queued. Disable automatic acknowledgements, request a conflicting
direction, and stop the backend while its controller commands are pending. Start
it again using the same working directory and database setting. The dashboard
must retain queues/history while showing DEGRADED and actual UNKNOWN. The manual
lease is cancelled and simulation is disabled. Re-enable acknowledgements and
allow fresh confirmation and clearance. Old ACKs cannot resume an old transition.

Automated check: `restart()` terminates and starts the disposable process against
the same database. It checks queues, sequence cursors, history, durable duplicate
receipts, a new batch, simulator reset, stale ACK rejection, and recovery. API
tests repeat recovery from every transition stage.

### 9. Concurrent events

Run the driver for this scenario rather than trying to click within milliseconds.
It submits a NORTH truck, an EAST emergency, a WEST manual request, an exact
duplicate of the emergency, and a controller ACK using five worker threads.
Request order is deliberately not assumed. The manual request can succeed before
the emergency and then be cancelled, or return 409 after emergency priority starts.
Both valid outcomes must leave NORTH queue two, EAST queue one, emergency priority,
and no conflicting movement. Explicit ACKs finish the initial pending batch before
safe emergency preemption proceeds.

Automated check: `concurrent()` checks those outcomes through real HTTP. The API
suite additionally exercises concurrent duplicates across service instances,
cross-junction event-ID uniqueness, clearances, and transaction rollback.
