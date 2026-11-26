#!/usr/bin/env bash
# Exercise the release image on an isolated Docker daemon, without accounts.
set -euo pipefail
# shellcheck source=tests/smoke-lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/smoke-lib.sh"
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
  # Every installed t3-boat file is readable by the container user, whatever the build checkout's umask.
  unreadable=$(docker exec "$container" find /opt/t3-boat ! -perm -a+r -print -quit)
  [[ -z "$unreadable" ]] || { echo "Unreadable in the image: $unreadable" >&2; exit 1; }
  # A fresh install has no subscriptions or Boat project. Offline status must report incomplete (exit 1 and
  # the setup hint), even though the actual T3 backend is already serving; a docker failure is not that.
  incomplete_status "$container"
  if [[ "$profile" == codex-only ]]; then
    # The real server must return its provider snapshot even before login.
    docker exec "$container" python3 -c 'import sys; sys.path.insert(0, "/opt/t3-boat/lib"); from model_catalog import provider_snapshot; assert any(p["instanceId"] == "codex" for p in provider_snapshot("codex")["providers"])'
  fi
  case "$profile" in
    # No playbook: choose the lead model by number (the provider is the only one), then accept.
    codex-only) answers=$'\n2\n3\n\n\n\n' ;;
    # The agent-playbook copy built into the image: its lead role, the set named like the profile, its suggestion.
    claude-only) answers=$'bundled\n\n\n\n\n\n' ;;
    # A team that shares no role name, count or model set name with agent-playbook, attached from the volume.
    codex+claude)
      answers=$'/data/teams/crew\nharbor-master\nduo\n\n\n\n'
      docker exec -i "$container" sh -eu <<'SH'
team=/data/teams/crew
for role in harbor-master rigger qa_bot; do
  mkdir -p "$team/people/$role"
  printf '# %s\n\n## Who you are\n\nYou are the %s.\n\nINSTRUCTIONS-FOR-%s\n' "$role" "$role" "$role" > "$team/people/$role/ROLE.md"
done
printf 'Shared house rules.\n' > "$team/house-rules.md"
mkdir -p "$team/sets"
cat > "$team/team.json" <<'JSON'
{"schema": 1, "name": "harbor-crew", "version": "0.0.1",
 "roles": {
   "harbor-master": {"instances": 1, "bundle": ["people/harbor-master/ROLE.md", "house-rules.md"]},
   "rigger": {"instances": 3, "bundle": ["people/rigger/ROLE.md", "house-rules.md"]},
   "qa_bot": {"instances": 2, "bundle": ["people/qa_bot/ROLE.md", "house-rules.md"]}},
 "model_rules": [{"roles": ["qa_bot"], "must_differ_from": "rigger"}],
 "model_sets": {"duo": "sets/duo.json", "claude-solo": "sets/claude-solo.json"}}
JSON
cat > "$team/sets/duo.json" <<'JSON'
{"roles": {"harbor-master": {"provider": "claude", "family": "fable", "effort": "high"},
           "rigger": [{"provider": "codex", "family": "luna", "effort": "low"},
                      {"provider": "codex", "family": "sol", "effort": "medium"},
                      {"provider": "claude", "family": "haiku", "effort": "medium"}],
           "qa_bot": [{"provider": "claude", "family": "sonnet", "effort": "high"},
                      {"provider": "codex", "family": "astra", "effort": "high"}]}}
JSON
cat > "$team/sets/claude-solo.json" <<'JSON'
{"roles": {"harbor-master": {"provider": "claude", "family": "opus", "effort": "high"}}}
JSON
SH
      ;;
  esac
  # Exercise real setup, worker scripts and status against offline service doubles.
  docker exec -i -e "ANSWERS=$answers" "$container" sh -eu <<'SH'
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
printf '%s' "$ANSWERS" | t3-boat setup
test ! -e "$HOME/check-vm"
t3-boat status | grep -F 'Setup verified'
SH
  role_file="/data/profiles/$profile.json"
  lead_dir=$(docker exec "$container" sh -c 'test "$(jq -r .lead.provider "$1")" = codex && echo .codex || echo .claude' _ "$role_file")
  skill="/data/$lead_dir/skills/t3-boat/SKILL.md"
  note="/data/$lead_dir/$( [[ $lead_dir == .codex ]] && echo AGENTS.md || echo CLAUDE.md )"
  docker exec "$container" jq -e 'keys == ["lead", "playbook"]' "$role_file" >/dev/null
  status=$(status_output "$container")
  case "$profile" in
    codex-only)
      docker exec "$container" jq -e '.playbook == null and .lead.model == "gpt-6.2-astra" and .lead.effort == "high"' "$role_file" >/dev/null
      docker exec "$container" grep -Fq '## Workers and briefs' "$skill"
      [[ $(docker exec "$container" ls /data/.codex/skills) == t3-boat ]]
      require 't3-boat status' "$status" 'Playbook: none'
      worker_role=
      worker_text='You are a worker on a disposable Boat VM, started by the T3 team lead.'
      ;;
    claude-only)
      docker exec "$container" jq -e '.playbook == {"path": "/opt/t3-boat/playbook", "model_set": "claude-only", "lead_role": "lead"}' "$role_file" >/dev/null
      # Every role the package declares is installed, named from its manifest; T3 Boat lists none of its own.
      for role in $(docker exec "$container" jq -r '.roles | keys[]' /opt/t3-boat/playbook/team.json); do
        docker exec "$container" grep -Fq "name: agent-playbook-$role" "/data/.claude/skills/agent-playbook-$role/SKILL.md"
      done
      docker exec "$container" grep -Fq '## Attached playbook' "$skill"
      docker exec "$container" grep -Fq 'load and follow its lead guide at /data/.claude/skills/agent-playbook-lead/SKILL.md' "$note"
      require 't3-boat status' "$status" 'Playbook: agent-playbook (version '
      require 't3-boat status' "$status" 'commit a27b16a7d696) at /opt/t3-boat/playbook, model set claude-only, team lead follows lead'
      worker_role=$(docker exec "$container" jq -r '.roles | keys_unsorted | map(select(. != "lead"))[0]' /opt/t3-boat/playbook/team.json)
      worker_text=$(docker exec "$container" jq -r --arg role "$worker_role" '.roles[$role].bundle[0]' /opt/t3-boat/playbook/team.json)
      worker_text="<!-- file: $worker_text -->"
      ;;
    codex+claude)
      docker exec "$container" jq -e '.playbook == {"path": "/data/teams/crew", "model_set": "duo", "lead_role": "harbor-master"}
        and .lead == {"provider": "claudeAgent", "model": "claude-fable-5-2", "effort": "high"}' "$role_file" >/dev/null
      for base in .codex .claude; do
        for role in harbor-master rigger qa-bot; do
          docker exec "$container" test -f "/data/$base/skills/harbor-crew-$role/SKILL.md"
        done
        docker exec "$container" grep -Fq '| rigger | 3 |' "/data/$base/skills/t3-boat/SKILL.md"
        docker exec "$container" grep -Fq '| qa_bot | 2 |' "/data/$base/skills/t3-boat/SKILL.md"
        docker exec "$container" grep -Fq '| rigger | 3 | claudeAgent | haiku | medium |' "/data/$base/skills/t3-boat/SKILL.md"
      done
      docker exec "$container" grep -Fq 'load and follow its harbor-master guide' "$note"
      require 't3-boat status' "$status" 'Playbook: harbor-crew (version 0.0.1) at /data/teams/crew, model set duo, team lead follows harbor-master'
      worker_role=qa_bot
      worker_text='INSTRUCTIONS-FOR-qa_bot'
      ;;
  esac
  docker exec "$container" grep -Fq '## Machine boundary' "$skill"
  docker exec "$container" grep -Fq '## Parent updates' "$skill"
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
  # Start a worker from the bound stub against a Boat double: capture the VM setup script, then fail it.
  docker exec -i -e "ROLE=$worker_role" -e "TEXT=$worker_text" "$container" sh -eu <<'SH'
tools=$(mktemp -d)
cat > "$tools/boat" <<'MOCK'
#!/bin/sh
case "$1 $2" in
  'new --json') echo '{"event":"ready","id":"bx_worker"}' ;;
  'ssh bx_worker') printf '%s' "$3" > "$HOME/worker-script"; exit 1 ;;
  'delete --yes') test "$3" = bx_worker && touch "$HOME/worker-deleted" ;;
  *) exit 1 ;;
esac
MOCK
chmod +x "$tools/boat"
cd /data/start-here
if PATH="$tools:$PATH" boat-worker start $ROLE; then exit 1; fi
test -e "$HOME/worker-deleted"
grep -Fq -- "$TEXT" "$HOME/worker-script"
grep -Fq '"enabled": true' "$HOME/worker-script"
rm "$HOME/worker-script" "$HOME/worker-deleted"
SH
  broken=
  if [[ "$profile" == codex+claude ]]; then
    # The attached package disappears: T3 must still start, without the profile, and name the file.
    docker exec "$container" rm /data/teams/crew/team.json
    broken=1
  fi
  docker stop "$container" >/dev/null
  docker rm "$container" >/dev/null
  container=
  start
  if [[ -n "$broken" ]]; then
    logs=$(container_logs "$container")
    require 'docker logs' "$logs" '/data/teams/crew/team.json is missing. T3 starts without the codex+claude profile'
    status=$(incomplete_status "$container")
    require 't3-boat status' "$status" '[missing] Global profile codex+claude applied'
    require 't3-boat status' "$status" '/data/teams/crew/team.json is missing'
    docker exec "$container" jq -e '.playbook.path == "/data/teams/crew"' "$role_file" >/dev/null
  fi
  [[ $(docker exec "$container" cat /data/.t3/userdata/environment-id) = "$environment" ]]
  docker exec "$container" t3 thread list --project /data/start-here --json |
    jq -e --arg id "$thread" 'any(.[]; .threadId == $id)' >/dev/null
  cleanup
  container=
  volume=
done
