# syntax=docker/dockerfile:1
FROM node:24-bookworm-slim

ARG TARGETARCH
RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates curl git jq openssh-client python3 ripgrep tini \
    && rm -rf /var/lib/apt/lists/*

# Upstream archives include the T3 runtime and web assets. Download them at
# build time so a new volume also works when GitHub is temporarily unavailable.
RUN set -eu; \
    case "${TARGETARCH:-$(dpkg --print-architecture)}" in \
      amd64) arch=x64; t3_sha=2b691c07f5bcb5adea4a863d5324aae7db21bd6d82280cec7787836dcaa013e1; boat_sha=9f2fb5529ad7ca58e4d724bb6bfcd6ff94cb3077a7c12a0941e501aa0e9335ec ;; \
      arm64) arch=arm64; t3_sha=7d90a5af032fe0988a616403e0f51a6cb68eff7ed7a84bc40999cc3997dffd71; boat_sha=38c250ea83237c4836367684fdfaf5f8e7de2cec15d50b4a6feae6adb863efa7 ;; \
      *) echo 'Supported architectures: amd64, arm64' >&2; exit 1 ;; \
    esac; \
    mkdir -p /opt/t3-runtime /usr/share/doc/boat; \
    curl -fsSL --retry 3 "https://github.com/pingdotgg/t3code/releases/download/v0.1.11/t3-0.1.11-linux-${arch}.tar.gz" -o /tmp/t3.tar.gz; \
    echo "$t3_sha  /tmp/t3.tar.gz" | sha256sum -c -; \
    tar -xzf /tmp/t3.tar.gz -C /opt/t3-runtime --strip-components=1; \
    rm /tmp/t3.tar.gz; \
    ln -s /opt/t3-runtime/t3 /usr/local/bin/t3; \
    curl -fsSL --retry 3 https://raw.githubusercontent.com/pingdotgg/t3code/v0.1.11/LICENSE -o /opt/t3-runtime/LICENSE; \
    curl -fsSL --retry 3 "https://github.com/ariana-dot-dev/agent-server/releases/download/boat-cli-v1.0.105/boat-linux-${arch}" -o /usr/local/bin/boat; \
    echo "$boat_sha  /usr/local/bin/boat" | sha256sum -c -; \
    chmod 755 /usr/local/bin/boat; \
    curl -fsSL --retry 3 https://raw.githubusercontent.com/ariana-dot-dev/agent-server/boat-cli-v1.0.105/LICENSE -o /usr/share/doc/boat/LICENSE; \
    t3 --version; boat --help >/dev/null

RUN --mount=type=bind,source=scripts/install-codex,target=/tmp/install-codex \
    sh /tmp/install-codex && npm cache clean --force

ENV HOME=/data \
    PATH=/opt/t3-boat/bin:/data/.local/bin:${PATH} \
    BOAT_NO_UPDATE=1 \
    PYTHONDONTWRITEBYTECODE=1
RUN install -d -m 700 -o node -g node /data \
    && usermod --home /data node
COPY bin/ /opt/t3-boat/bin/
COPY lib/ /opt/t3-boat/lib/
COPY --chmod=755 scripts/install-claude /usr/local/bin/install-claude
COPY skills/t3-boat/ /opt/t3-boat/skills/t3-boat/
# COPY keeps the build checkout's modes; a restrictive umask there must not hide files from the node user.
RUN chmod -R a+rX,go-w /opt/t3-boat/bin /opt/t3-boat/lib /opt/t3-boat/skills && chmod 755 /opt/t3-boat/bin/* \
    && ln -s /opt/t3-boat/bin/boat-worker /usr/local/bin/boat-worker \
    && ln -s /opt/t3-boat/bin/t3-boat /usr/local/bin/t3-boat \
    && ln -s /data/.local/bin/claude /usr/local/bin/claude

# Optional, off by default: `--build-arg BUNDLE_PLAYBOOK=1` adds agent-playbook at the commit pinned in
# scripts/fetch-playbook, which setup then offers as the `bundled` playbook. The default image contains no
# playbook, and neither its build nor a running container fetches or needs one.
ARG BUNDLE_PLAYBOOK=0
ARG PLAYBOOK_REPOSITORY=
RUN --mount=type=bind,source=scripts/fetch-playbook,target=/tmp/fetch-playbook \
    case "$BUNDLE_PLAYBOOK" in 0|1) ;; *) echo 'BUNDLE_PLAYBOOK must be 0 or 1' >&2; exit 1 ;; esac; \
    if [ "$BUNDLE_PLAYBOOK" = 1 ]; then \
      PLAYBOOK_REPOSITORY="$PLAYBOOK_REPOSITORY" sh /tmp/fetch-playbook /opt/t3-boat/playbook \
      && chmod -R a+rX,go-w /opt/t3-boat/playbook; \
    fi
USER node:node
WORKDIR /data
VOLUME ["/data"]
EXPOSE 3773
ENTRYPOINT ["/usr/bin/tini", "--", "t3-boat"]
CMD ["serve"]
