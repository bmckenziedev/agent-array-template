FROM python:3.13-slim-trixie@sha256:3dd7cc108ec1493442514f5c2a871af6af0ec31d768ff6e378a93340c3b3db5f

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY portal/panel/requirements.txt /app/requirements.txt
RUN pip install --require-hashes --only-binary=:all: -r requirements.txt \
    && useradd --create-home --uid 1000 panel
COPY portal/panel/app/ /app/
USER 1000
EXPOSE 8080 8081
CMD ["python", "main.py"]
