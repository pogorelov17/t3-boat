<h1 align="center">⛵ T3 Boat</h1>

<p align="center"><strong>Run a team of coding agents on disposable Boat VMs from T3 Code.</strong></p>

<p align="center">
  <a href="https://github.com/pogorelov17/t3-boat/actions/workflows/release.yml"><img alt="CI" src="https://img.shields.io/github/actions/workflow/status/pogorelov17/t3-boat/release.yml?label=CI"></a>
  <a href="https://hub.docker.com/r/pogorelov17/t3-boat"><img alt="amd64 + arm64" src="https://img.shields.io/badge/docker-amd64%20%2B%20arm64-0284c7?logo=docker&amp;logoColor=white"></a>
</p>

<p align="center">
  <a href="#quick-start">Quick start</a> · <a href="https://hub.docker.com/r/pogorelov17/t3-boat">Docker Hub ↗</a> · <a href="https://github.com/pogorelov17/agent-playbook">Agent Playbook ↗</a>
</p>

```mermaid
%%{init: {"flowchart": {"curve": "stepAfter", "rankSpacing": 26, "nodeSpacing": 22}}}%%
flowchart TB
    A(["Owner · Request"]):::endpoint
    B["T3 Code · Lead<br/>Discuss, plan, write a brief"]:::lead
    C["Boat · Start a fresh worker VM"]:::lead

    subgraph VM["Worker VM"]
        D["Worker follows the brief<br/>on its own checkout"]:::work
        R["Returns a result:<br/>report, tests, SHA, draft PR"]:::work
        D --> R
    end

    F["Lead posts a compact update<br/>and releases the VM"]:::lead
    G(["Owner · Result"]):::endpoint

    A --> B --> C --> D
    R --> F
    F -->|Work complete| G
    F -.->|Next brief| B

    classDef lead fill:#e0f2fe,stroke:#0284c7,color:#0c4a6e
    classDef work fill:#f0fdfa,stroke:#0d9488,color:#134e4a
    classDef endpoint fill:#f5f3ff,stroke:#8b5cf6,color:#4c1d95
    style VM fill:#f8fafc,stroke:#cbd5e1,stroke-dasharray:5 5,color:#64748b
```

Coordinate coding agents across your GitHub projects from one Linux machine, local or remote. Bring an idea or an existing repository and work from a T3 chat with Codex, Claude or both. The code itself runs on disposable Boat VMs.

## How it works

Suppose you ask in T3, “Fix the failing test and show me why it broke.”

1. **The team lead talks with you in T3.** It discusses, plans and checks a fact on the web when needed. New chats in all your projects inherit your installation’s lead model and reasoning level. While you are still discussing an idea, the conversation stays in T3 until there is work for a worker.
2. **Workers run on Boat.** The lead starts a worker on a disposable VM and gives it a brief it writes for this task: here, reproduce the failure, fix it, run the tests and open a draft PR. Everything that touches the repository runs there: reading source, edits, installs, builds, tests and extended research. Your machine only hosts T3 and its history. The lead decides how many workers to use and what each does.
3. **The parent chat stays compact.** For each delegation, result and decision, the lead posts who asked whom for what, known dependencies, the result and what it decided, with links to the child chat and the PR instead of shell logs. It reports what it observed; T3 shows a child’s link and the result it returns, not its live progress.
4. **You decide when to merge.** Once a worker’s result is recorded, its VM is deleted. You can come back to the chat or ask for another change on the PR without keeping workers running.

Prefer a fixed team? [Attach a playbook](#optional-playbook): an external package such as [agent-playbook](https://github.com/pogorelov17/agent-playbook), or your own, that defines the roles, their instructions, how many of each run and on which models. T3 Boat only launches its workers.

## Quick start

You need a Linux machine with Docker Engine, a Boat account that can start a small VM, a GitHub account, and the subscriptions enabled in your profile. A repository is optional. The image supports AMD64 and ARM64. [Install Docker Engine](https://docs.docker.com/engine/install/).

### 1. Start the container

Run this on the server. Set `T3_BOAT_PROFILE` to `codex-only`, `claude-only`, or `codex+claude`. Docker downloads the published image on first use. T3 uses the container hostname as its display name. The example uses the server’s short hostname so you can recognize it on your devices.

```sh
docker run -d --name t3-boat --hostname "$(hostname -s)" --restart unless-stopped \
  -p 127.0.0.1:3773:3773 \
  -v t3-boat-data:/data \
  -e T3_BOAT_PROFILE=codex+claude \
  pogorelov17/t3-boat:0.1.5
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

1. Follow the login prompts. Existing valid sessions are reused. For Claude profiles, setup keeps an installed Claude Code that is the pinned version or newer. If `claude --version` fails or prints nothing, setup reinstalls the pinned version. If it prints a version setup cannot read, setup leaves Claude Code unchanged and prints a warning. After sign-in, setup asks whether to attach a [playbook](#optional-playbook); press **Enter** for none. It then shows the **lead model**: the model and reasoning level of your chats, which every worker also opens with. Choose one, or press **Enter** to accept the one shown. Nothing is saved until you accept it. At **GitHub repository URL**, enter a project URL or press **Enter** to open **Start here** with your own idea.
2. Approve the Boat device login if prompted. In Boat, connect GitHub. Open **Agents & Proxying**, select your worker credential profile and connect the subscriptions in the table. Boat supplies these logins to new VMs. Select a private worker environment that passes GitHub and agent credentials and uses that credential profile. For a repository, grant the Boat GitHub App access to that repository with **Contents** and **Pull requests** write permissions. [Boat environments](https://docs.boat.dev/environments/access) · [Worker subscriptions](https://docs.boat.dev/ai-credentials)
3. Setup starts a fresh VM, authenticates to GitHub from it and requests one reply from each enabled provider: the lead model for its own provider, and the CLI’s default model for any other provider. If you supplied a repository, it also reads its Git refs from that VM. Look for each success line, the deleted VM ID and **Setup verified**. If a check fails, fix the reported connection and rerun setup.

**Installing with an AI agent?** Have the account owner complete browser confirmations directly. Report the profile, the local logins, the VM ID and deletion, the providers and models that replied, and the repository checked or deferred. Complete the first-chat check below. After reporting the results, offer the [optional phone setup](#connect-your-phone-optional) with the environment name you verified. If you know the owner’s platform, mention its store. Otherwise, offer both iOS and Android. A running container alone is only the first step.

### 3. Open your first chat

In T3, select your repository, or **Start here** if you skipped the URL, and open a new chat. Write in your own words, in any language. You can ask a question, share an idea, or use an opening message suggested by the agent helping you get set up.

<details>
<summary><strong>First time connecting this computer?</strong></summary>

If T3 runs on this computer, skip the SSH command and create the pairing link directly in a local terminal. For a remote machine, use SSH port forwarding. In a terminal on your computer, replace `USER@SERVER` with your SSH login and connect:

```sh
ssh -L 127.0.0.1:3773:127.0.0.1:3773 -o ExitOnForwardFailure=yes USER@SERVER
```

On the machine running Docker, get a pairing link:

```sh
docker exec -it t3-boat t3 auth pairing create --ttl 5m --base-url http://127.0.0.1:3773
```

For the desktop app, open **Settings → Environments → Add environment → Remote link** and paste the pairing URL. Opening the URL in a browser pairs that browser. Issue a fresh code to pair another client. For a remote machine, keep the SSH session open while using port forwarding. Closing that session or putting the computer to sleep interrupts this route. The container and chat history stay on the server. Run the SSH command again to reconnect to the same installation.

After a server rebuild, verify the new SSH host key fingerprint at the machine’s console before updating the saved key on your computer. If the installation also has a fresh data volume, pair the client to its new T3 environment.

</details>

<details>
<summary><strong>Keep T3 reachable when you close your laptop (T3 Connect)</strong></summary>

Pair your desktop app using the steps above first. On the server, link the container to your T3 Connect account. Follow the device approval prompt in your browser.

```sh
docker exec -it t3-boat t3 connect --headless
```

Once authorized, restart the container to apply the link. Do this between tasks. Docker already manages the server process if the CLI mentions a user-systemd service.

```sh
docker restart t3-boat
docker exec t3-boat t3 connect status --base-dir /data/.t3
```

Provisioning can take a moment. Repeat the status command until it reports **Other devices can reach this machine** and **Tunnel connected**. In the desktop app’s **Settings → Environments**, add **T3 Connect** as a route for the paired server. Close the SSH forward if you opened one, confirm the environment shows **via T3 Connect**, then send a message in a new chat. A reply confirms the route end to end. The tunnel runs on the server while your laptop sleeps.

A fresh data volume creates a new T3 environment. If an older entry shows **tunnel is offline**, select the newly paired environment.

</details>

If you are unsure where to begin, ask the team lead for ideas. Wait for a reply in the new chat to confirm your client can reach the team lead. Planning can continue there until you choose a repository.

## Build your first feature

1. **Choose the repository for this change.** Use the URL of any GitHub repository you can work on. For a brand-new project, initializing the repository with a README gives the first PR a base commit.
2. **Attach the repository.** If you supplied its URL during setup, continue to step 3. Otherwise rerun `docker exec -it t3-boat t3-boat setup` and enter the URL. Setup checks access from a fresh VM. The first real change exercises branch push and draft PR creation.
3. **Continue the conversation.** Use your project chat. If you began in **Start here**, attaching the first repository keeps your planning thread there. When you are ready, ask for the first change you want. A worker implements it on Boat, checks the result, and opens a draft PR. The parent chat records the worker’s model, test results and the PR’s head SHA. With a playbook attached, its roles decide what happens next, such as reviews on separate VMs. GitHub shows the draft PR and any checks configured by that repository. You decide when to merge.

To work on another repository, rerun setup with its URL. To inspect current local logins, Boat configuration and the timestamp of the last successful VM check, run `docker exec t3-boat t3-boat status`. Status does not allocate a VM. Rerun setup for a fresh check after changing profiles, credentials or models.

## Models

`T3_BOAT_PROFILE` selects the subscriptions T3 Boat uses: `codex-only`, `claude-only` or `codex+claude`. Setup saves one model for the profile, the **lead model**: the model and reasoning level of your T3 chats, in every project on this machine. Every worker VM opens with the same model and has every provider of the profile enabled, so the lead can pick another model and reasoning level that T3 lists for each brief, and names its choice in the parent chat.

Setup lists the exact model IDs and reasoning levels that T3 reports for your signed-in providers. Without a playbook nothing is preselected. With a playbook whose model set assigns a model to the role you chose for the lead, setup suggests the newest stable release of that family that supports its reasoning level.

**Change your choices later.** Run `docker exec -it t3-boat t3-boat setup` again. Setup keeps your saved lead model until you type `edit`, or `refresh` to take the playbook’s suggestion. If the model or reasoning level is no longer available, setup asks for a replacement before continuing. The choices are saved in `/data/profiles/PROFILE.json`.

## Optional playbook

A playbook is an external package of role instructions in the format of [agent-playbook](https://github.com/pogorelov17/agent-playbook): a `team.json` manifest (schema 1) that lists each role, the ordered Markdown files of its instructions and how many instances of it run, plus optional model sets that give each role a provider, model family and reasoning level. T3 Boat reads the package as data and never runs code from it. Role names, counts, model assignments, model rules and every workflow instruction come from the package; T3 Boat adds only how to start a worker on Boat, the machine boundary and the compact parent updates.

When setup asks for a playbook, enter:

- **Enter** for none, or to keep the one already attached, and `none` to detach it;
- `bundled` for the copy of agent-playbook built into the image, pinned to commit `a27b16a7d696`;
- or the absolute path of any package directory inside the container. To use your own, copy it into the volume, for example `docker cp ./my-team t3-boat:/data/teams/my-team`, and enter `/data/teams/my-team`.

Setup checks the manifest and every file it names, then asks two questions:

1. **Which role the T3 team lead follows.** agent-playbook calls its owner-facing role `lead`, so that is the default when a package has one; otherwise choose a role, or `none` to have the lead brief each role itself.
2. **Which model set applies.** Setup lists the package’s model sets that use only providers this profile enables, and suggests the one named like the profile.

Then, for every project on this machine:

- Every role’s instructions become a skill for each enabled provider, named after the package and the role, in `/data/.codex/skills/` and `/data/.claude/skills/`. Nothing is added to your repositories. The team lead is told to load and follow the guide of the role you chose from its first response, and its Boat skill lists every role with its instance count, guide and the selected model assignment, quoted from the package together with its model rules.
- `boat-worker start ROLE` starts a worker for any role of the package. Its VM receives that role’s instructions in its provider’s global instruction file (`~/.codex/AGENTS.md` for Codex, `~/.claude/CLAUDE.md` for Claude) before it does anything. `boat-worker start` without a role starts a worker that has only the lead’s brief and a short worker note. Codex reads a non-empty `~/.codex/AGENTS.override.md` instead of `AGENTS.md`; if the Boat environment provides one, the instructions are appended to it after its own text.
- The package directory is read again at every container start and worker start, so edits to your own package apply after `docker restart t3-boat`. If the package can no longer be read, T3 still starts, without the profile, and the container log and `t3-boat status` name the file; rerun setup to attach it again or detach it.

Setup writes only skill files it generated itself, which carry a `t3-boat: generated from an attached playbook` line. A file you created at the same path is never replaced and is used as that role’s guide. If something else occupies the path, that guide is not installed, the team lead is not pointed to it, and setup and the container log say so. Files you add to a skill folder stay there. Detaching the playbook removes the generated skills.

Each `T3_BOAT_PROFILE` keeps its own lead model and playbook attachment. `t3-boat status` prints the attachment.

<details>
<summary><strong>Fresh installation required</strong></summary>

This version supports clean installations only. Start it on a new volume, for example `-v t3-boat-data-2:/data`, and run setup. A volume from another version is not converted: a container started on one stops with a one-line message naming the profile file and leaves the file unchanged. Keep that volume as it is if you want its chat history.

</details>

<details>
<summary><strong>Build a customized image</strong></summary>

To change the packaged tools or workflow instructions, build from your edited source checkout. Use `t3-boat:local` in the start command.

```sh
docker build -t t3-boat:local .
```

The build fetches agent-playbook at the commit named in `scripts/fetch-playbook`, validates it with the package’s own checker and stores it as the `bundled` choice. To bundle another commit, change `commit` there and rebuild. Any other package can be attached from the volume without rebuilding. To run the unit tests from a checkout, run `python3 -m unittest discover -s tests`; to include the checks against the bundled package, first fetch it with `sh scripts/fetch-playbook playbook`.

</details>

## Connect your phone (optional)

1. If you already use T3 Connect on desktop, continue to step 2. Otherwise, enable T3 Connect on the machine running T3 as described in [Open your first chat](#3-open-your-first-chat).
2. On your phone, open the [official T3 Code site](https://t3.codes/). Under **Also on your phone**, choose **iOS** for the App Store or **Android** for Google Play. Install T3 Code, then open it and sign in with the same T3 account you linked to that machine.
3. Select your environment through T3 Connect. Open the chat you used during setup and send a message to confirm you can continue it from your phone.

Keep the machine running T3 awake and online while connecting from your phone.
