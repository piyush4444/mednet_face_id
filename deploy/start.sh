#!/usr/bin/env bash
set -Eeuo pipefail
deploy_root=/home/pg/apps/mednet-face-id
exec 9>"$deploy_root/update.lock"
flock -w 1800 9
release_dir="$(readlink -f "$deploy_root/current")"
[[ "$release_dir" == "$deploy_root/releases/"* && -f "$release_dir/deploy/compose.yaml" ]] || {
  echo "No valid deployed release found" >&2; exit 1;
}
export IMAGE_TAG="$(basename "$release_dir")"
docker compose --project-name mednet-face-id \
  --env-file "$deploy_root/shared/stack.env" \
  --project-directory "$release_dir" -f "$release_dir/deploy/compose.yaml" \
  up -d --no-build --pull never --wait --wait-timeout 900
