# Factory Traffic dashboard

Angular 21 interface for the FastAPI traffic controller. The backend remains the
authority for scheduling, signal timing, queues, and physical confirmations.

## Run

Start the backend on `127.0.0.1:8000` using the [root instructions](../README.md).
Use Node.js 20.19+, 22.12+, or 24.x; development was checked with Node.js 24.11.1.
Then run from this directory:

```sh
npm ci
npm start
```

Open [Junction A](http://127.0.0.1:4200/junctions/A). The development server proxies
`/api/**` to FastAPI, so browser requests use the same origin. Enable **Automatic
acknowledgements** to start the REST controller simulation after each backend
restart. Allow two seconds for confirmed all-red clearance before sending manual
traffic requests. Follow the [dashboard walkthrough](../docs/dashboard.md).

## Checks and build

```sh
npm test
npm run build
npm run format:check
```

Tests use Vitest and Angular's HTTP testing adapter. They check polling races,
staleness, rejected commands, duplicate events, invalid responses, and the
distinction between confirmed and requested signals. `npm run format` applies
Prettier to source and configuration files.

The production bundle is emitted to `dist/frontend/browser/`. A production static
server needs an `index.html` fallback for `/junctions/:id` and an `/api` reverse
proxy to FastAPI. The Angular development proxy does not ship with the bundle.
`vercel.json` configures the hosted dashboard and proxies API and documentation
requests to the `BACKEND_URL` environment variable. See the
[deployment guide](../docs/deployment.md) for the free hosting setup and limits.

## Structure and state

- `src/app/core/`: typed DTOs, HTTP API, runtime response guards, and the shared
  signal-based store. Polls run once a second without overlapping timer requests.
- `src/app/dashboard/`: junction metrics, an SVG intersection, manual controls,
  simulation inputs, and recent activity.
- `src/app/shared/`: navigation header, junction creation dialog, and SVG icons.
- `src/styles.scss`: shared form, card, button, and layout primitives.

Each status/history refresh is applied together. Request ordering and junction
identity prevent an older response from replacing a newer observation. Requests
time out after eight seconds; observations become stale after four seconds, and
a failed poll disables writes immediately. The last valid state remains visible
with an explicit stale label. UNKNOWN physical signals stay unknown until the
backend reports a matching confirmation.

The workbench generates unique event IDs and increasing per-direction sequence
numbers for demonstration. **Resend last event** reuses the complete original
payload to exercise server-side idempotency. The screen shows the latest 30 audit
entries; the API provides pagination for older history. Traffic decisions and
timers are never implemented in the browser.
