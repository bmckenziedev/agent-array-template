FROM debian:bookworm-slim@sha256:3783cc01769c7b2b1b83a5c5ad96c815348e28ed7da68e2e3687004faa906251 AS build
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl git python3 python3-venv && rm -rf /var/lib/apt/lists/*
ARG HERMES_VERSION=v2026.9.24
ARG HERMES_COMMIT=f97608f178d1ffeca59860195ab7da295f7c8e5f
ARG HERMES_LOCK_SHA256=5b3798f326209475abca8ef7cbf7c9406f12e687c28c0b540dfe597466f48590
RUN git clone --depth 1 --branch "$HERMES_VERSION" https://github.com/NousResearch/hermes-agent.git /opt/hermes  && test "$(git -C /opt/hermes rev-parse HEAD)" = "$HERMES_COMMIT"  && echo "$HERMES_LOCK_SHA256  /opt/hermes/uv.lock" | sha256sum -c -  && rm -rf /opt/hermes/.git
FROM debian:bookworm-slim@sha256:3783cc01769c7b2b1b83a5c5ad96c815348e28ed7da68e2e3687004faa906251
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates tini python3  && rm -rf /var/lib/apt/lists/*  && groupadd --gid 10001 hermes  && useradd --uid 10001 --gid 10001 --home-dir /var/lib/hermes --no-create-home hermes  && find / -xdev -type f -perm /6000 -exec chmod a-s {} +
COPY --from=build /opt/hermes /opt/hermes
COPY mcp/servers/arrayops /opt/arrayops
COPY modules/hermes-ops-chat/gateway.py /opt/ops-chat/gateway.py
ENV HOME=/var/lib/hermes ARRAYOPS_READONLY=1 PYTHONDONTWRITEBYTECODE=1
USER 10001:10001
WORKDIR /var/lib/hermes
ENTRYPOINT ["/usr/bin/tini", "--", "python3", "/opt/ops-chat/gateway.py"]
