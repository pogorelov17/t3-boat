# Contributing

## Where to make a change

| Path | Responsibility |
| --- | --- |
| `Dockerfile` | Build the coordinator image with T3, Boat and the pinned Codex CLI. |
| `bin/t3-boat` | Start T3, connect accounts and projects, and report readiness. |
| `bin/boat-worker` | Provision and link a fresh Boat VM for a role. Clean up the VM if linking fails. |
| `bin/profile.py` | Read and validate role assignments. Apply T3 settings and install the orchestration skill for each enabled provider. |
| `bin/install-claude` | Install the pinned Claude Code CLI in the user's persistent volume when setup needs it. Repeated setup reuses that version. |
| `profiles/` | Set the provider, model and reasoning effort for each role. |
| `skills/t3-boat/SKILL.md` | Define how the orchestrator delegates, reviews and delivers a PR. Codex and Claude share this source. |
| `.github/workflows/release.yml` | Check PRs on both supported architectures and publish tested images when a version tag is pushed. |

At startup, the profile loader adds the selected roles to the skill and points each enabled provider to its installed copy through Codex's `AGENTS.md` or Claude's `CLAUDE.md`.

## Verification

Run the unit tests and exercise all three profiles with a local image before opening a pull request.

```sh
python3 -m unittest discover -s tests -v
docker build -t t3-boat:test .
bash tests/smoke-image.sh t3-boat:test
```

The unit tests cover account and workspace setup, protection against fetching source on the coordinator, model independence, profile upgrades, and deletion of a worker after a failed bootstrap. The image smoke test starts real T3 offline and checks that the same thread survives repository attachment and container replacement. Worker credentials and a real draft PR need an account-backed run.

## Releases

Configure the Actions variable `DOCKERHUB_REPOSITORY` and the secrets `DOCKERHUB_USERNAME` and `DOCKERHUB_TOKEN` for a public Docker Hub repository. Pin the Quick start to the version being released.

Before tagging, check the account-backed workflow for each profile: start a planning chat before connecting Boat, attach a README-only repository to the same chat, request a first feature, verify the draft PR and two independent reviews at its final SHA, and confirm the workers were deleted. The offline smoke test covers startup and chat persistence.

After merge, push a tag of the form `vX.Y.Z`. The workflow publishes the tested AMD64 and ARM64 images under the same version tag. The GitHub release includes a pull command and a link to that version's Quick start.
