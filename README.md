<h1 align="center">⛵ T3 Boat</h1>

<p align="center"><strong>Run a team of coding agents on disposable Boat VMs from T3 Code.</strong></p>

<p align="center">
  <a href="https://github.com/pogorelov17/t3-boat/actions/workflows/release.yml"><img alt="CI" src="https://github.com/pogorelov17/t3-boat/actions/workflows/release.yml/badge.svg?branch=main"></a>
  <a href="https://hub.docker.com/r/pogorelov17/t3-boat"><img alt="amd64 + arm64" src="https://img.shields.io/badge/docker-amd64%20%2B%20arm64-0284c7?logo=docker&amp;logoColor=white"></a>
</p>

<p align="center">
  <a href="#quick-start">Quick start</a> · <a href="https://github.com/pogorelov17/t3-boat/releases/latest">Latest release ↗</a> · <a href="https://hub.docker.com/r/pogorelov17/t3-boat">Docker Hub ↗</a> · <a href="https://github.com/pogorelov17/agent-playbook">Agent Playbook ↗</a>
</p>

Discuss your task in T3 with Codex or Claude. For example, ask “Fix the failing test and show me why it broke.” The lead can plan and check a fact directly. For project work it gives a worker a brief on a fresh Boat VM, where the worker reads the source, makes changes and runs tests. The lead summarizes the assignment, result and next step in your chat, links the details and releases the VM.

## Quick start

You need a Linux server with [Docker Engine](https://docs.docker.com/engine/install/) (AMD64 or ARM64), a Boat account that can start a small VM, a GitHub account and the subscriptions for your profile. A repository is optional.

### 1. Start the container

Run this on the server. It looks up the [latest release](https://github.com/pogorelov17/t3-boat/releases/latest) and pulls its image from Docker Hub:

```sh
VERSION=$(curl -fsSLo /dev/null -w '%{url_effective}' https://github.com/pogorelov17/t3-boat/releases/latest |
  sed -n 's|.*/releases/tag/v\([0-9][0-9.]*\)$|\1|p')
echo "T3 Boat ${VERSION:?could not resolve the latest release}" &&
  docker pull "pogorelov17/t3-boat:$VERSION"
```

Set `T3_BOAT_PROFILE` to `codex-only`, `claude-only` or `codex+claude`, then start it on a new volume. T3 shows the container hostname as its display name.

```sh
docker run -d --name t3-boat --hostname "$(hostname -s)" --restart unless-stopped \
  -p 127.0.0.1:3773:3773 \
  -v t3-boat-data:/data \
  -e T3_BOAT_PROFILE=codex+claude \
  "pogorelov17/t3-boat:${VERSION:?run the commands above first}"
```

Use a fresh data volume for a clean install. If another container uses `t3-boat-data`, choose a new volume name to keep your previous chat history.

### 2. Connect your accounts and verify a worker

```sh
docker exec -it t3-boat t3-boat setup
```

Follow the prompts to sign in to the providers for your profile, choose the lead model and optionally attach a [playbook](#optional-playbook). Enter a GitHub repository URL or press Enter to start with an idea.

In Boat, choose a private worker environment with GitHub access and the same provider subscriptions. For repository work, grant the Boat GitHub App **Contents** and **Pull requests** write access. Setup checks the connections on a temporary VM. When it prints **Setup verified**, open your first chat. To check the installation later, run `docker exec t3-boat t3-boat status`. [Boat environments](https://docs.boat.dev/environments/access) · [Worker subscriptions](https://docs.boat.dev/ai-credentials)

### 3. Open your first chat

**First time connecting this computer?** For a remote server, forward the port from your computer and keep this session open:

```sh
ssh -L 127.0.0.1:3773:127.0.0.1:3773 -o ExitOnForwardFailure=yes USER@SERVER
```

Then, on the server, create a pairing link:

```sh
docker exec -it t3-boat t3 auth pairing create --ttl 5m --base-url http://127.0.0.1:3773
```

In the desktop app, open **Settings → Environments → Add environment → Remote link** and paste the URL, or open it in a browser to pair that browser. Issue a fresh link for each client.

Select your repository or **Start here**, open a chat and describe your task. For access from another device, including a phone, run `docker exec -it t3-boat t3 connect --headless`, follow the browser prompt, then restart the container between tasks and select the T3 Connect route on your client.

## Optional playbook

On a fresh install, press **Enter** at the playbook prompt for a generic worker. To use a team with roles, choose `agent-playbook` for the version supplied with your Boat release, or enter the path to your own package inside the container. The package defines the team and its workflow.

To use the current [agent-playbook main branch](https://github.com/pogorelov17/agent-playbook/tree/main), run this before setup and enter `/data/teams/agent-playbook-main` at the prompt:

```sh
docker exec t3-boat git clone --depth 1 --branch main https://github.com/pogorelov17/agent-playbook /data/teams/agent-playbook-main
```

To build a custom image yourself, run `docker build -t t3-boat:custom .` from a checkout of this repository.
