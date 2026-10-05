FROM python:3.12-slim-bookworm@sha256:54c85f3c47607a77f32adec749d3c81d1348bf25833671f512b26a9b6d778cb3
ENV PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1
COPY portal/panel/requirements.txt /tmp/panel-requirements.txt
COPY modules/session-jobs/requirements.txt /tmp/job-requirements.txt
RUN pip install --require-hashes --only-binary=:all: -r /tmp/panel-requirements.txt
RUN python -m venv /opt/jobs \
 && /opt/jobs/bin/pip install --require-hashes --only-binary=:all: -r /tmp/job-requirements.txt
COPY portal/panel/app /suite/panel
COPY portal/e2e /suite/e2e
COPY modules/session-jobs /suite/session-jobs
WORKDIR /suite/e2e
USER 65532:65532
ENTRYPOINT ["python", "run_scenarios.py"]
