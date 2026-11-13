# syntax=docker/dockerfile:1.7
# Two independent images from one file:
#   docker build --target api -t nl-assistant-api .
#   docker build --target mcp -t nl-assistant-mcp .

FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN useradd --create-home --uid 10001 appuser
WORKDIR /srv
COPY pyproject.toml ./

FROM base AS api
COPY app ./app
RUN pip install ".[api]"
USER appuser
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=2).status==200 else 1)"
CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--forwarded-allow-ips", "*", "--timeout-keep-alive", "75"]

FROM base AS mcp
COPY mcp_server ./mcp_server
RUN pip install ".[mcp-server]"
USER appuser
EXPOSE 8001
HEALTHCHECK --interval=30s --timeout=3s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8001/health',timeout=2).status==200 else 1)"
CMD ["uvicorn", "mcp_server.server:app", "--host", "0.0.0.0", "--port", "8001", "--timeout-keep-alive", "75"]
