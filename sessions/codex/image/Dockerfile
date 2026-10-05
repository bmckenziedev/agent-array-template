# syntax=docker/dockerfile:1
# Build context: repository root. Vendor locks are preserved unchanged.
FROM node:22-bookworm-slim@sha256:43ac6c60b8f89723f746e8a92ce91abd5017e627ce1ddfe4238355d3a30b772c

ARG CODEX_CLI_VERSION=0.160.0

ENV DEBIAN_FRONTEND=noninteractive \
    LANG=C.UTF-8

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      ca-certificates git ripgrep fd-find jq tmux python3 python3-venv util-linux tini procps less \
 && ln -s /usr/bin/fdfind /usr/local/bin/fd \
 && rm -rf /var/lib/apt/lists/*

COPY sessions/codex/image/npm/package.json sessions/codex/image/npm/package-lock.json /opt/aa/npm/
RUN cd /opt/aa/npm \
 && npm ci --ignore-scripts --no-audit --no-fund \
 && test "$(node -p "require('./node_modules/@openai/codex/package.json').version")" = "${CODEX_CLI_VERSION}" \
 && plat="$(node -p "({x64:'linux-x64',arm64:'linux-arm64'})[process.arch]")" \
 && test "$(node -p "require('./node_modules/@openai/codex-${plat}/package.json').version")" = "${CODEX_CLI_VERSION}-${plat}" \
 && ln -s /opt/aa/npm/node_modules/@openai/codex/bin/codex.js /usr/local/bin/codex \
 && test "$(codex --version)" = "codex-cli ${CODEX_CLI_VERSION}" \
 && npm cache clean --force

RUN groupmod -n codex node \
 && usermod -l codex -d /home/codex -m node \
 && id codex \
 && mkdir -p /work /etc/codex \
 && chown codex:codex /work

COPY sessions/codex/image/gitconfig /etc/gitconfig
COPY sessions/codex/image/tmux.conf /etc/tmux.conf
COPY sessions/codex/image/bin/ /opt/aa/bin/
COPY sessions/common/bin/ /usr/local/bin/
ARG WITH_FACTORY_MCP=1
RUN --mount=type=bind,source=.,target=/build,ro \
    if [ "$WITH_FACTORY_MCP" = 1 ] && [ -d /build/modules/factory/mcp ] && [ -d /build/modules/factory/index ]; then \
      mkdir -p /opt/factory; cp -a /build/modules/factory/mcp /opt/factory/mcp; \
      cp -a /build/modules/factory/index /opt/factory/index; \
      python3 -m venv /opt/factory/venv; \
      site="$('/opt/factory/venv/bin/python' -c 'import site; print(site.getsitepackages()[0])')"; \
      printf '%s\n' /opt/factory/mcp /opt/factory/index > "$site/factory.pth"; \
      if [ -f /opt/factory/mcp/requirements.txt ]; then /opt/factory/venv/bin/pip install -r /opt/factory/mcp/requirements.txt; fi; \
    fi
RUN chmod 0755 /opt/aa/bin/* /usr/local/bin/aa-* /usr/local/bin/entrypoint-check \
 && for b in /opt/aa/bin/aa-*; do ln -sf "$b" "/usr/local/bin/$(basename "$b")"; done \
 && find / -xdev -type f -perm /6000 -exec chmod a-s {} + \
 && test -z "$(find / -xdev -type f -perm /6000)"

USER codex
ENV HOME=/home/codex \
    CODEX_HOME=/home/codex/.codex
WORKDIR /work

ENTRYPOINT ["/usr/bin/tini", "--", "/opt/aa/bin/entrypoint.sh"]
CMD ["codex"]
