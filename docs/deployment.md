# Hosted demo

The assessment demo uses Vercel for the Angular dashboard, a free Render web
service for FastAPI, and a separate Neon Free PostgreSQL database in Singapore.
Local development continues to use SQLite without any cloud account.

The backend owns the timers and controller acknowledgements, so it runs as one
Uvicorn process. Vercel Functions have an ephemeral filesystem and do not provide
the continuous control loop this application uses. See
[Vercel runtimes](https://vercel.com/docs/functions/runtimes).

## Free hosting limits

The setup selects Vercel Hobby, Render Free, and Neon Free, with no paid disk or
compute upgrade. Free quotas and service availability still apply. Render sleeps
after 15 minutes without incoming traffic, and waking it can take about a minute.
The dashboard can briefly report a connection failure while that happens; it
keeps retrying and disables writes until observations are current.

An external database retains queues, receipts, sequences, and history across
Render restarts. Sleep or a restart interrupts traffic control. The backend
returns with physical signals UNKNOWN, requests fresh ALL RED, and requires
explicit simulator recovery. This is suitable for an assessment demonstration;
a factory controller needs continuous availability and a hardware watchdog.

Neon Free currently provides 1 GB per project and 100 CU-hours per project per
month. The control loop uses the database while Render is awake, so active demo
time counts toward that quota. Check provider usage before a long-running demo.
Do not add a paid plan automatically when a free quota is reached. Provider
details checked on 29 October 2026:
[Render Free](https://render.com/docs/free),
[Neon Free](https://neon.com/blog/neon-free-plan-1-gb-per-project).

## Backend

`backend/Dockerfile` installs locked production dependencies and starts one
Uvicorn worker. It uses the hosting provider's `PORT`, defaulting to 8000.
`deploy/render.yaml` describes the free Docker web service, its health check at
`/api/health`, and these environment variables:

- `DATABASE_URL`: the PostgreSQL connection string from Neon. Retain its TLS
  parameters, including `sslmode=require`. Store it only as a hosting secret.
- `FACTORY_TRAFFIC_REQUIRE_POSTGRES=1`: fail startup if `DATABASE_URL` is missing,
  rather than silently putting assessment data on Render's temporary filesystem.

Create the database separately on the Free plan. Auth and Realtime are not
needed. In Render, connect this repository and use `backend/Dockerfile` with
Docker context `backend`. Confirm the **Free** instance type before creating the
service. Do not attach a disk. Automatic redeployment is disabled to avoid
interrupting an active review; deploy changes deliberately.

Startup applies versioned PostgreSQL migrations. A small connection pool uses
transaction-scoped advisory locks for serialized writes and repeatable-read
snapshots for coherent reads. Domain rules and application services are shared
with the SQLite adapter. Neither adapter coordinates multiple timer owners;
keep a single backend process and replica.

To check the image locally with SQLite and a named volume:

```sh
docker build -t factory-traffic-api -f backend/Dockerfile backend
docker run --rm -p 127.0.0.1:8001:8000 -v factory-traffic-data:/data factory-traffic-api
```

The default image path is `/data/factory-traffic.sqlite3`. Reusing that volume
retains local data. Supplying `DATABASE_URL` selects PostgreSQL instead.

## Vercel dashboard

Create a Vercel project connected to this repository with **Root Directory** set
to `frontend`. `frontend/vercel.json` defines the Angular preset, `npm ci`,
`npm run build`, and output at `dist/frontend/browser`.

Set the project environment variable `BACKEND_URL` to the backend's HTTPS origin,
without a trailing slash or `/api` suffix. This origin is public; it is not the
database credential. Vercel forwards `/api/*`, `/docs`, and `/openapi.json` there.
Other application routes fall back to `index.html`. API responses are marked
`no-store`.

The browser requests its own Vercel origin, so no hard-coded hosting address or
cross-origin browser configuration is needed in Angular. The development proxy
provides the same paths locally. Restart the Angular development server after
changing its proxy configuration.

## Check the public URL

1. Open the production URL without signing in. Load `/junctions/A` directly and
   refresh it to check the route fallback.
2. Request `/api/health` through Vercel. It should return the backend's JSON
   liveness response; controller readiness is a separate concern.
3. Open API documentation from the sidebar and check that Swagger loads the
   OpenAPI document through the same origin.
4. Enable automatic acknowledgements, wait for fresh all-red clearance, add a
   vehicle, retry the same event, and clear it. Check queue and history updates.
5. Leave a vehicle queued, restart Render, and verify retained queue/history,
   UNKNOWN physical state, disabled automatic simulation, and fresh recovery.
6. Check that the public page and API work without account cookies or deployment
   protection before using the production URL in the submission form.

The public demo has no production authentication and uses simulated devices.
Use only demonstration data. Operator controls are public for assessment review.
Do not connect real factory equipment or store private operational information.

## PostgreSQL verification

The normal test suite uses temporary SQLite files and needs no server. Additional
adapter tests run only when `FACTORY_TRAFFIC_TEST_POSTGRES` points to a disposable
PostgreSQL database. They create a randomly named schema per test and drop that
schema afterward. Do not supply production credentials for this command.

From `backend/`, in PowerShell:

```powershell
$env:FACTORY_TRAFFIC_TEST_POSTGRES = 'postgresql://user:password@127.0.0.1:5432/traffic_test'
uv run pytest tests/hosted
Remove-Item Env:FACTORY_TRAFFIC_TEST_POSTGRES
```

These tests cover controller scenario parity, committed dispatch, restart,
concurrent global event IDs, atomic rollback, pagination, 64-bit sequences,
snapshot consistency, and bound text parameters.
