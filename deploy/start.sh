#!/usr/bin/env bash
set -Eeuo pipefail
deploy_root=/home/pg/apps/mednet-face-id
exec 9>"$deploy_root/update.lock"
flock -w 1800 9
# Start the installed containers exactly as deployed, without rebuilding,
# recreating them, or changing their image versions during boot.
containers=(mednet-face-id-postgres-1 mednet-face-id-backend-1 mednet-face-id-web-1)
docker inspect "${containers[@]}" >/dev/null
docker start "${containers[@]}"
for ((attempt=0; attempt<180; attempt++)); do
  healthy=true
  for container in "${containers[@]}"; do
    status="$(docker inspect --format '{{.State.Health.Status}}' "$container")"
    [[ "$status" == healthy ]] || healthy=false
  done
  if [[ "$healthy" == true ]]; then
    echo "All Mednet containers are healthy"
    exit 0
  fi
  sleep 5
done
echo "Mednet containers did not become healthy within 15 minutes" >&2
exit 1
