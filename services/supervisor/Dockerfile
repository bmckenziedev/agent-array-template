FROM node:22-bookworm-slim@sha256:43ac6c60b8f89723f746e8a92ce91abd5017e627ce1ddfe4238355d3a30b772c
RUN apt-get update && apt-get install -y --no-install-recommends python3 tmux ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 && groupmod -n aa-supervisor node \
 && usermod -l aa-supervisor node
COPY services/supervisor/aa_supervisor/ /opt/supervisor/aa_supervisor/
COPY services/supervisor/bin/aa-supervise /usr/local/bin/aa-supervise
RUN chmod 0755 /usr/local/bin/aa-supervise
# VERIFY: compare tmux -V with the session image in the image build pipeline.
ENV PYTHONPATH=/opt/supervisor PYTHONDONTWRITEBYTECODE=1 HOME=/run/aa-supervisor
USER 1000:1000
WORKDIR /opt/supervisor
ENTRYPOINT ["python3", "-m", "aa_supervisor"]
