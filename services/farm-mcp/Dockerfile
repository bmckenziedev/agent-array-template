ARG PYTHON_IMAGE
FROM ${PYTHON_IMAGE}
WORKDIR /opt/agent-array
COPY mcp/servers/_template/aa_mcp.py mcp/servers/_template/aa_mcp.py
COPY services/farm-mcp/farm_mcp.py services/farm-mcp/farm_mcp.py
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
USER 10001:10001
EXPOSE 8080
ENTRYPOINT ["python", "/opt/agent-array/services/farm-mcp/farm_mcp.py"]
