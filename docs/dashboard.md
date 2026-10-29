# Dashboard walkthrough

Start both servers using the [README](../README.md), then open
[Junction A](http://127.0.0.1:4200/junctions/A). The screen polls the backend once a
second. Actions may take one polling interval to appear. This is a local REST
simulation; there is no physical controller connected.

## Start controller simulation

1. In **Controller simulator**, enable **Automatic acknowledgements**. This
   setting resets to disabled whenever the backend restarts.
2. The backend obtains fresh ALL RED confirmations, then waits two seconds for
   clearance. Wait for **Control active** and **Automatic** mode.
3. With no vehicles waiting, all four signals can remain RED. Confirmed signals
   appear on the intersection and each approach card; the requested state appears
   separately below them. UNKNOWN is never displayed as confirmed RED.

## Add traffic and retry an event

1. Select **Vehicles** in the Simulation workbench. Choose an approach and vehicle
   type, optionally enter an ID, and click **Add vehicle**.
2. Check the approach's queue and Recent activity. Normal scheduling considers
   vehicle weights and waiting age; GREEN alone does not remove a vehicle.
3. Click **Resend last event**. It sends the same event ID, sequence, and payload.
   The duplicate notice confirms the retry without increasing the queue.
4. Select the vehicle under **Waiting vehicle to clear**, then click **Clear
   vehicle**. This reports its departure to the backend.

## Manual control and emergency priority

1. Under **Manual control**, choose EAST or WEST and click **Request green**.
   The backend serves its compatible road group through YELLOW and ALL RED before
   a conflicting GREEN. The override expires after 120 seconds.
2. Add a NORTH or SOUTH **Emergency** vehicle. The emergency cancels the manual
   lease and starts safe preemption. The manual controls are disabled while the
   emergency is active.
3. Clear that emergency vehicle. Automatic scheduling resumes; the cancelled
   manual lease does not resume.
4. Without an emergency, **Return to automatic** ends an active manual override.
   Every movement still follows backend confirmation and clearance rules.

## Device faults and recovery

1. Open **Devices**. Select **Sensor**, an approach, and **Offline**, then click
   **Report status**.
2. The backend enters DEGRADED mode, requests ALL RED as a best-effort fallback,
   and shows a device alert. Queues remain intact. Manual traffic requests are
   disabled.
3. Report the same sensor **Online**. With automatic acknowledgements enabled,
   fresh recovery confirmations and clearance restore automatic control. If
   simulation is disabled, use **Recover controller** and explicit ACKs instead.

## Explicit controller acknowledgements

For this demonstration, the default ACK deadline is only three seconds. A missed
deadline deliberately enters DEGRADED mode; use recovery to start a fresh batch.

1. Disable **Automatic acknowledgements**. If the junction is healthy and idle,
   request a movement; if it is degraded, click **Recover controller**.
2. **Acknowledge pending batch** confirms every remaining command in the current
   batch. The backend still waits for clearance before a conflicting GREEN.
3. To inspect partial confirmation, open the **Controller** tab, select a pending
   controller command, and submit an **ACK** with its requested state. Only that
   direction becomes confirmed. Submit the rest before the deadline, or observe
   the safe timeout and recover again.
4. A **FAILED** response or mismatched reported signal demonstrates controller
   failure. Restore devices, enable automatic acknowledgements, and recover to
   return to the continuous demo.

## Junctions, history, and connection loss

- **Add junction** creates another configured junction and opens it. Duplicate IDs
  show the API's conflict message without closing the dialog. Each new junction
  needs its own controller confirmation. Use the sidebar to switch junctions; on
  narrow screens, open the navigation menu first.
- Recent activity shows the newest 30 backend entries. Filter traffic, control,
  or safety events and expand the list. Older entries remain available through
  the paginated history API.
- Stop the backend while viewing a loaded junction. The last snapshot stays
  visible with **Updates paused — controls are disabled**. Restarting the backend
  resumes polling; physical confirmation and simulator activation are required
  again. If the initial load fails, the screen shows an error and retry controls.
- An unknown junction URL shows the backend's error and a link to Junction A.
  If the junction list initially failed to load, use its separate **Try again**
  button after the backend is available.

## Verification completed for this checkpoint

The frontend tests cover polling ordering, route changes, stale and invalid data,
backend rejections, duplicate payloads, and UNKNOWN signal rendering. The optimized
Angular build and formatting checks pass. Live browser checks exercised arrivals,
duplicate retries, clearances, manual and emergency control, sensor failure and
recovery, duplicate/new junction creation, partial and batch ACKs, unknown routes,
backend disconnection/reconnection, and narrow-screen navigation.

The intersection confirmation caption also checks the current physical evidence.
If another fault invalidates previously confirmed signals, it shows UNKNOWN even
when the engine is no longer waiting for that batch's ACKs. Known signals that do
not match the requested state show MISMATCH. Regression tests cover both cases.
Controls for returning to automatic, clearing vehicles, resending events, and
batch acknowledgements appear when their corresponding action is available.

The [assessment walkthrough](assessment-demo.md) covers all nine required scenarios,
including a runnable live HTTP driver. [Submission notes](submission.md) record
the clean-source installation and full verification results. There is no automated
browser end-to-end suite or production deployment.
