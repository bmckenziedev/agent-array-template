FROM node:22-bookworm-slim@sha256:43ac6c60b8f89723f746e8a92ce91abd5017e627ce1ddfe4238355d3a30b772c AS tools
WORKDIR /build/js
COPY modules/factory/engine/js/package*.json ./
RUN npm ci --ignore-scripts --no-audit --no-fund

FROM python:3.12-slim
COPY --from=tools /usr/local/bin/node /usr/local/bin/node
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /opt/factory
COPY modules/factory/engine/ ./engine/
COPY modules/factory/queue/ ./queue/
COPY modules/factory/api/ ./api/
COPY modules/factory/index/ ./index/
COPY --from=tools /build/js/node_modules ./engine/js/node_modules/
RUN pip install --no-cache-dir --require-hashes --no-deps -r index/requirements.txt
ENV PYTHONPATH=/opt/factory/engine:/opt/factory/queue:/opt/factory/api:/opt/factory/index
RUN useradd --uid 10001 --create-home factory && mkdir /var/lib/factory && chown factory /var/lib/factory
USER 10001:10001
EXPOSE 8080
CMD ["python", "-m", "factory_api.service"]
