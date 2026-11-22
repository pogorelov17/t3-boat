# T3 + Boat

Run a coding team from one T3 chat. Your server keeps the conversation. Disposable Boat VMs check out the repository, run tests, and review changes. You get a draft pull request and two independent reviews of the same commit.

[Docker Hub](https://hub.docker.com/r/pogorelov17/t3-boat) · [Releases](https://github.com/pogorelov17/t3-boat/releases) · [Role profiles](#role-profiles)

## Quick start

You need a Linux server with Docker Engine and SSH, a Boat account, and the subscriptions for your chosen profile. The image supports AMD64 and ARM64. [Install Docker Engine](https://docs.docker.com/engine/install/).

### 1. Start the container

Run this on the server. Set `T3_BOAT_PROFILE` to `codex-only`, `claude-only`, or `codex+claude`. Docker downloads the published image on first use.

```sh
docker run -d --name t3-boat --restart unless-stopped \
  -p 127.0.0.1:3773:3773 \
  -v t3-boat-data:/data \
  -e T3_BOAT_PROFILE=codex+claude \
  pogorelov17/t3-boat:0.1.1
```

### 2. Connect your accounts and project

```sh
docker exec -it t3-boat t3-boat setup
docker exec t3-boat t3-boat status
```

Have your GitHub repository URL and Boat environment name ready. Sign in to the orchestrator on the server, connect your worker subscriptions in Boat, and grant Boat permission to push branches and open pull requests for the project. If an agent runs setup for you, complete the sign-in prompts in your own browser or terminal. Repeat `setup` to add another project. [Boat repositories](https://docs.boat.dev/environments/repositories) · [Worker credentials](https://docs.boat.dev/ai-credentials).

### 3. Open your first chat

From your computer, open an SSH tunnel and leave it running. Replace `USER@SERVER` with your usual SSH login.

```sh
ssh -N -o ExitOnForwardFailure=yes -L 127.0.0.1:3773:127.0.0.1:3773 USER@SERVER
```

Open **http://127.0.0.1:3773**. To pair a new browser or desktop client, run this in a second SSH session and open the printed link on the computer running the tunnel.

```sh
docker exec -it t3-boat t3 auth pairing create --ttl 5m --base-url http://127.0.0.1:3773
```

Choose the project, open a chat, and ask **“Run the tests.”** The parent chat records the worker link, commit, test command, and result before releasing the VM. Ask **“Fix the failing test”** to start a change. The executor opens a draft PR. Two reviewers check its head commit on separate VMs. Each revision gets a fresh review from both. You decide when to merge.

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
docker exec t3-boat t3-boat status
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
