# session-runner image: executes the per-task pipeline inside the gVisor sandbox.
# Keep it lean; it runs untrusted generated code, so no privileged tooling.
#
# Digest-pinned base; update deliberately with image and offline checks.
FROM python:3.12-slim-bookworm@sha256:54c85f3c47607a77f32adec749d3c81d1348bf25833671f512b26a9b6d778cb3

# apt packages are NOT version-pinned, on purpose:
#  * integrity is already enforced: apt verifies every .deb against the
#    InRelease file signed by the Debian archive keys that ship in the
#    digest-pinned base image (no unsigned or third-party repo is added);
#  * deb.debian.org drops a version as soon as a security update supersedes it,
#    so `pkg=<exact>` pins would break the build on every Debian security
#    release and push us toward stale, unpatched packages.
# The exact set that went in is recorded by the SBOM attestation that
# .github/workflows/images.yml attaches to every pushed image. For a
# bit-for-bit rebuild, point sources at snapshot.debian.org for the build date.
#
# Python deps: requirements.txt is the FULL hash-locked closure compiled from
# requirements.in; --require-hashes rejects any file whose sha256 is not listed
# and any unlisted transitive package; --only-binary=:all: means no setup.py
# runs at build time.
ENV DEBIAN_FRONTEND=noninteractive PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
COPY modules/session-jobs/requirements.txt /tmp/requirements.txt
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      git curl ca-certificates rsync build-essential nodejs npm \
 && rm -rf /var/lib/apt/lists/* \
 && pip install --no-cache-dir --require-hashes --only-binary=:all: -r /tmp/requirements.txt \
 && rm -f /tmp/requirements.txt \
 && useradd -m -u 1000 agent

COPY modules/session-jobs/orchestrator.py  /usr/local/bin/orchestrator.py
COPY modules/session-jobs/entrypoint.sh    /usr/local/bin/entrypoint.sh
COPY modules/session-jobs/diff_bundler.sh  /usr/local/bin/diff_bundler.sh
# tester.py runs in the secret-free `tester` sidecar (see task-job.template.yaml).
COPY modules/session-jobs/tester.py /usr/local/bin/tester.py
RUN chmod +x /usr/local/bin/entrypoint.sh /usr/local/bin/diff_bundler.sh

USER agent
WORKDIR /workspace
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
