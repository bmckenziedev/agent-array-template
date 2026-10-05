ARG PYTHON_IMAGE=python:3.12-slim@sha256:02108f5d322dd89f1c9e552442c25acb0543dfdbc455693a5599624f20d9155d
FROM ${PYTHON_IMAGE}
WORKDIR /opt/agent-array
COPY mcp/servers/_template/aa_mcp.py mcp/servers/_template/aa_mcp.py
COPY services/farm-mcp/farm_mcp.py services/farm-mcp/farm_mcp.py
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
USER 10001:10001
EXPOSE 8080
ENTRYPOINT ["python", "/opt/agent-array/services/farm-mcp/farm_mcp.py"]
