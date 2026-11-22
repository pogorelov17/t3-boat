# T3 + Boat

Start with an idea in a T3 chat. When you are ready to build, connect a GitHub repository and let agents implement your first feature on disposable Boat VMs. Your server keeps the conversation. Code changes arrive as a draft pull request with two independent reviews.

[Docker Hub](https://hub.docker.com/r/pogorelov17/t3-boat) · [Releases](https://github.com/pogorelov17/t3-boat/releases) · [Role profiles](#role-profiles)

## Quick start

For your first chat, you need a Linux server with Docker Engine and SSH, and a subscription for the orchestrator in your chosen profile. Connect Boat and the worker subscriptions when you start coding. The image supports AMD64 and ARM64. [Install Docker Engine](https://docs.docker.com/engine/install/).

### 1. Start the container

Run this on the server. Set `T3_BOAT_PROFILE` to `codex-only`, `claude-only`, or `codex+claude`. Docker downloads the published image on first use.

```sh
docker run -d --name t3-boat --restart unless-stopped \
  -p 127.0.0.1:3773:3773 \
  -v t3-boat-data:/data \
  -e T3_BOAT_PROFILE=codex+claude \
  pogorelov17/t3-boat:0.1.1
```

### 2. Sign in

On the server, run setup and sign in to the orchestrator. At **GitHub repository URL**, press **Enter** to start with an idea. Setup creates a T3 workspace called **Start here**. If an AI agent is installing this for you, ask it to choose this path and let you complete the account login.

```sh
docker exec -it t3-boat t3-boat setup
```

### 3. Open your first chat

In T3, select **Start here** and open a new chat.

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

Try this first message:

> I want to build a reading list. Help me choose one feature for the first version. What should someone be able to do with it?

The orchestrator replies in this chat. You can refine the idea here before spending any Boat VM time.

## Build your first feature

1. **Choose a repository.** For a new project, create a GitHub repository and select **Add a README** to give the first pull request a base commit. A README is enough. If you already have a repository with a default branch, use its URL. [Create a repository](https://github.com/new)
2. **Connect workers.** Rerun `docker exec -it t3-boat t3-boat setup`, enter the repository URL, and select your Boat environment. Follow setup to connect the worker subscriptions and grant Boat permission to push branches and open PRs for this repository. Run `docker exec t3-boat t3-boat status` to check the binding. [Boat repositories](https://docs.boat.dev/environments/repositories) · [Worker credentials](https://docs.boat.dev/ai-credentials)
3. **Continue the conversation.** The first repository attaches to **Start here**, so your plan stays in the same chat. Try: **“Build the reading list we planned. Let me add a title and see it in the list. Add a check for that behavior and open a draft PR.”** The executor sets up the project on Boat and runs the check. Two reviewers examine the same commit. You decide when to merge.

Already have a project? You can enter its URL during the initial setup and open a chat under its repository name. Rerun setup to register more projects.

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
