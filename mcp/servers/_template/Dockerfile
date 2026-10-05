ARG PYTHON_IMAGE
FROM ${PYTHON_IMAGE}
WORKDIR /opt/mcp
COPY mcp/servers/_template/aa_mcp.py mcp/servers/_template/server.py ./
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
USER 10001:10001
EXPOSE 8080
ENTRYPOINT ["python", "/opt/mcp/server.py"]
