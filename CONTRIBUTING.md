# Contributing

Run the unit tests and exercise all three profiles with a local image before opening a pull request.

```sh
python3 -m unittest discover -s tests -v
docker build -t t3-boat:test .
bash tests/smoke-image.sh t3-boat:test
```

## Releases

Configure the Actions variable `DOCKERHUB_REPOSITORY` and the secrets `DOCKERHUB_USERNAME` and `DOCKERHUB_TOKEN` for a public Docker Hub repository. Pin the Quick start to the version being released.

Before tagging, check the account-backed workflow for each profile: start a planning chat before connecting Boat, attach a README-only repository to the same chat, request a first feature, verify the draft PR and two independent reviews at its final SHA, and confirm the workers were deleted. The offline smoke test covers startup and chat persistence.

After merge, push a tag of the form `vX.Y.Z`. The workflow publishes the tested AMD64 and ARM64 images under the same version tag. The GitHub release includes a pull command and a link to that version's Quick start.
