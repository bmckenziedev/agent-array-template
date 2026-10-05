# syntax=docker/dockerfile:1
# Build context: repository root. Vendor locks are preserved unchanged.
FROM node:22-bookworm-slim@sha256:43ac6c60b8f89723f746e8a92ce91abd5017e627ce1ddfe4238355d3a30b772c

ARG CLAUDE_CLI_VERSION=2.1.285
ARG PNPM_VERSION=12.9.1
ARG PNPM_SHA512=06b055fff5cd20dc12781de173ced3310ce0951bf2ec620211a16045d5444d5c93a663210764ef29a653a610459ba0368f0e74f84d80c54723cf95aae7bc0d6d

ENV DEBIAN_FRONTEND=noninteractive \
    LANG=C.UTF-8 \
    DISABLE_AUTOUPDATER=1 \
    COREPACK_HOME=/opt/corepack \
    COREPACK_ENABLE_DOWNLOAD_PROMPT=0 \
    COREPACK_DEFAULT_TO_LATEST=0

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      ca-certificates git ripgrep fd-find jq tmux python3 python3-venv tini procps less \
      bubblewrap socat \
 && ln -s /usr/bin/fdfind /usr/local/bin/fd \
 && rm -rf /var/lib/apt/lists/*

COPY sessions/claude/image/npm/package.json sessions/claude/image/npm/package-lock.json /opt/aa/npm/
RUN cd /opt/aa/npm \
 && npm ci --ignore-scripts --no-audit --no-fund \
 && node node_modules/@anthropic-ai/claude-code/install.cjs \
 && test "$(node -p "require('./node_modules/@anthropic-ai/claude-code/package.json').version")" = "${CLAUDE_CLI_VERSION}" \
 && rm -rf node_modules/@anthropic-ai/claude-code-linux-*-musl \
 && ln -s /opt/aa/npm/node_modules/@anthropic-ai/claude-code/bin/claude.exe /usr/local/bin/claude \
 && claude --version \
 && npm cache clean --force

RUN test "$(uname -m)" = x86_64 \
 && corepack enable pnpm \
 && corepack install -g "pnpm@${PNPM_VERSION}+sha512.${PNPM_SHA512}" \
 && d="${COREPACK_HOME}/v1/pnpm/${PNPM_VERSION}" \
 && test -x /opt/aa/npm/node_modules/@pnpm/exe.linux-x64/pnpm \
 && test "$(node -p "require('/opt/aa/npm/node_modules/@pnpm/exe.linux-x64/package.json').version")" = "${PNPM_VERSION}" \
 && mkdir -p "$d/node_modules/@pnpm" \
 && mv /opt/aa/npm/node_modules/@pnpm/exe.linux-x64 "$d/node_modules/@pnpm/" \
 && test "$(COREPACK_ENABLE_NETWORK=0 pnpm --version)" = "${PNPM_VERSION}" \
 && test ! -e "$d/pnpm-native" \
 && chmod -R a+rX "${COREPACK_HOME}"
ENV COREPACK_ENABLE_NETWORK=0

RUN groupmod -n claude node \
 && usermod -l claude -d /home/claude -m node \
 && id claude \
 && mkdir -p /work /etc/claude-code \
 && chown claude:claude /work

COPY sessions/claude/image/gitconfig /etc/gitconfig
COPY sessions/claude/image/tmux.conf /etc/tmux.conf
COPY sessions/claude/image/bin/ /opt/aa/bin/
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

USER claude
ENV HOME=/home/claude \
    CLAUDE_CONFIG_DIR=/home/claude/.claude
WORKDIR /work

ENTRYPOINT ["/usr/bin/tini", "--", "/opt/aa/bin/entrypoint.sh"]
CMD ["claude"]
