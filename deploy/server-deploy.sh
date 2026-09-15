#!/usr/bin/env bash
set -Eeuo pipefail

release_sha="${1:?release SHA is required}"
deploy_root="${2:-/home/pg/apps/mednet-face-id}"

case "$release_sha" in
  *[!0-9a-f]*|"") echo "Invalid release SHA" >&2; exit 2 ;;
esac

case "$deploy_root" in
  /home/pg/apps/*) ;;
  *) echo "Deploy root must stay under /home/pg/apps" >&2; exit 2 ;;
esac

release_dir="$deploy_root/releases/$release_sha"
stack_env="$deploy_root/shared/stack.env"
compose_file="$release_dir/deploy/compose.yaml"

if [[ ! -f "$compose_file" || ! -f "$stack_env" ]]; then
  echo "Release or shared stack.env is missing" >&2
  exit 2
fi

env_mode="$(stat -c '%a' "$stack_env")"
if (( (8#$env_mode & 077) != 0 )); then
  echo "Refusing to deploy: $stack_env must not be readable by group/others" >&2
  exit 2
fi

mkdir -p "$deploy_root/backups"
previous_release="$(readlink -f "$deploy_root/current" 2>/dev/null || true)"

export IMAGE_TAG="$release_sha"
export COMPOSE_PARALLEL_LIMIT=1
compose=(
  docker compose
  --project-name mednet-face-id
  --env-file "$stack_env"
  --project-directory "$release_dir"
  -f "$compose_file"
)

rollback() {
  exit_code=$?
  if (( exit_code == 0 )) || [[ -z "$previous_release" || ! -f "$previous_release/deploy/compose.yaml" ]]; then
    return "$exit_code"
  fi
  echo "Deployment failed; restoring previous application images" >&2
  export IMAGE_TAG="$(basename "$previous_release")"
  docker compose \
    --project-name mednet-face-id \
    --env-file "$stack_env" \
    --project-directory "$previous_release" \
    -f "$previous_release/deploy/compose.yaml" \
    up -d --wait --wait-timeout 600 || true
  return "$exit_code"
}
trap rollback EXIT

if docker ps --format '{{.Names}}' | grep -qx 'mednet-face-id-postgres-1'; then
  backup_path="$deploy_root/backups/postgres-$(date -u +%Y%m%dT%H%M%SZ).sql.gz"
  "${compose[@]}" exec -T postgres \
    sh -c 'pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB"' | gzip -9 > "$backup_path"
  echo "Database backup written to $backup_path"
fi

"${compose[@]}" build --pull backend web
"${compose[@]}" up -d --wait --wait-timeout 600 postgres
"${compose[@]}" run --rm backend python -m backend.scripts.seed_initial
"${compose[@]}" up -d --wait --wait-timeout 900

web_port="$(sed -n 's/^WEB_PORT=//p' "$stack_env" | tail -n 1)"
web_port="${web_port:-8083}"
case "$web_port" in
  *[!0-9]*) echo "Invalid WEB_PORT in stack.env" >&2; exit 2 ;;
esac

curl --fail --silent --show-error \
  "http://127.0.0.1:${web_port}/api/v1/ready" >/dev/null

ln -sfn "$release_dir" "$deploy_root/current.next"
mv -Tf "$deploy_root/current.next" "$deploy_root/current"
trap - EXIT

echo "Deployed $release_sha successfully"
