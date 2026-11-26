#!/usr/bin/env bash
# Exercise the release image on an isolated Docker daemon, without accounts.
#
# KIND is `generic` for the default image, which must contain no playbook, or `bundled` for an image built with
# BUNDLE_PLAYBOOK=1. Every kind runs a clean installation without a playbook and installations with a package
# attached from the volume, copied in from the host exactly as the README describes; `bundled` also attaches the
# built-in agent-playbook.
set -euo pipefail
# shellcheck source=tests/smoke-lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/smoke-lib.sh"
image=${1:?Usage: smoke-image.sh IMAGE generic|bundled}
kind=${2:?Usage: smoke-image.sh IMAGE generic|bundled}
[[ $kind == generic || $kind == bundled ]] || { echo "KIND must be generic or bundled" >&2; exit 2; }
container=
volume=
host_dir=$(mktemp -d)
cleanup() {
  if [[ -n "$container" ]]; then docker rm -f "$container" >/dev/null; fi
  if [[ -n "$volume" ]]; then docker volume rm "$volume" >/dev/null; fi
}
remove_host_dir() {  # Files the container user wrote into a bind mount may belong to another uid on the host.
  docker run --rm -u 0 --network none --entrypoint sh -v "$host_dir:/host" "$image" -c 'rm -rf /host/* /host/.[!.]*' \
    >/dev/null 2>&1 || true
  rm -rf "$host_dir"
}
trap 'cleanup; remove_host_dir' EXIT
# The suggested playbook's real pinned release, for the generic image: downloaded on this host by the same code the
# image runs, because the containers here have no network. The bundled image already holds it.
lib=$(cd "$(dirname "${BASH_SOURCE[0]}")/../lib" && pwd)
pinned_commit=$(python3 -I -c 'import sys; sys.path.insert(0, sys.argv[1]); import playbook_pin; print(playbook_pin.COMMIT)' "$lib")
release_name=agent-playbook-${pinned_commit:0:12}
if [[ $kind == generic ]]; then
  python3 -I -c 'import sys; sys.path.insert(0, sys.argv[1]); import playbook_fetch; playbook_fetch.load(sys.argv[2])' \
    "$lib" "$host_dir"
  [[ -f $host_dir/$release_name/.commit ]]
fi

# The image contains a playbook exactly when it was built to bundle one.
if docker run --rm --network none --entrypoint test "$image" -e /opt/t3-boat/playbook; then
  [[ $kind == bundled ]] || { echo "The generic image contains /opt/t3-boat/playbook" >&2; exit 1; }
else
  [[ $kind == generic ]] || { echo "The bundled image lacks /opt/t3-boat/playbook" >&2; exit 1; }
fi

# Write a package to $host_dir/NAME the way an owner would before `docker cp`.
crew_package() {  # Roles harbor-master x1, rigger x3, qa_bot x2; model sets duo and claude-solo.
  local team=$host_dir/crew
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
}

t3_package() {  # Package `t3` with role `boat`, whose plain name would be the Boat skill's own, and qa_bot / QA-bot.
  local team=$host_dir/t3
  mkdir -p "$team/roles" "$team/sets"
  for role in boat qa_bot QA-bot; do
    printf '# %s\n\nINSTRUCTIONS-FOR-%s\n' "$role" "$role" > "$team/roles/$role.md"
  done
  cat > "$team/team.json" <<'JSON'
{"schema": 1, "name": "t3",
 "roles": {"boat": {"instances": 1, "bundle": ["roles/boat.md"]},
           "qa_bot": {"instances": 1, "bundle": ["roles/qa_bot.md"]},
           "QA-bot": {"instances": 2, "bundle": ["roles/QA-bot.md"]}},
 "model_sets": {"solo": "sets/solo.json"}}
JSON
  printf '%s\n' '{"roles": {"boat": {"provider": "claude", "family": "opus", "effort": "high"}}}' > "$team/sets/solo.json"
}

# Copy a package written on the host under a restrictive umask into the container with the README's commands.
# docker cp keeps the host user's numeric owner and modes, so only the README's chown makes it readable when that
# user is not the container's (as on CI runners).
copy_package() {  # NAME
  docker cp "$host_dir/$1/." "$container:/data/teams/$1"
  docker exec -u 0 "$container" chown -R node:node "/data/teams/$1"
  foreign=$(docker exec "$container" find "/data/teams/$1" ! -user node -print -quit)
  [[ -z "$foreign" ]] || { echo "not owned by the container user after the README's chown: $foreign" >&2; exit 1; }
}

restart() {
  docker stop "$container" >/dev/null
  docker rm "$container" >/dev/null
  container=
  start
}
extra=()  # More `docker run` options, such as bind mounts.
start() {
  container=$(docker run -d --network none -v "$volume:/data" "${extra[@]}" -e "T3_BOAT_PROFILE=$profile" "$image")
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

# Run real setup with ANSWERS on stdin, against offline service doubles for Boat, the providers and their model
# lists; its output is kept in /data/setup-output.
run_setup() {  # ANSWERS
  docker exec -i -e "ANSWERS=$1" "$container" sh -eu <<'SH'
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
printf '%s' "$ANSWERS" | t3-boat setup > "$HOME/setup-output"
test ! -e "$HOME/check-vm"
t3-boat status | grep -F 'Setup verified'
SH
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
    # The suggested playbook cannot be downloaded without a network: setup says so, changes nothing and asks
    # again; Enter then attaches none. Choose the lead model by number (the provider is the only one), then accept.
    codex-only) answers=$'agent-playbook\n\n2\n3\n\n\n\n' ;;
    # Every choice is explicit: the role for the team lead and the model set, then the suggested lead model.
    claude-only)
      if [[ $kind == bundled ]]; then
        answers=$'bundled\nlead\nclaude-only\n\n\n\n'
      else
        (umask 077; t3_package)
        copy_package t3
        answers=$'/data/teams/t3\nboat\nsolo\n\n\n\n'
      fi ;;
    # A team that shares no role name, count or model set name with agent-playbook, copied in from the host
    # with the README's exact command into the directory every container start provides.
    codex+claude)
      (umask 077; crew_package)
      copy_package crew
      echo " " >> "$host_dir/crew/team.json"
      copy_package crew
      docker exec "$container" cat /data/teams/crew/team.json | cmp - "$host_dir/crew/team.json"
      answers=$'/data/teams/crew\nharbor-master\nduo\n\n\n\n' ;;
  esac
  # Exercise real setup, worker scripts and status against offline service doubles.
  run_setup "$answers"
  role_file="/data/profiles/$profile.json"
  lead_dir=$(docker exec "$container" sh -c 'test "$(jq -r .lead.provider "$1")" = codex && echo .codex || echo .claude' _ "$role_file")
  skill="/data/$lead_dir/skills/t3-boat/SKILL.md"
  note="/data/$lead_dir/$( [[ $lead_dir == .codex ]] && echo AGENTS.md || echo CLAUDE.md )"
  docker exec "$container" jq -e 'keys == ["lead", "playbook"]' "$role_file" >/dev/null
  status=$(status_output "$container")
  setup_output=$(docker exec "$container" cat /data/setup-output)
  # No role or model set is preselected by its name.
  [[ $setup_output != *"follows ('none' for no role) ["* ]] || { echo "setup preselected a role" >&2; exit 1; }
  if [[ $kind == generic ]]; then
    [[ $setup_output != *"'bundled'"* ]] || { echo "the generic image offered a bundled playbook" >&2; exit 1; }
  else
    require 't3-boat setup' "$setup_output" "'bundled' for agent-playbook"
  fi
  case "$profile" in
    codex-only)
      docker exec "$container" jq -e '.playbook == null and .lead.model == "gpt-6.2-astra" and .lead.effort == "high"' "$role_file" >/dev/null
      docker exec "$container" grep -Fq '## Workers and briefs' "$skill"
      [[ $(docker exec "$container" ls /data/.codex/skills) == t3-boat ]]
      # Generic instructions set no delivery form of their own.
      generic_text=$(docker exec "$container" cat "$skill" /data/.codex/AGENTS.md)
      [[ ! ${generic_text,,} =~ draft|pull\ request|task\ branch ]] ||
        { echo "generic instructions name a branch or pull request rule: ${BASH_REMATCH[0]}" >&2; exit 1; }
      require 't3-boat status' "$status" 'Playbook: none'
      require 't3-boat setup' "$setup_output" "Suggested: agent-playbook, https://github.com/pogorelov17/agent-playbook at commit a27b16a7d696."
      require 't3-boat setup' "$setup_output" "Enter for none, 'agent-playbook' for the suggested playbook"
      require 't3-boat setup' "$setup_output" 'agent-playbook could not be downloaded: git fetch failed'
      require 't3-boat setup' "$setup_output" "Nothing was changed. Check this server's network and enter 'agent-playbook' to retry"
      [[ -z $(docker exec "$container" ls /data/teams) ]] || { echo "a failed download left files in /data/teams" >&2; exit 1; }
      # The image's git downloads a pinned commit as plain data, here from a repository inside the container.
      docker exec "$container" sh -eu <<'SH'
repository=$(mktemp -d)
mkdir "$repository/guides"
printf '# deckhand\n\nINSTRUCTIONS-FOR-deckhand\n' > "$repository/guides/deckhand.md"
printf '#!/bin/sh\ntouch /data/package-code-ran\n' > "$repository/check.sh"
chmod 755 "$repository/check.sh"
printf '{"schema": 1, "name": "deck-crew", "roles": {"deckhand": {"instances": 1, "bundle": ["guides/deckhand.md"]}}}' > "$repository/team.json"
export GIT_CONFIG_GLOBAL=/dev/null GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@t GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@t
git -C "$repository" init --quiet && git -C "$repository" add -A && git -C "$repository" commit --quiet -m package
commit=$(git -C "$repository" rev-parse HEAD)
teams=$(mktemp -d)
python3 - "$teams" "$repository" "$commit" <<'PYTHON'
import hashlib, os, subprocess, sys
sys.path.insert(0, "/opt/t3-boat/lib")
import playbook, playbook_fetch, playbook_pin
teams, repository, commit = sys.argv[1:]
def git(*args):
    return subprocess.run(["git", "-C", repository, *args], check=True, capture_output=True).stdout
files = {}
for entry in filter(None, git("ls-tree", "-r", "-z", commit).split(b"\0")):
    meta, _, name = entry.partition(b"\t")
    mode, _, blob = meta.decode().split()
    files[name.decode()] = (mode, blob, hashlib.sha256(git("cat-file", "blob", blob)).hexdigest())
pin = playbook_pin.Pin(commit, git("rev-parse", commit + "^{tree}").decode().strip(), files)
package = playbook_fetch.load(teams, repository="file://" + repository, pin=pin)
target = package.root
assert package.pin == commit and list(package.roles) == ["deckhand"], package.label()
assert "INSTRUCTIONS-FOR-deckhand" in package.bundle("deckhand")
assert not os.access(target / "check.sh", os.X_OK)
assert sorted(os.listdir(teams)) == [target.name]
# Reuse runs no git; a changed file is then refused, named, and left as it is.
def offline(*args, **kwargs):
    raise AssertionError("git ran")
playbook_fetch._git = offline
assert playbook_fetch.load(teams, pin=pin).pin == commit
(target / "guides/deckhand.md").write_text("EDITED\n")
try:
    playbook_fetch.load(teams, pin=pin)
    raise AssertionError("a changed release was accepted")
except playbook_fetch.Mismatch as error:
    assert "guides/deckhand.md differs from the pinned commit" in str(error), error
assert (target / "guides/deckhand.md").read_text() == "EDITED\n"
assert f"unverified commit {commit[:12]}" in playbook.load(str(target), pin).label()
PYTHON
test ! -e /data/package-code-ran
rm -rf "$repository" "$teams"
SH
      worker_role=
      worker_text='You are a worker on a disposable Boat VM, started by the T3 team lead.'
      ;;
    claude-only)
      if [[ $kind == bundled ]]; then
        docker exec "$container" jq -e '.playbook == {"path": "/opt/t3-boat/playbook", "model_set": "claude-only", "lead_role": "lead"}' "$role_file" >/dev/null
        # Every role the package declares is installed, named from its manifest; T3 Boat lists none of its own.
        for role in $(docker exec "$container" jq -r '.roles | keys[]' /opt/t3-boat/playbook/team.json); do
          docker exec "$container" sh -c 'grep -Fq "name: playbook-agent-playbook-$1-" /data/.claude/skills/playbook-agent-playbook-"$1"-*/SKILL.md' _ "$role"
        done
        docker exec "$container" grep -Fq '## Attached playbook' "$skill"
        docker exec "$container" grep -Eq 'load and follow its lead guide at /data/.claude/skills/playbook-agent-playbook-lead-[0-9a-f]{8}/SKILL.md' "$note"
        require 't3-boat status' "$status" 'Playbook: agent-playbook (version '
        require 't3-boat status' "$status" 'commit a27b16a7d696) at /opt/t3-boat/playbook, model set claude-only, team lead follows lead'
        worker_role=$(docker exec "$container" jq -r '.roles | keys_unsorted | map(select(. != "lead"))[0]' /opt/t3-boat/playbook/team.json)
        worker_text=$(docker exec "$container" jq -r --arg role "$worker_role" '.roles[$role].bundle[0]' /opt/t3-boat/playbook/team.json)
        worker_text="<!-- file: $worker_text -->"
      else
        docker exec "$container" jq -e '.playbook == {"path": "/data/teams/t3", "model_set": "solo", "lead_role": "boat"}
          and .lead == {"provider": "claudeAgent", "model": "claude-opus-5-5", "effort": "high"}' "$role_file" >/dev/null
        # The package's `boat` role cannot replace the Boat skill; qa_bot and QA-bot keep separate guides.
        docker exec "$container" grep -Fq '# Boat project workflow' "$skill"
        guides=$(docker exec "$container" sh -c 'ls -d /data/.claude/skills/playbook-t3-*' | wc -l)
        [[ $guides == 3 ]] || { echo "expected 3 guides for package t3, found $guides" >&2; exit 1; }
        docker exec "$container" sh -c 'grep -lq INSTRUCTIONS-FOR-QA-bot /data/.claude/skills/playbook-t3-qa-bot-*/SKILL.md'
        docker exec "$container" sh -c 'grep -lq INSTRUCTIONS-FOR-qa_bot /data/.claude/skills/playbook-t3-qa-bot-*/SKILL.md'
        docker exec "$container" grep -Eq 'load and follow its boat guide at /data/.claude/skills/playbook-t3-boat-[0-9a-f]{8}/SKILL.md' "$note"
        require 't3-boat status' "$status" 'Playbook: t3 at /data/teams/t3, model set solo, team lead follows boat'
        worker_role=QA-bot
        worker_text='INSTRUCTIONS-FOR-QA-bot'
      fi
      ;;
    codex+claude)
      docker exec "$container" jq -e '.playbook == {"path": "/data/teams/crew", "model_set": "duo", "lead_role": "harbor-master"}
        and .lead == {"provider": "claudeAgent", "model": "claude-fable-5-2", "effort": "high"}' "$role_file" >/dev/null
      for base in .codex .claude; do
        for role in harbor-master rigger qa-bot; do
          docker exec "$container" sh -c 'test -f "$1"/skills/playbook-harbor-crew-"$2"-*/SKILL.md' _ "/data/$base" "$role"
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
  # The machine boundary and the factual parent updates hold in every mode.
  docker exec "$container" grep -Fq '## Machine boundary' "$skill"
  docker exec "$container" grep -Fq '## Parent updates' "$skill"
  docker exec "$container" grep -Fq 'These updates take the same form with or without a playbook' "$skill"
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
  'ssh bx_worker') cat > "$HOME/worker-script"; exit 1 ;;
  'delete --yes') test "$3" = bx_worker && touch "$HOME/worker-deleted" ;;
  *) exit 1 ;;
esac
MOCK
chmod +x "$tools/boat"
cd /data/start-here
if PATH="$tools:$PATH" boat-worker start $ROLE; then exit 1; fi
test -e "$HOME/worker-deleted"
grep -Fq -- "$TEXT" "$HOME/worker-script"
grep -Fq 'Your result for the lead states only what you observed' "$HOME/worker-script"
grep -Fq '"enabled": true' "$HOME/worker-script"
rm "$HOME/worker-script" "$HOME/worker-deleted"
SH
  if [[ "$profile" == codex+claude ]]; then
    # Damage the package in turn: a file the container user cannot read, a role name holding the instruction
    # files' END marker, a FIFO in place of the manifest, then no manifest. Each time T3 still starts, with only a
    # note in place of the profile and no Boat skill, guide or worker; the log and status name the file. Restoring
    # the package and restarting applies the profile again.
    crew=/data/teams/crew
    docker exec -u 0 "$container" cp -p "$crew/team.json" /data/team.json.orig
    for damage in unreadable marker fifo missing; do
      case $damage in
        unreadable)
          docker exec -u 0 "$container" chmod 000 "$crew/people/qa_bot/ROLE.md"
          message="$crew/people/qa_bot/ROLE.md: cannot be read: Permission denied" ;;
        marker)
          docker exec -u 0 "$container" sh -ec 'jq ".roles[\"<!-- END t3-boat -->\"] = .roles.qa_bot | del(.roles.qa_bot)" \
            /data/team.json.orig > "$1/team.json"' _ "$crew"
          message="$crew/team.json: role '<!-- END t3-boat -->' needs a name" ;;
        fifo)
          docker exec -u 0 "$container" sh -ec 'rm "$1/team.json"; mkfifo -m 644 "$1/team.json"' _ "$crew"
          message="$crew/team.json is not a regular file" ;;
        missing)
          docker exec -u 0 "$container" rm "$crew/team.json"
          message="$crew/team.json is missing" ;;
      esac
      # The marker case is started twice: an earlier note quoting the marker broke the second start.
      for attempt in 1 2; do
        restart
        [[ $damage == marker ]] || break
      done
      logs=$(container_logs "$container")
      require 'docker logs' "$logs" "$message"
      require 'docker logs' "$logs" "T3 starts without the codex+claude profile"
      status=$(incomplete_status "$container")
      require 't3-boat status' "$status" '[missing] Global profile codex+claude applied'
      require 't3-boat status' "$status" "$message"
      docker exec "$container" jq -e '.playbook.path == "/data/teams/crew"' "$role_file" >/dev/null
      for base in .codex .claude; do
        # T3 Boat's own skills only; Codex keeps its built-in ones under skills/.system.
        left=$(docker exec "$container" sh -c 'ls -d "$1"/t3-boat "$1"/playbook-* 2>/dev/null; true' _ "/data/$base/skills")
        [[ -z $left ]] || { echo "skills left after the package broke: $left" >&2; exit 1; }
      done
      for file in .codex/AGENTS.md .claude/CLAUDE.md; do
        text=$(docker exec "$container" cat "/data/$file")
        require "/data/$file" "$text" 'T3 Boat is not ready'
        require "/data/$file" "$text" 't3-boat status` name the file'
        # The diagnostic stays out of the managed block: one BEGIN and one END marker.
        [[ $text != *"$message"* ]] || { echo "/data/$file quotes the diagnostic" >&2; exit 1; }
        [[ $(grep -c -- '<!-- END t3-boat -->' <<<"$text") == 1 ]] || { echo "/data/$file: marker block broken" >&2; exit 1; }
      done
      docker exec "$container" test ! -e /data/profiles/codex+claude.applied.json
      refused=$(docker exec -w /data/start-here "$container" boat-worker start 2>&1) && exit 1
      require 'boat-worker start' "$refused" 'profile is not applied, so no worker can start'
      [[ $damage != missing ]] || break
      if [[ $damage == unreadable ]]; then
        docker exec -u 0 "$container" chmod 644 "$crew/people/qa_bot/ROLE.md"
      else
        docker exec -u 0 "$container" sh -ec 'rm -f "$1/team.json"; cp -p /data/team.json.orig "$1/team.json"' _ "$crew"
      fi
      restart
      status=$(status_output "$container")
      require 't3-boat status' "$status" '[ok] Global profile codex+claude applied'
      docker exec "$container" grep -Fq '| qa_bot | 2 |' /data/.claude/skills/t3-boat/SKILL.md
      docker exec "$container" test -f /data/profiles/codex+claude.applied.json
    done
  elif [[ "$profile" == codex-only ]]; then
    # Claude's skill folder linked to Codex's: the disabled provider's path reaches the active Boat skill, which
    # must survive the restart. An owner's skill quoting the generated marker is kept as well.
    docker exec "$container" sh -ec 'mkdir -p /data/.claude && ln -s /data/.codex/skills /data/.claude/skills
      mkdir /data/.codex/skills/notes
      printf "%s\n" "---" "name: notes" "description: mine" "---" "" "$1" > /data/.codex/skills/notes/SKILL.md' _ \
      '<!-- t3-boat: generated from the T3 Boat profile. t3-boat replaces and removes this file. -->'
    restart
    restart
    status=$(status_output "$container")
    require 't3-boat status' "$status" '[ok] Global profile codex-only applied'
    docker exec "$container" grep -Fq '## Workers and briefs' /data/.claude/skills/t3-boat/SKILL.md
    docker exec "$container" test -L /data/.claude/skills
    docker exec "$container" grep -Fq 'name: notes' /data/.codex/skills/notes/SKILL.md
  elif [[ "$profile" == claude-only ]]; then
    # An owner's Boat skill for the provider this profile does not enable stays, even quoting the marker.
    docker exec "$container" sh -ec 'mkdir -p /data/.codex/skills/t3-boat
      printf "%s\n" "---" "name: t3-boat" "description: mine" "---" "" "Mine. $1" > /data/.codex/skills/t3-boat/SKILL.md' _ \
      '<!-- t3-boat: generated from the T3 Boat profile. t3-boat replaces and removes this file. -->'
    restart
    docker exec "$container" grep -Fq 'description: mine' /data/.codex/skills/t3-boat/SKILL.md
    status=$(status_output "$container")
    require 't3-boat status' "$status" '[ok] Global profile claude-only applied'
  fi
  [[ $(docker exec "$container" cat /data/.t3/userdata/environment-id) = "$environment" ]]
  docker exec "$container" t3 thread list --project /data/start-here --json |
    jq -e --arg id "$thread" 'any(.[]; .threadId == $id)' >/dev/null
  cleanup
  container=
  volume=
done

# The real pinned release in a fresh codex-only volume, offline: setup checks every file against the pin and
# attaches it without git. A changed copy is refused and left as it is; attached by its path it is labelled
# unverified.
profile=codex-only
volume=$(docker volume create)
start
release=/data/teams/$release_name
if [[ $kind == generic ]]; then
  copy_package "$release_name"
else
  docker exec "$container" cp -R /opt/t3-boat/playbook "$release"
fi
run_setup $'agent-playbook\nlead\ncodex-only\n\n\n\n'
setup_output=$(docker exec "$container" cat /data/setup-output)
require 't3-boat setup' "$setup_output" "Checking $release against the pinned commit $pinned_commit (offline)"
require 't3-boat setup' "$setup_output" "Attaching agent-playbook (version 2.0.0, commit ${pinned_commit:0:12}) at $release"
[[ $setup_output != *Downloading* ]] || { echo "setup downloaded although the release was there" >&2; exit 1; }
status=$(status_output "$container")
require 't3-boat status' "$status" '[ok] Global profile codex-only applied'
require 't3-boat status' "$status" "Playbook: agent-playbook (version 2.0.0, commit ${pinned_commit:0:12}) at $release, model set codex-only, team lead follows lead"
docker exec "$container" sh -c 'printf "TAMPERED\n" >> "$1/roles/lead/ROLE.md"' _ "$release"
listing='cd "$1" && find . -printf "%y %m %p %l\n" | sort && find . -type f -exec sha256sum {} + | sort'
before=$(docker exec "$container" sh -c "$listing" _ "$release")
refused=$(printf 'agent-playbook\n\n' | docker exec -i "$container" python3 -c \
  'import runpy; print(runpy.run_path("/opt/t3-boat/bin/t3-boat")["choose_playbook"]("codex-only", None))')
require 'choose_playbook' "$refused" "agent-playbook was not attached: $release does not hold the pinned commit ${pinned_commit:0:12}: roles/lead/ROLE.md differs from the pinned commit"
require 'choose_playbook' "$refused" '(None, None)'
[[ $(docker exec "$container" sh -c "$listing" _ "$release") == "$before" ]] ||
  { echo "the changed release was modified" >&2; exit 1; }
restart
status=$(status_output "$container")
require 't3-boat status' "$status" '[ok] Global profile codex-only applied'
require 't3-boat status' "$status" "Playbook: agent-playbook (version 2.0.0, unverified commit ${pinned_commit:0:12}) at $release"
docker exec "$container" grep -Fq "unverified commit ${pinned_commit:0:12}" /data/.codex/skills/t3-boat/SKILL.md
cleanup
container=
volume=

# One host folder bind-mounted at both providers' skill folders, as `docker run -v DIR:/data/.codex/skills -v
# DIR:/data/.claude/skills` does: two paths with different realpaths that are one folder. For each profile, and so
# with either provider's path disabled, attach a package, refresh it unchanged and edited, then detach it; every
# start must leave the profile applied with its Boat skill and guides in place and the owner's own skill kept.
shared=$host_dir/shared-skills
mkdir "$shared"
docker run --rm -u 0 --network none --entrypoint chown -v "$shared:/skills" "$image" node:node /skills
mounts=(--mount "type=bind,src=$shared,dst=/data/.codex/skills" --mount "type=bind,src=$shared,dst=/data/.claude/skills")
bind_check() {  # PROFILE MODE: playbook or generic
  local status base guides
  status=$(status_output "$container")
  require 't3-boat status' "$status" "[ok] Global profile $1 applied"
  docker exec "$container" test -f "/data/profiles/$1.applied.json"
  docker exec "$container" sh -ec '[ "$(stat -c %d:%i /data/.codex/skills)" = "$(stat -c %d:%i /data/.claude/skills)" ]
    [ "$(realpath /data/.codex/skills)" != "$(realpath /data/.claude/skills)" ]'
  for base in .codex .claude; do
    docker exec "$container" grep -Fq '# Boat project workflow' "/data/$base/skills/t3-boat/SKILL.md"
    guides=$(docker exec "$container" sh -c 'ls -d "$1"/playbook-harbor-crew-* 2>/dev/null | wc -l' _ "/data/$base/skills")
    if [[ $2 == playbook ]]; then
      [[ $guides == 3 ]] || { echo "$1: expected 3 guides under /data/$base/skills, found $guides" >&2; exit 1; }
      docker exec "$container" sh -c 'grep -Fq INSTRUCTIONS-FOR-rigger "$1"/playbook-harbor-crew-rigger-*/SKILL.md' _ "/data/$base/skills"
    else
      [[ $guides == 0 ]] || { echo "$1: $guides guides left under /data/$base/skills after detaching" >&2; exit 1; }
    fi
  done
  docker exec "$container" grep -Fq 'my notes' /data/.claude/skills/team-notes/SKILL.md
}
for profile in codex-only claude-only codex+claude; do
  docker run --rm -u 0 --network none --entrypoint sh -v "$shared:/skills" "$image" -c 'rm -rf /skills/* && mkdir /skills/team-notes && printf "%s\n" --- "name: team-notes" "description: mine" --- "" "my notes" > /skills/team-notes/SKILL.md && chown -R node:node /skills'
  volume=$(docker volume create)
  # The mount points are made in the volume first, by the container user, as an owner's earlier start would.
  docker run --rm --network none --entrypoint mkdir -v "$volume:/data" "$image" -p /data/.codex/skills /data/.claude/skills
  extra=("${mounts[@]}")
  start
  (umask 077; crew_package)
  copy_package crew
  case $profile in
    claude-only) lead='{"provider": "claudeAgent", "model": "claude-opus-5-5", "effort": "high"}' ;;
    *) lead='{"provider": "codex", "model": "gpt-6.2-astra", "effort": "high"}' ;;
  esac
  attached='{"path": "/data/teams/crew", "model_set": null, "lead_role": "rigger"}'
  docker exec "$container" sh -c 'mkdir -p /data/profiles && printf "%s\n" "$1" > "/data/profiles/$2.json"' _ \
    "{\"lead\": $lead, \"playbook\": $attached}" "$profile"
  for step in attach refresh; do restart; bind_check "$profile" playbook; done
  docker exec "$container" sh -c 'printf "EDITED-RIGGER\n" >> /data/teams/crew/people/rigger/ROLE.md'
  restart
  bind_check "$profile" playbook
  docker exec "$container" sh -c 'grep -lq EDITED-RIGGER /data/.claude/skills/playbook-harbor-crew-rigger-*/SKILL.md'
  docker exec "$container" sh -c 'printf "%s\n" "$1" > "/data/profiles/$2.json"' _ "{\"lead\": $lead, \"playbook\": null}" "$profile"
  for step in detach refresh; do restart; bind_check "$profile" generic; done
  [[ $(docker exec "$container" ls /data/.codex/skills | sort | tr '\n' ' ') == "t3-boat team-notes " ]] ||
    { echo "unexpected entries in the shared skills folder" >&2; exit 1; }
  cleanup
  container=
  volume=
  extra=()
done
