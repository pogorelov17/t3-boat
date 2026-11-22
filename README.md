# T3 + Boat

Use a small Linux server to coordinate coding agents across your GitHub projects. Bring an idea or an existing repository, then work from a T3 chat using Codex, Claude, or both.

[Quick start](#quick-start) · [Role profiles](#role-profiles) · [Docker Hub](https://hub.docker.com/r/pogorelov17/t3-boat) · [Releases](https://github.com/pogorelov17/t3-boat/releases)

## How it works

Suppose you ask in T3, “Fix the failing test and show me why it broke.”

1. **T3 coordinates the work.** The orchestrator uses your installation’s role profile to select the executor and reviewers. New chats across your projects inherit those model and reasoning settings. If you are still discussing an idea, the conversation can stay in T3 until you have a task for a worker.
2. **Boat runs the code.** The executor checks out the repository on a disposable VM, reproduces the failure, tests the fix and opens a draft PR. Dependencies, builds and test runs stay on Boat, so your server only needs to host T3 and its history.
3. **Two reviewers check the same commit.** Each gets a fresh VM and the exact PR commit. Both send independent verdicts to the orchestrator. A model that wrote the change cannot review it. If a fix is needed, a fresh executor updates the same PR and both reviewers check the new commit.
4. **You decide when to merge.** The parent chat collects links to the agents, the test results, both verdicts and the PR. Once a worker’s result is recorded, its VM can be deleted. You can return to the chat or request another change on the PR without keeping workers running.

## Quick start

You need a Linux server with Docker Engine and SSH, a Boat account that can start a small VM, a GitHub account, and the subscriptions enabled in your profile. A repository is optional. The image supports AMD64 and ARM64. [Install Docker Engine](https://docs.docker.com/engine/install/).

### 1. Start the container

Run this on the server. Set `T3_BOAT_PROFILE` to `codex-only`, `claude-only`, or `codex+claude`. Docker downloads the published image on first use.

```sh
docker run -d --name t3-boat --restart unless-stopped \
  -p 127.0.0.1:3773:3773 \
  -v t3-boat-data:/data \
  -e T3_BOAT_PROFILE=codex+claude \
  pogorelov17/t3-boat:0.1.2
```

### 2. Connect your accounts and verify a worker

Run this on the server. The live check uses a small Boat VM and one short model request per enabled provider.

```sh
docker exec -it t3-boat t3-boat setup
```

| Profile | Sign in on the T3 server | Connect in the Boat credential profile |
| --- | --- | --- |
| `codex-only` | Codex with ChatGPT | ChatGPT subscription |
| `claude-only` | Claude Code with Claude | Claude subscription |
| `codex+claude` | Both | Both subscriptions |

1. Follow the login prompts. Existing valid sessions are reused. At **GitHub repository URL**, enter a project URL or press **Enter** to open **Start here** with your own idea.
2. Approve the Boat device login if prompted. In Boat, connect GitHub. Open **Agents & Proxying**, select your worker credential profile and connect the subscriptions in the table. Boat supplies these logins to new VMs. Select a private worker environment that passes GitHub and agent credentials and uses that credential profile. For a repository, grant the Boat GitHub App access to that repository with **Contents** and **Pull requests** write permissions. [Boat environments](https://docs.boat.dev/environments/access) · [Worker subscriptions](https://docs.boat.dev/ai-credentials)
3. Setup starts a fresh VM, authenticates to GitHub from it and requests one reply from each enabled provider. If you supplied a repository, it also reads its Git refs from that VM. Look for each success line, the deleted VM ID and **Setup verified**. If a check fails, fix the reported connection and rerun setup.

**Installing with an AI agent?** Have the account owner complete browser confirmations directly. Report the profile, the local logins, the VM ID and deletion, the providers and models that replied, and the repository checked or deferred. Complete the first-chat check below. A running container alone is only the first step.

### 3. Open your first chat

In T3, select your repository, or **Start here** if you skipped the URL, and open a new chat. Write in your own words, in any language. You can ask a question, share an idea, or use an opening message suggested by the agent helping you get set up.

<details>
<summary><strong>First time connecting this computer?</strong></summary>

In a terminal on your computer, replace `USER@SERVER` with your SSH login and connect:

```sh
ssh -L 127.0.0.1:3773:127.0.0.1:3773 -o ExitOnForwardFailure=yes USER@SERVER
```

In that SSH session, get a pairing link:

```sh
docker exec -it t3-boat t3 auth pairing create --ttl 5m --base-url http://127.0.0.1:3773
```

Open the printed link on your computer and keep the SSH session open.

</details>

If you are unsure where to begin, ask the orchestrator for ideas. Wait for a reply in the new chat to confirm your client can reach the orchestrator. Planning can continue there until you choose a repository.

## Build your first feature

1. **Choose the repository for this change.** Use the URL of any GitHub repository you can work on. For a brand-new project, initializing the repository with a README gives the first PR a base commit.
2. **Attach the repository.** If you supplied its URL during setup, continue to step 3. Otherwise rerun `docker exec -it t3-boat t3-boat setup` and enter the URL. Setup checks access from a fresh VM. The first real change exercises branch push and draft PR creation.
3. **Continue the conversation.** Use your project chat. If you began in **Start here**, attaching the first repository keeps your planning thread there. When you are ready, ask for the first change you want. The executor implements it on Boat, checks the result, and opens a draft PR. Two reviewers examine the same commit. You decide when to merge.

To work on another repository, rerun setup with its URL. To inspect current local logins, Boat configuration and the timestamp of the last successful VM check, run `docker exec t3-boat t3-boat status`. Status does not allocate a VM. Rerun setup for a fresh check after changing profiles, credentials or models.

## Role profiles

The profile applies to new chats across all projects on this server. Each entry shows the model and its default reasoning effort.

| Role | Codex only | Claude only | Codex + Claude |
| --- | --- | --- | --- |
| Orchestrator | Astra 6.2 · high | Opus 5.5 · high | Astra 6.2 · high |
| Code scout | Luna 6 · low | Haiku 5.5 · medium | Haiku 5.5 · medium |
| Internet researcher | Sol 6.2 · high | Opus 5.5 · high | Sol 6.2 · high |
| **Executor** | **Sol 5.6 · medium** | **Opus 5.5 · high** | **Opus 5.5 · high** |
| Reviewer 1 | Astra 6.2 · high | Fable 5.2 · high | Fable 5.2 · high |
| Reviewer 2 | Sol 6.2 · high | Sonnet 5.5 · high | Astra 6.2 · high |
| Disputed reviews | Astra 6.2 · xhigh | Fable 5.2 · high | Astra 6.2 · xhigh |

The executor's model must differ from both reviewers and the model that settles disputes. The orchestrator checks model availability before delegation. [Codex profile](profiles/codex-only.json) · [Claude profile](profiles/claude-only.json) · [Combined profile](profiles/codex+claude.json).

<details>
<summary><strong>Customize models and reasoning</strong></summary>

The editable profile lives in the Docker volume. In this example, change `PROFILE` to match the running container, copy the role table out, and edit the model IDs or effort levels in `roles.json`.

```sh
PROFILE=codex+claude
docker cp "t3-boat:/data/profiles/$PROFILE.json" ./roles.json
```

Apply your edits, then open a new chat.

```sh
docker exec -i t3-boat sh -ec 'p="$HOME/profiles/$T3_BOAT_PROFILE.json"; cat > "$p.new" && mv "$p.new" "$p"' < roles.json
docker restart t3-boat
docker exec -it t3-boat t3-boat setup
```

</details>

<details>
<summary><strong>Keep your history when updating</strong></summary>

Finish active jobs and back up the named volume privately. Stop and remove the old container, then repeat the start command with the new image tag, the same profile, and the same `t3-boat-data` volume. It holds chat history, project bindings, role settings, and account logins.

The stock Claude profile from 0.1.0 upgrades automatically. If you customized that profile, assign reviewer and adjudicator models distinct from the executor before upgrading.

</details>

<details>
<summary><strong>Build a customized image</strong></summary>

To change the packaged tools or orchestration instructions, build from your edited source checkout. Use `t3-boat:local` in the start command.

```sh
docker build -t t3-boat:local .
```

</details>
