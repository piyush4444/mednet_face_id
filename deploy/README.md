# Office server operations

After pushing changes to `main`, SSH into the office server and run:

```bash
/home/pg/apps/mednet-face-id/update.sh
```

Use `update.sh --check` to fetch and show the latest revision without deploying.
The updater serializes deployments, downloads only committed code, retains
previous releases, and invokes `server-deploy.sh`. That script backs up PostgreSQL,
builds the images, seeds initial data, checks health, and updates `current` only
after success. On failure it attempts to restore the previous application images;
this does not undo database migrations. Secrets remain in `shared/stack.env`.
Existing account passwords are preserved by the seed process.

The `mednet-face-id.service` system service starts the last successful release
at boot without fetching code or requiring GitHub access. Docker's
`unless-stopped` policies recover exited containers. Docker and Tailscale must
remain enabled. Funnel's saved background configuration restores the public route.
A Docker healthcheck reports unhealthy containers but does not itself restart a
running, unhealthy process.

```bash
systemctl status mednet-face-id docker tailscaled
journalctl -u mednet-face-id -n 100 --no-pager
```

Initial installation (administrator): install `update.sh` and `start.sh` as
executable files in `/home/pg/apps/mednet-face-id`, and install the service unit
in `/etc/systemd/system/mednet-face-id.service`. After a successful deployment,
run `systemctl daemon-reload` and `systemctl enable --now mednet-face-id`.
Updates are manual; no deployment occurs merely because code was pushed.
