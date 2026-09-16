#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

deploy_root=/home/pg/apps/mednet-face-id
repository=https://github.com/piyush4444/mednet_face_id.git
cache="$deploy_root/repository.git"
case "${1:-}" in
  ''|--check) ;;
  *) echo "Usage: $0 [--check]" >&2; exit 2 ;;
esac
mkdir -p "$deploy_root/releases"
exec 9>"$deploy_root/update.lock"
flock -n 9 || { echo "Another deployment is running" >&2; exit 1; }
export GIT_TERMINAL_PROMPT=0
if [[ ! -d "$cache" ]]; then
  git clone --bare "$repository" "$cache"
fi
[[ "$(git --git-dir="$cache" remote get-url origin)" == "$repository" ]] || {
  echo "Unexpected deployment repository" >&2; exit 1;
}
git --git-dir="$cache" fetch origin refs/heads/main
release_sha="$(git --git-dir="$cache" rev-parse 'FETCH_HEAD^{commit}')"
echo "Latest main: $release_sha"
if [[ "${1:-}" == --check ]]; then
  echo "Current release: $(readlink "$deploy_root/current" || true)"
  exit 0
fi
release_dir="$deploy_root/releases/$release_sha"
if [[ ! -d "$release_dir" ]]; then
  staging="$(mktemp -d "$deploy_root/releases/.staging-XXXXXXXX")"
  git --git-dir="$cache" archive "$release_sha" | tar -x -C "$staging"
  mv -T "$staging" "$release_dir"
fi
bash "$release_dir/deploy/server-deploy.sh" "$release_sha" "$deploy_root"
echo "Public site: https://synoraserver.tail89ab07.ts.net/mednet/"
