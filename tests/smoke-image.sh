#!/usr/bin/env bash
# Exercise the release image on an isolated Docker daemon, without accounts.
set -euo pipefail
image=${1:?Usage: smoke-image.sh IMAGE}
container=
volume=
cleanup() {
  if [[ -n "$container" ]]; then docker rm -f "$container" >/dev/null; fi
  if [[ -n "$volume" ]]; then docker volume rm "$volume" >/dev/null; fi
}
trap cleanup EXIT

start() {
  container=$(docker run -d --network none -v "$volume:/data" -e "T3_BOAT_PROFILE=$profile" "$image")
  for _ in {1..60}; do
    if docker exec "$container" curl -fsS --max-time 2 http://127.0.0.1:3773/ -o /dev/null 2>/dev/null; then
      return
    fi
    if [[ $(docker inspect -f '{{.State.Running}}' "$container") != true ]]; then break; fi
    sleep 1
  done
  docker logs "$container" >&2
  return 1
}

for profile in codex-only claude-only codex+claude; do
  volume=$(docker volume create)
  start
  docker exec "$container" sh -ec 'test "$(id -u)" != 0 && test -w /data && test "$HOME" = /data'
  # A fresh install has no subscriptions or Boat project. Offline status must
  # report incomplete, even though the actual T3 backend is already serving.
  if docker exec "$container" t3-boat status; then
    echo "Unexpected ready status for empty $profile volume" >&2
    exit 1
  fi
  docker exec "$container" mkdir /data/smoke-project
  docker exec "$container" t3 project add /data/smoke-project
  role_file="/data/profiles/$profile.json"
  provider=$(docker exec "$container" jq -er .orchestrator.provider "$role_file")
  model=$(docker exec "$container" jq -er .orchestrator.model "$role_file")
  # No accounts, networking or worker VM: only the transcript is under test.
  thread=$(docker exec "$container" t3 thread start /data/smoke-project 'Offline persistence check' \
    --provider "$provider" --model "$model" --json | jq -er .threadId)
  docker exec "$container" t3 auth pairing create --ttl 2m --json >/dev/null
  environment=$(docker exec "$container" cat /data/.t3/userdata/environment-id)
  [[ -n "$environment" ]]
  docker stop "$container" >/dev/null
  docker rm "$container" >/dev/null
  container=
  start
  [[ $(docker exec "$container" cat /data/.t3/userdata/environment-id) = "$environment" ]]
  docker exec "$container" t3 thread list --project /data/smoke-project --json |
    jq -e --arg id "$thread" 'any(.[]; .threadId == $id)' >/dev/null
  cleanup
  container=
  volume=
done
