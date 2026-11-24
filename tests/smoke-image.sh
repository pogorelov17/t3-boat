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
  if [[ "$profile" == codex-only ]]; then
    # The real server must return its provider snapshot even before login.
    docker exec "$container" python3 -c 'import sys; sys.path.insert(0, "/opt/t3-boat/lib"); from model_catalog import provider_snapshot; assert any(p["instanceId"] == "codex" for p in provider_snapshot("codex")["providers"])'
  fi
  # Exercise real setup, worker scripts and status against offline service doubles.
  docker exec -i "$container" sh -eu <<'SH'
auth_tools=$(mktemp -d)
cat > "$auth_tools/boat" <<'MOCK'
#!/bin/sh
case "$1 $2" in
  'status --check') exit 0 ;;
  'env list') echo '{"environments":[{"id":"env1","name":"test","safeForThirdParties":false,"passGithub":true,"passAgentsCredentials":true}]}' ;;
  'ai profiles') echo '{"profiles":[{"id":"creds","isDefault":true,"connected":["chatgpt-subscription","claude-subscription"]}]}' ;;
  'new --json') [ "${10}" = "--environment" ] && [ "${11}" = test ] || exit 1; touch "$HOME/check-vm"; echo '{"event":"ready","id":"bx_check"}' ;;
  'ssh bx_check') sh -ec "$3" ;;
  'delete --yes') test "$3" = bx_check || exit 1
    echo '{"operation":{"id":"bdop_mock","targetId":"bx_check","kind":"sandbox","status":"pending"}}' ;;
  'deletion status') test "$3" = bdop_mock && rm "$HOME/check-vm" || exit 1
    echo '{"operation":{"id":"bdop_mock","targetId":"bx_check","kind":"sandbox","status":"completed"}}' ;;
  *) exit 1 ;;
esac
MOCK
cat > "$auth_tools/codex" <<'MOCK'
#!/bin/sh
case "$1" in
  login) echo 'Logged in using ChatGPT' ;;
  exec) echo BOAT_OK > reply ;;
  *) exit 1 ;;
esac
MOCK
cat > "$auth_tools/claude" <<'MOCK'
#!/bin/sh
case "$1" in
  --version) echo 2.1.330 ;;
  auth) echo '{"loggedIn":true,"apiProvider":"firstParty","authMethod":"oauth_token"}' ;;
  -p) echo BOAT_OK ;;
  *) exit 1 ;;
esac
MOCK
printf '%s\n' '#!/bin/sh' 'exit 0' > "$auth_tools/install-claude"
printf '%s\n' '#!/bin/sh' 'test "$1" = api' > "$auth_tools/gh"
# Only the provider snapshot is simulated. Keep T3 and its local authentication real.
cat > "$auth_tools/node" <<'MOCK'
#!/bin/sh
if [ "$1" != /opt/t3-boat/lib/model_snapshot.mjs ]; then exec /usr/local/bin/node "$@"; fi
cat >/dev/null
exec python3 - "$3" <<'PYTHON'
import json, sys
provider = sys.argv[1]
if provider == "codex":
    models = ["gpt-6.2-sol", "gpt-6.2-astra", "gpt-6-luna"]
    option = "reasoningEffort"
else:
    models = ["claude-opus-5-5", "claude-fable-5-2", "claude-haiku-5-5", "claude-sonnet-5-5"]
    option = "effort"
levels = [{"id": effort, "label": effort} for effort in ("low", "medium", "high", "xhigh")]
caps = {"optionDescriptors": [{"id": option, "type": "select", "options": levels}]}
rows = [{"slug": model, "name": model, "capabilities": caps} for model in models]
print(json.dumps({"providers": [{"instanceId": provider, "driver": provider, "installed": True,
    "enabled": True, "status": "ready", "auth": {"status": "authenticated"}, "models": rows}]}))
PYTHON
MOCK
chmod +x "$auth_tools"/*
export PATH="$auth_tools:$PATH"
printf '\n\n\n' | t3-boat setup
test ! -e "$HOME/check-vm"
t3-boat status | grep -F 'Setup verified'
SH
  role_file="/data/profiles/$profile.json"
  provider=$(docker exec "$container" jq -er .lead.provider "$role_file")
  model=$(docker exec "$container" jq -er .lead.model "$role_file")
  # No accounts, networking or worker VM: only the transcript is under test.
  thread=$(docker exec "$container" t3 thread start /data/start-here 'Help me plan a small app' \
    --provider "$provider" --model "$model" --json | jq -er .threadId)
  docker exec "$container" t3 auth pairing create --ttl 2m --json >/dev/null
  environment=$(docker exec "$container" cat /data/.t3/userdata/environment-id)
  [[ -n "$environment" ]]
  docker exec "$container" python3 -c 'import runpy; runpy.run_path("/opt/t3-boat/bin/t3-boat")["register"]("sample/new-app", "test")'
  [[ $(docker exec "$container" git -C /data/start-here remote get-url origin) = https://github.com/sample/new-app.git ]]
  docker stop "$container" >/dev/null
  docker rm "$container" >/dev/null
  container=
  start
  [[ $(docker exec "$container" cat /data/.t3/userdata/environment-id) = "$environment" ]]
  docker exec "$container" t3 thread list --project /data/start-here --json |
    jq -e --arg id "$thread" 'any(.[]; .threadId == $id)' >/dev/null
  cleanup
  container=
  volume=
done
