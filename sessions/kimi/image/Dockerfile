# syntax=docker/dockerfile:1
# Build context: repository root. Vendor locks are preserved unchanged.
FROM node:22-bookworm-slim@sha256:43ac6c60b8f89723f746e8a92ce91abd5017e627ce1ddfe4238355d3a30b772c

ARG KIMI_CLI_VERSION=2.1.1

ENV DEBIAN_FRONTEND=noninteractive \
    LANG=C.UTF-8 \
    KIMI_DISABLE_TELEMETRY=1 \
    KIMI_CODE_NO_AUTO_UPDATE=1 \
    KIMI_DISABLE_CRON=1 \
    KIMI_CODE_MODEL_CATALOG_REFRESH_ON_START=0 \
    KIMI_CODE_MODEL_CATALOG_REFRESH_INTERVAL_MS=0 \
    KIMI_CODE_WATCH=0 \
    NPM_CONFIG_UPDATE_NOTIFIER=false

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      ca-certificates git ripgrep fd-find jq tmux python3 python3-venv tini procps less \
 && ln -s /usr/bin/fdfind /usr/local/bin/fd \
 && rm -rf /var/lib/apt/lists/*

COPY sessions/kimi/image/npm/package.json sessions/kimi/image/npm/package-lock.json /opt/aa-kimi/npm/
RUN cd /opt/aa-kimi/npm \
 && npm ci --ignore-scripts --omit=optional --no-audit --no-fund \
 && test "$(node -p "require('./node_modules/@moonshot-ai/kimi-code/package.json').version")" = "${KIMI_CLI_VERSION}" \
 && test ! -e node_modules/node-pty \
 && ln -s /opt/aa-kimi/npm/node_modules/@moonshot-ai/kimi-code/dist/main.mjs /usr/local/bin/kimi \
 && test "$(kimi --version)" = "${KIMI_CLI_VERSION}" \
 && npm cache clean --force

RUN groupmod -n kimi node \
 && usermod -l kimi -d /home/kimi -m node \
 && id kimi \
 && mkdir -p /work /etc/kimi \
 && chown kimi:kimi /work

COPY sessions/kimi/image/gitconfig /etc/gitconfig
COPY sessions/kimi/image/tmux.conf /etc/tmux.conf
COPY sessions/kimi/image/bin/ /opt/aa/bin/
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

USER kimi

ENV HOME=/home/kimi \
    KIMI_CODE_HOME=/home/kimi/.kimi-code
WORKDIR /work

ENTRYPOINT ["/usr/bin/tini", "--", "/opt/aa/bin/entrypoint.sh"]
CMD ["kimi"]
