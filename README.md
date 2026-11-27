<h1 align="center">⛵ T3 Boat</h1>

<p align="center"><strong>Run a team of coding agents on disposable Boat VMs from T3 Code.</strong></p>

<p align="center">
  <a href="https://github.com/pogorelov17/t3-boat/actions/workflows/release.yml"><img alt="CI" src="https://github.com/pogorelov17/t3-boat/actions/workflows/release.yml/badge.svg?branch=main"></a>
  <a href="https://hub.docker.com/r/pogorelov17/t3-boat"><img alt="amd64 + arm64" src="https://img.shields.io/badge/docker-amd64%20%2B%20arm64-0284c7?logo=docker&amp;logoColor=white"></a>
</p>

<p align="center">
  <a href="#quick-start">Quick start</a> · <a href="https://github.com/pogorelov17/t3-boat/releases/latest">Latest release ↗</a> · <a href="https://hub.docker.com/r/pogorelov17/t3-boat">Docker Hub ↗</a> · <a href="https://github.com/pogorelov17/agent-playbook">Agent Playbook ↗</a>
</p>

Coordinate coding agents across your GitHub projects from one Linux machine, local or remote. Bring an idea or an existing repository and work from a T3 chat with Codex, Claude or both. The code itself runs on disposable Boat VMs.

## How it works

Suppose you ask in T3, “Fix the failing test and show me why it broke.”

1. **The team lead talks with you in T3.** It discusses, plans and checks a fact on the web when needed. Until there is work for a worker, the conversation stays in T3.
2. **Workers run on Boat.** The lead starts a worker on a fresh VM and writes it a brief for this task: here, reproduce the failure, fix it, run the tests and deliver the fix the way you asked, for example as a pull request. Everything that touches the repository runs on the VM: reading source, edits, installs, builds, tests and extended research. Your machine only hosts T3 and its history. What a worker delivers comes from its brief or an [attached playbook](#optional-playbook); T3 Boat sets no branch, pull request or review rule of its own.
3. **The parent chat stays compact.** For each delegation, result and decision, the lead posts who asked whom for what, known dependencies, the result and its decision, with links to the child chat and what it delivered instead of shell logs.
4. **You decide what happens next.** Once a worker’s result is recorded, its VM is deleted.

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

To pin a version instead, set `VERSION` to a number from [Releases](https://github.com/pogorelov17/t3-boat/releases), such as `VERSION=0.2.0`. Each release links the Quick start at its own tag; this README on the default branch can describe changes that are not released yet.

**Fresh installations only.** Start each new version on a new volume, for example `-v t3-boat-data-2:/data`. A volume from another version is not converted: the container stops with a one-line message and leaves the volume unchanged, so you keep its chat history.

### 2. Connect your accounts and verify a worker

```sh
docker exec -it t3-boat t3-boat setup
```

| Profile | Sign in on the T3 server | Connect in the Boat credential profile |
| --- | --- | --- |
| `codex-only` | Codex with ChatGPT | ChatGPT subscription |
| `claude-only` | Claude Code with Claude | Claude subscription |
| `codex+claude` | Both | Both subscriptions |

1. Follow the login prompts; valid sessions are reused. Setup then asks for a [playbook](#optional-playbook) (press **Enter** for none) and the **lead model**: the model and reasoning level of your chats, which every worker also opens with. At **GitHub repository URL**, enter a project URL or press **Enter** to start in **Start here** with an idea.
2. Approve the Boat device login if prompted. In Boat, connect GitHub, open **Agents & Proxying**, select your worker credential profile and connect the subscriptions in the table. Select a private worker environment that passes GitHub and agent credentials and uses that profile. For a repository, grant the Boat GitHub App **Contents** and **Pull requests** write access to it. [Boat environments](https://docs.boat.dev/environments/access) · [Worker subscriptions](https://docs.boat.dev/ai-credentials)
3. Setup starts a fresh VM, authenticates to GitHub from it, requests one reply from each enabled provider and, for a repository, reads its Git refs. Look for the success lines, the deleted VM ID and **Setup verified**. If a check fails, fix the reported connection and rerun setup.

`docker exec t3-boat t3-boat status` shows the local logins, Boat configuration and last successful check without starting a VM. Rerun setup to add a repository, or to change the playbook or lead model.

**Installing with an AI agent?** Have the account owner complete browser confirmations directly. Report the profile, the local logins, the VM ID and its deletion, the providers and models that replied, and the repository checked or deferred. Then complete the first chat below and offer the [phone setup](#connect-your-phone-optional). A running container alone is only the first step.

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

In T3, select your repository, or **Start here**, and open a new chat. Write in your own words, in any language. A reply confirms your client reaches the team lead. When you are ready, ask for a change and say how you want it delivered, for example as a pull request; the parent chat records the worker’s model, test results and what it delivered.

<details>
<summary><strong>Keep T3 reachable when you close your laptop (T3 Connect)</strong></summary>

After pairing, link the container to your T3 Connect account and follow the browser prompt. Then restart the container, between tasks, to apply the link:

```sh
docker exec -it t3-boat t3 connect --headless
docker restart t3-boat
docker exec t3-boat t3 connect status --base-dir /data/.t3
```

Repeat the status command until it reports **Other devices can reach this machine** and **Tunnel connected**. In the desktop app’s **Settings → Environments**, add **T3 Connect** as a route for the paired server, confirm it shows **via T3 Connect** and send a message in a new chat. A fresh data volume is a new T3 environment: pair it again.

</details>

## Models

Setup lists the model IDs and reasoning levels T3 reports for your signed-in providers and saves your choice as the lead model in `/data/profiles/PROFILE.json`. Every worker VM opens with that model and has every provider of the profile enabled, so the lead can choose another listed model for each brief and names its choice in the parent chat. Rerun setup and type `edit` to change it.

## Optional playbook

A playbook is an external package, in the format of [agent-playbook](https://github.com/pogorelov17/agent-playbook), that defines roles, their instructions, how many of each run and on which models. T3 Boat reads it as data, never runs code from it, and adds only how to start a worker on Boat and the compact parent updates. When setup asks for a playbook, enter:

- **Enter** for none, or to keep the current one; `none` detaches it.
- `agent-playbook` for the version of agent-playbook pinned in this T3 Boat release. Setup downloads it into `/data/teams` and checks every file against the pin; later runs reuse it offline. It does not follow agent-playbook’s main branch.
- The absolute path of a package directory in the container, such as `/data/teams/my-team`.

To use agent-playbook’s current main branch, clone it into the volume and enter `/data/teams/agent-playbook-main`:

```sh
docker exec t3-boat git clone --depth 1 https://github.com/pogorelov17/agent-playbook /data/teams/agent-playbook-main
```

To use your own package, copy it in and make the container user its owner, then enter `/data/teams/my-team`:

```sh
docker cp ./my-team/. t3-boat:/data/teams/my-team
docker exec -u 0 t3-boat chown -R node:node /data/teams/my-team
```

Setup then asks which role the team lead follows and which model set applies; `none` is valid for both. Each role becomes a skill for every project on this machine, and `boat-worker start ROLE` gives a worker that role’s instructions. Changes to a package apply after `docker restart t3-boat`. If a package can no longer be read, T3 still starts without it, and the container log and `t3-boat status` name the problem.

## Build from source

To change the packaged tools or instructions, build your own image and use its tag in `docker run`. Add `--build-arg BUNDLE_PLAYBOOK=1` to include agent-playbook as the `bundled` setup choice. Run the unit tests with `python3 -m unittest discover -s tests`.

```sh
docker build -t t3-boat:custom .
```

## Connect your phone (optional)

1. Enable [T3 Connect](#3-open-your-first-chat) on the server if you have not already.
2. On your phone, open the [official T3 Code site](https://t3.codes/). Under **Also on your phone**, choose **iOS** for the App Store or **Android** for Google Play, then sign in with the same T3 account you linked to the server.
3. Select your environment through T3 Connect, open the chat you used during setup and send a message.

Keep the server awake and online while connecting from your phone.
