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
- Public Funnel target: `https://synoraserver.tail89ab07.ts.net/mednet/`

Persistent Docker volumes hold PostgreSQL, biometric face/media data, the
InsightFace model cache. They survive application release replacement. The
server's existing Tailscale node owns the public `/mednet` route; Iris does not
create another tailnet device.

## Manual deployment button

The workflow `.github/workflows/deploy-office.yml` uses `workflow_dispatch`.
After it is present on the default branch, open **Actions → Deploy office test
server → Run workflow**, choose a branch/tag/commit, and run it.

The workflow validates the frontend, Python syntax, and Compose file on a
GitHub-hosted runner. Its deploy job is then picked up by the dedicated
`synoraserver-mednet` self-hosted runner and creates the immutable release
locally. The server creates a PostgreSQL dump, builds the new images, runs the
idempotent initial seed, waits for health checks, and updates the `current`
symlink only after success.

## GitHub environment

Create the `office-testing` environment with these values:

Variables:

- `OFFICE_DEPLOY_ROOT=/home/pg/apps/mednet-face-id`

The deploy job does not need SSH or Tailscale credentials. Repository access is
held by the self-hosted runner registration under `/home/pg/actions-runner-mednet`.
The runner starts immediately as user `pg` and has an `@reboot` crontab entry
because this host does not enable systemd user lingering.

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

The public route shares HTTPS port 443 without replacing the existing root
handler:

```bash
tailscale funnel --bg --yes --https=443 --set-path=/mednet \
  http://127.0.0.1:8083
```

Tailscale strips `/mednet` before proxying to nginx. The production frontend
therefore uses `/mednet/` as its asset/router base and `/mednet/api/v1` as its
browser-visible API base.

## Operational notes

- Do not run more than one Uvicorn worker while cameras are enabled.
- The first backend start can take several minutes while InsightFace downloads
  its model pack; the model volume prevents repeat downloads.
- `/api/v1/health` is liveness. `/api/v1/ready` also checks PostgreSQL.
- The reverse proxy disables buffering for WebSocket and MJPEG traffic.
- `/media/*` requires a valid application session at the proxy.
- Application rollback can restore the previous images, but startup schema
  changes are forward-only. Always retain the generated database backup.
