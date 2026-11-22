# T3 + Boat

Open a T3 project chat and ask it to run tests or make a change. T3 keeps the conversation on your server. Its orchestrator delegates repository work to agents on fresh Boat VMs. Each project gets its own GitHub repository and Boat environment. The same role profile applies to new chats across the installation.

## Quick start

**1. Get the published image reference.** On a Linux server with Docker Engine, run `docker version` and check that it prints a Server section. You also need SSH access, a Boat account and the subscription(s) for your profile. [Install Docker Engine](https://docs.docker.com/engine/install/).

Open the project's GitHub **Releases** page. Copy the `export IMAGE=...` command for the release you want and run it on the server. Then run one of the commands below. Docker downloads the matching image for the server's architecture on first use. This path becomes available with the first public release.

For automated installation, the same release includes a one-line `docker-image.txt` asset with the exact image reference.

**2. Start one profile.** Run one command on the server, in the shell where you set `IMAGE`.

**Codex only** — Astra orchestrates, Luna scouts, Sol researches and implements, and Astra and Sol review.

```sh
docker run -d --name t3-boat --restart unless-stopped -p 127.0.0.1:3773:3773 -v t3-boat-data:/data -e T3_BOAT_PROFILE=codex-only "$IMAGE"
```

**Claude only** — Opus orchestrates, researches and implements. Haiku scouts, and Fable and Opus review.

```sh
docker run -d --name t3-boat --restart unless-stopped -p 127.0.0.1:3773:3773 -v t3-boat-data:/data -e T3_BOAT_PROFILE=claude-only "$IMAGE"
```

**Codex + Claude** — Astra orchestrates, Haiku scouts, Sol researches, Opus implements, Fable and Astra review.

```sh
docker run -d --name t3-boat --restart unless-stopped -p 127.0.0.1:3773:3773 -v t3-boat-data:/data -e 'T3_BOAT_PROFILE=codex+claude' "$IMAGE"
```

**3. Connect accounts and register your project.**

```sh
docker exec -it t3-boat t3-boat setup
docker exec t3-boat t3-boat status
```

Have your GitHub repository URL and Boat environment name ready. The local provider login runs the orchestrator. Boat's AI credentials authorize agents on new VMs. Claude-enabled installations download Claude Code during setup. Connect GitHub to Boat and grant access to the target repository, including pushing a branch and opening a pull request. Instructions for the repository and subscription connections are in the [Boat repository guide](https://docs.boat.dev/environments/repositories) and [Boat AI credential guide](https://docs.boat.dev/ai-credentials).

If an AI agent handles the installation, let the owner complete the browser or device login in their terminal. Resume with `setup` and `status`. Keep login codes and pairing codes out of chat. Setup registers an empty project directory with the Git remote and Boat environment. The repository checkout lives on Boat.

**4. Open T3 from your computer.** Leave this command running on the computer, using your server's SSH login:

```sh
ssh -N -o ExitOnForwardFailure=yes -L 127.0.0.1:3773:127.0.0.1:3773 USER@SERVER
```

Open **http://127.0.0.1:3773**. To pair the browser, run the following in a second SSH session and open the printed loopback pairing link on the computer running the tunnel. Select the project and open a new chat.

```sh
docker exec -it t3-boat t3 auth pairing create --ttl 5m --base-url http://127.0.0.1:3773
```

| First message | What to look for |
| --- | --- |
| «Какой профиль сейчас активен?» | The orchestrator identifies the profile. A general question costs no VM time. |
| «Запусти тесты» | A child agent runs tests on Boat. The parent reports the result before releasing the VM. |
| «Исправь падающий тест» | The executor opens a Draft PR. Two independent reviewers examine the same commit on separate VMs. |

The parent chat keeps the child links, models, requested reasoning, test results, commit and verdicts. Fixes go to the same PR on a fresh VM. Both reviewers check the new head SHA. The owner decides when to merge.

## Default models by role

Each cell shows the configured model and reasoning level. Both reviewers independently check the same commit.

| Role | `codex-only` | `claude-only` | `codex+claude` |
| --- | --- | --- | --- |
| Orchestrator / team lead | `gpt-6.2-astra` · high | `claude-opus-5-5` · high | `gpt-6.2-astra` · high |
| Code scout | `gpt-6-luna` · low | `claude-haiku-5-5` · medium | `claude-haiku-5-5` · medium |
| Internet researcher | `gpt-6.2-sol` · high | `claude-opus-5-5` · high | `gpt-6.2-sol` · high |
| Executor | `gpt-5.6-sol` · medium | `claude-opus-5-5` · high | `claude-opus-5-5` · high |
| Independent reviewer 1 | `gpt-6.2-astra` · high | `claude-fable-5-2` · high | `claude-fable-5-2` · high |
| Independent reviewer 2 | `gpt-6.2-sol` · high | `claude-opus-5-5` · high | `gpt-6.2-astra` · high |
| Adjudicator for disputed reviews | `gpt-6.2-astra` · xhigh | `claude-opus-5-5` · high | `gpt-6.2-astra` · xhigh |

## Change roles and add projects

The installation keeps its editable role tables in `/data/profiles/`. To change a model or reasoning level, copy the active file out, edit it, copy it back and restart the container before opening a new chat. The example uses the combined profile:

```sh
docker cp 't3-boat:/data/profiles/codex+claude.json' ./roles.json
# Edit roles.json using the existing schema.
docker exec -i t3-boat sh -c 'cat > "$HOME/profiles/codex+claude.json.new" && mv "$HOME/profiles/codex+claude.json.new" "$HOME/profiles/codex+claude.json"' < roles.json
docker restart t3-boat
docker exec t3-boat t3-boat status
```

Repeat `setup` to add a project. Use a Boat environment scoped to that project's GitHub access. A new chat inherits the installation profile. If a model is unavailable for the selected account, the role instructions require the orchestrator to report the mismatch and ask before substituting a model.

## Persistence

Reuse the named volume `t3-boat-data` when replacing the container or upgrading the image. It holds T3 history, project registrations, role settings and account logins. Back up the volume privately. Wait for active jobs to finish before replacing the container. On restart, open the parent chat and ask the orchestrator to continue if a run was interrupted.

## Build a customized image

When changing the Dockerfile, bundled tools or orchestration instructions, run the following in your modified source checkout, then continue at Quick start step 2:

```sh
export IMAGE=t3-boat:local
docker build -t "$IMAGE" .
```

For installation-specific models and reasoning levels, edit the role table in the data volume as described above.

## Building a release

The GitHub Actions workflow builds and tests Linux amd64 and arm64. A maintainer creates a **public** Docker Hub repository, sets the GitHub repository variable `DOCKERHUB_REPOSITORY` (`namespace/image`), and configures the secrets `DOCKERHUB_USERNAME` and `DOCKERHUB_TOKEN` for publishing. Pushing a tag such as `v1.0.0` runs the checks, publishes the tested images under one version tag and attaches the pull reference to the GitHub release.

The offline smoke check starts each profile, creates a T3 project and thread, replaces the container and checks that the thread survives. A release candidate also needs an account-backed run for each profile: start a change from a new chat, check the Draft PR and both reviews against its final SHA, and confirm the workers were deleted. Record the actual models and which checks passed.
