# Office test-server deployment

Iris is deployed beside the existing Edusphere stack as the independent
Compose project `mednet-face-id`. It uses its own containers and named volumes;
no Edusphere directory, container, network, port, or database is reused.

## Layout

- Server root: `/home/pg/apps/mednet-face-id`
- Releases: `/home/pg/apps/mednet-face-id/releases/<git-sha>`
- Current release: `/home/pg/apps/mednet-face-id/current`
- Server-only configuration: `shared/stack.env` (mode `600`)
- Pre-deploy PostgreSQL dumps: `backups/`
- Loopback preview: `http://127.0.0.1:8083`
- Public Funnel target: `https://mednet-test.tail89ab07.ts.net`

Persistent Docker volumes hold PostgreSQL, biometric face/media data, the
InsightFace model cache, and Tailscale node state. They survive application
release replacement.

## Manual deployment button

The workflow `.github/workflows/deploy-office.yml` uses `workflow_dispatch`.
After it is present on the default branch, open **Actions → Deploy office test
server → Run workflow**, choose a branch/tag/commit, and run it.

The workflow validates the frontend, Python syntax, and Compose file before it
joins the tailnet and uploads an immutable release over SSH. The server creates
a PostgreSQL dump, builds the new images, runs the idempotent initial seed,
waits for health checks, and updates the `current` symlink only after success.

## GitHub environment

Create the `office-testing` environment with these values:

Variables:

- `OFFICE_SSH_HOST=synoraserver`
- `OFFICE_SSH_USER=pg`
- `OFFICE_DEPLOY_ROOT=/home/pg/apps/mednet-face-id`

Secrets:

- `OFFICE_SSH_PRIVATE_KEY` — dedicated deployment key, never a personal key
- `OFFICE_SSH_KNOWN_HOSTS` — pinned host-key line for `synoraserver`
- `TS_OAUTH_CLIENT_ID` — Tailscale federated-identity client ID
- `TS_AUDIENCE` — Tailscale federated-identity audience

The Tailscale identity must be allowed to create ephemeral `tag:ci` nodes that
can reach SSH on `synoraserver`.

## First server setup

Create the release and shared directories as user `pg`, copy
`deploy/stack.env.example` to `shared/stack.env`, replace every placeholder,
and set mode `600`. Use URL-safe characters for `POSTGRES_PASSWORD` because it
is interpolated into `DATABASE_URL`.

The office server has no NVIDIA GPU, so its configuration must retain:

```env
GPU_ENABLED=false
GPU_DEVICE_ID=-1
```

Keep `ENABLE_FUNNEL=false` until the long-running `mednet-test` Tailscale node
credential has been created. Enabling the `funnel` Compose profile gives Iris a
separate Tailscale identity, avoiding the existing server node's occupied
Funnel ports 443, 8443, and 10000.

## Operational notes

- Do not run more than one Uvicorn worker while cameras are enabled.
- The first backend start can take several minutes while InsightFace downloads
  its model pack; the model volume prevents repeat downloads.
- `/api/v1/health` is liveness. `/api/v1/ready` also checks PostgreSQL.
- The reverse proxy disables buffering for WebSocket and MJPEG traffic.
- `/media/*` requires a valid application session at the proxy.
- Application rollback can restore the previous images, but startup schema
  changes are forward-only. Always retain the generated database backup.
