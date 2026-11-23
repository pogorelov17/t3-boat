# T3 + Boat

Use a small Linux server to coordinate coding agents across your GitHub projects. Bring an idea or an existing repository, then work from a T3 chat using Codex, Claude, or both.

[Quick start](#quick-start) · [Default role profiles](#default-role-profiles) · [Docker Hub](https://hub.docker.com/r/pogorelov17/t3-boat) · [Releases](https://github.com/pogorelov17/t3-boat/releases)

## How it works

Suppose you ask in T3, “Fix the failing test and show me why it broke.”

1. **T3 coordinates the work.** The orchestrator uses your installation’s role profile to select the executor and reviewers. New chats across your projects inherit those model and reasoning settings. If you are still discussing an idea, the conversation can stay in T3 until you have a task for a worker.
2. **Boat runs the code.** The executor checks out the repository on a disposable VM, reproduces the failure, tests the fix and opens a draft PR. Dependencies, builds and test runs stay on Boat, so your server only needs to host T3 and its history.
3. **Two reviewers check the same commit.** Each gets a fresh VM and the exact PR commit. Both send independent verdicts to the orchestrator. The first reviewer always uses a different model from the executor. If a fix is needed, a fresh executor updates the same PR and both reviewers check the new commit.
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
  pogorelov17/t3-boat:0.1.4
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

1. Follow the login prompts. Existing valid sessions are reused. After sign-in, setup shows the model and reasoning level for each role. Press **Enter** to accept the table or select a role to customize it. At **GitHub repository URL**, enter a project URL or press **Enter** to open **Start here** with your own idea.
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

## Default role profiles

Each entry is a suggested model family and reasoning level. On first setup, the installer selects the newest stable version of that family listed by T3 that supports the requested effort. You see the exact model IDs before accepting. Your choices apply to new chats across all projects on this server.

| Role | Codex only | Claude only | Codex + Claude |
| --- | --- | --- | --- |
| Orchestrator | Astra · high | Opus · high | Astra · high |
| Code scout | Luna · low | Haiku · medium | Haiku · medium |
| Internet researcher | Sol · high | Opus · high | Sol · high |
| **Executor** | **Sol · medium** | **Opus · high** | **Opus · high** |
| Reviewer 1 | Astra · high | Fable · high | Fable · high |
| Reviewer 2 | Sol · high | Sonnet · high | Astra · high |
| Disputed reviews | Astra · xhigh | Fable · high | Astra · xhigh |

The first reviewer and the adjudicator use a different model from the executor. In **Codex only**, the default executor and second reviewer share the newest Sol that supports both effort levels. The second reviewer starts a separate read-only session on a fresh VM and reviews the same commit as the first reviewer. [Codex defaults](profiles/codex-only.json) · [Claude defaults](profiles/claude-only.json) · [Combined defaults](profiles/codex+claude.json).

**Change your choices later.** Run `docker exec -it t3-boat t3-boat setup` again. Setup keeps your saved versions unless you edit a role or explicitly refresh the defaults. If a model or reasoning level is unavailable, choose a replacement before continuing.

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
