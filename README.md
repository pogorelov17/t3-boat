<h1 align="center">⛵ T3 Boat</h1>

<p align="center"><strong>Run a team of coding agents on disposable Boat VMs from T3 Code.</strong></p>

<p align="center">
  <a href="https://github.com/pogorelov17/t3-boat/actions/workflows/release.yml"><img alt="CI" src="https://github.com/pogorelov17/t3-boat/actions/workflows/release.yml/badge.svg?branch=main"></a>
  <a href="https://hub.docker.com/r/pogorelov17/t3-boat"><img alt="amd64 + arm64" src="https://img.shields.io/badge/docker-amd64%20%2B%20arm64-0284c7?logo=docker&amp;logoColor=white"></a>
</p>

<p align="center">
  <a href="#quick-start">Quick start</a> · <a href="https://github.com/pogorelov17/t3-boat/releases/latest">Latest release ↗</a> · <a href="https://hub.docker.com/r/pogorelov17/t3-boat">Docker Hub ↗</a> · <a href="https://github.com/pogorelov17/agent-playbook">Agent Playbook ↗</a>
</p>

T3 Boat runs T3 Code in a Docker container on your Linux server. You discuss a task with a team lead in T3, using Codex, Claude or both. When the work touches a repository, the lead writes an explicit brief for an agent on a fresh Boat VM and reports the outcome back in your chat.

```mermaid
%%{init: {"flowchart": {"curve": "basis", "rankSpacing": 30, "nodeSpacing": 26}}}%%
flowchart TB
    U(["You · ask in a T3 chat"]):::endpoint

    subgraph SERVER["Your server · T3"]
        direction LR
        L["Team lead<br/>writes an explicit brief"]:::lead
        P["Playbook · optional<br/>defines roles and sequence"]:::optional
        P -. guides .-> L
    end

    subgraph VM["Disposable Boat VM"]
        W["Generic agent<br/>does the work in the brief"]:::work
    end

    R["Team lead<br/>posts the update, then deletes the VM"]:::lead
    S(["You · see the update in your chat"]):::endpoint

    U --> SERVER
    SERVER -->|brief| VM
    VM -->|result| R
    R --> S

    classDef lead fill:#e0f2fe,stroke:#0284c7,color:#0c4a6e
    classDef work fill:#f0fdfa,stroke:#0d9488,color:#134e4a
    classDef endpoint fill:#f5f3ff,stroke:#8b5cf6,color:#4c1d95
    classDef optional fill:#fffbeb,stroke:#d97706,color:#78350f,stroke-dasharray:4 3
    style SERVER fill:#f8fafc,stroke:#cbd5e1,color:#64748b
    style VM fill:#f8fafc,stroke:#cbd5e1,stroke-dasharray:5 5,color:#64748b
```

For each delegation, result or decision, the lead posts a short update with links instead of shell logs. For example, after you ask “Fix the failing date test, explain why it broke and open a pull request with the fix.”:

> - **Who → whom:** lead → worker on `bx_7f3k` (child chat), Codex, lead model, high effort
> - **Asked:** reproduce the failing `test_parse_iso_date`, fix it and open a pull request
> - **Depends on:** `main` at `4e1c9a2`
> - **Result:** the parser dropped the `Z` offset; fixed in `src/dates.py`; `pytest` 142 passed, 0 failed; pull request `#42` opened
> - **Decision:** `#42` is ready for your review.

With a [playbook](#2-choose-a-playbook-optional) attached, its roles and their order decide who gets the next brief. The update takes the same form.

**What survives cleanup.** The lead copies each outcome into your chat before it deletes the worker's VM. Afterwards, the T3 history on your server and whatever the worker actually delivered elsewhere, such as a pushed branch or a pull request, remain. The worker's checkout, its chat and anything else left on the VM are gone.

Your server hosts T3 and handles worker provisioning and cleanup. The lead can plan and check a fact on the web. Workers clone the project, install its dependencies, edit its source and run builds and tests on Boat VMs.

## Quick start

### AI agent installation contract

**Installing with an AI agent?** A request to install T3 Boat authorizes infrastructure work, not setup decisions. On a fresh volume, the first `t3-boat setup` prints `OWNER_INPUT_REQUIRED` and exits without reading answers. The agent must show that block to the owner, stop its turn and wait. It must not rerun setup with `--owner-approved` until the owner replies.

The agent must obtain explicit choices of provider profile, playbook, project and whether it may accept setup's recommended lead model. It must not infer them from the installing agent, this repository URL or an omitted answer. If a playbook is selected, it must later show its lead roles and model sets and wait again. It must never press **Enter**, choose `none`, defer the project or select a model on the owner's behalf.

- **Provider profile:** setup asks separately whether to connect Codex with ChatGPT and Claude Code with Claude. Codex only uses `codex-only`; Claude only uses `claude-only`; both use `codex+claude`; neither stops the installation without changing the saved choice.
- **Playbook:** choose no playbook, the suggested `agent-playbook`, or a custom package path. The agent must not download or attach one until you choose it. If you attach one, the agent must also show you its available lead roles and model sets and wait for those choices.
- **Project:** provide the GitHub repository you want workers to use, or explicitly defer it and start with an idea. The T3 Boat repository named in an install request is only the installation source unless you explicitly select it as your project.

You need a Linux server with curl and [Docker Engine](https://docs.docker.com/engine/install/) (AMD64 or ARM64), a Boat account that can start a small VM, a GitHub account and the subscriptions for your profile. A repository is optional.

### 1. Start the container

Run this on the server. It looks up the [latest release](https://github.com/pogorelov17/t3-boat/releases/latest) and pulls its image from Docker Hub:

```sh
VERSION=$(curl -fsSLo /dev/null -w '%{url_effective}' https://github.com/pogorelov17/t3-boat/releases/latest |
  sed -n 's|.*/releases/tag/v\([0-9][0-9.]*\)$|\1|p')
echo "T3 Boat ${VERSION:?could not resolve the latest release}" &&
  docker pull "pogorelov17/t3-boat:$VERSION"
```

Start it on a new volume. No provider is selected by this command; T3 shows the container hostname as its display name.

```sh
docker run -d --name t3-boat --hostname "$(hostname -s)" --restart unless-stopped \
  -p 127.0.0.1:3773:3773 \
  -v t3-boat-data:/data \
  "pogorelov17/t3-boat:${VERSION:?run the commands above first}"
```

Use a fresh data volume for a clean install. If another container uses `t3-boat-data`, choose a new volume name to keep your previous chat history. To build the image yourself instead, run `docker build -t t3-boat:custom .` from a checkout of this repository and start that tag.

### 2. Choose a playbook (optional)

Setup asks for a playbook right after sign-in, so decide before you run it. Without one, the lead briefs a generic worker for each task and decides how many to start and in what order. A playbook is an external package, a `team.json` manifest with role guides, that defines the roles, their instructions, their sequence and optionally their models. T3 Boat reads it as data and only launches its workers. It applies to every project of this profile and adds nothing to your repositories.

An AI installer must stop before the **Playbook** prompt, present the choices below and wait for the owner. It must not press **Enter**, enter `agent-playbook`, choose a lead role or choose a model set on the owner's behalf. It may recommend a choice and explain it.

At the **Playbook** prompt, enter one of these:

- **Enter** for none: a generic worker. This is the default on a fresh install.
- `agent-playbook` to download [agent-playbook](https://github.com/pogorelov17/agent-playbook) in the version selected for this T3 Boat release.
- The path of a package inside the container, such as your own package under `/data/teams`.

To use the current [agent-playbook main branch](https://github.com/pogorelov17/agent-playbook/tree/main) instead, run this before setup and enter `/data/teams/agent-playbook-main` at the prompt:

```sh
docker exec t3-boat git clone --depth 1 --branch main https://github.com/pogorelov17/agent-playbook /data/teams/agent-playbook-main
```

When you attach a package, setup also asks which of its roles the lead follows and, if a compatible model set is available, which one to use.

### 3. Connect your accounts and verify a worker

Before you start, decide whether to attach a project now. If you do, complete both sides:

- **T3:** give setup the project's GitHub URL so it creates or attaches the project instead of leaving repository access deferred. Do not reuse the T3 Boat installation URL unless this is the project you explicitly chose.
- **Boat:** choose a private worker environment whose credential profile, under **Agents & Proxying**, connects the same subscriptions as your profile. Grant the Boat GitHub App access to this repository with **Contents** and **Pull requests** write permissions. [Boat environments](https://docs.boat.dev/environments/access) · [Worker subscriptions](https://docs.boat.dev/ai-credentials)

If you are not ready to choose a repository, explicitly defer it and use **Start here**. An AI installer must ask which path you want and wait; it must not silently defer the repository or substitute the installation repository.

```sh
docker exec -it t3-boat t3-boat setup
```

On a fresh volume this command only prints the owner questions and stops. After the owner answers them, continue with:

```sh
docker exec -it t3-boat t3-boat setup --owner-approved
```

Saving a new provider profile deliberately stops setup once and asks for a container restart. Restart it and rerun the ordinary setup command; this makes the already-running T3 process load the selected providers before model discovery.

Setup asks, in this order:

1. **Provider subscriptions:** answer `yes` or `no` separately for Codex with ChatGPT and Claude Code with Claude. A fresh setup accepts no default and stops if both answers are `no`.
2. **Sign in** to each selected provider. Valid sessions are reused.
3. **Playbook:** press **Enter** for a generic worker, or enter your explicit choice from [step 2](#2-choose-a-playbook-optional). For an attached playbook, explicitly choose its lead role and model set when offered.
4. **Lead model:** the model and reasoning level of your chats, which every worker also opens with. Press **Enter** to accept the one shown, or type `edit` to choose another.
5. **GitHub repository URL:** enter the project URL you chose, or press **Enter** only if you explicitly chose to defer it and start with an idea in **Start here**.
6. **Boat:** approve the device login if prompted, select the worker environment you prepared and confirm the repository attachment if prompted.

Setup then checks the connections on a temporary VM, deletes it and finishes with **Setup verified**. For an attached repository, the check must authenticate to GitHub from that VM and read the repository's refs; access only on the T3 server is not enough.

An AI installer must not answer the playbook, lead role, model set, lead model or repository prompts for the owner. It must pause at each undecided choice. After setup, it must run `docker exec t3-boat t3-boat status` and report the exact profile; playbook name, version and commit or `none`; lead role and model set when present; lead model; repository URL or an explicit defer; Boat environment and credential profile; providers and models that replied; the temporary VM ID and confirmed deletion; and whether that VM read the selected repository's refs. It must not report repository setup as complete while status says access is deferred.

**Change it later.** Run the same setup command again. Keep the saved provider profile or explicitly choose to change its subscriptions, change the lead model (type `edit`), attach another playbook or detach it with `none`, or add a repository by entering its URL. Where setup shows a saved default, press **Enter** to keep it. To check the installation without starting a VM, run `docker exec t3-boat t3-boat status`.

### 4. Connect your devices and open your first chat

For a remote server, use T3 Connect. Link the container to the T3 Connect account you want to use on every device:

```sh
docker exec -it t3-boat t3 connect --headless
```

Open the browser URL it prints, sign in and approve the displayed code. Between tasks, restart the container to apply the link, then check it:

```sh
docker restart t3-boat
docker exec t3-boat t3 connect status --base-dir /data/.t3
```

On each Mac, phone or browser, sign in to the same T3 Connect account and select this environment. The server and container must stay running. This route needs no public port, SSH tunnel or localhost pairing link.

**Installing with an AI agent?** It must offer T3 Connect first for a remote server. It must not create or ask the owner to paste a `127.0.0.1` pairing link unless the owner chooses the direct SSH route below. Creating a pairing link does not open an SSH tunnel.

<details>
<summary><strong>Use a direct SSH route instead of T3 Connect</strong></summary>

On the client computer, start the port forward and keep this command running:

```sh
ssh -N -L 127.0.0.1:3773:127.0.0.1:3773 -o ExitOnForwardFailure=yes USER@SERVER
```

Only after that command remains running without an error, create a pairing link on the server:

```sh
docker exec -it t3-boat t3 auth pairing create --ttl 5m --base-url http://127.0.0.1:3773
```

Open the URL on that client, or paste it into **Settings → Environments → Add environment → Remote link**. Issue a fresh link for each client. An installer must never say the tunnel is open unless it started and verified the `ssh -L` command.

</details>

If you skipped the repository URL, open **Start here** to plan your idea. When you are ready to delegate project work, rerun setup with the repository URL.

Open a chat in your repository and describe the task. For example:

> Fix the failing date test, explain why it broke and open a pull request with the fix.

The lead turns this into a brief for a worker and posts updates like the example at the top of this page. If you attached a playbook, its roles and sequence already apply; you do not need to mention them.
