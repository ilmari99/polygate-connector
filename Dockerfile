# Read-only Polymarket research MCP server.
#
# Run hardened (the read-only filesystem is a runtime flag, not an image
# property), publishing the port to loopback only so nothing but the tunnel
# reaches it:
#
#   docker build -t polygate-connector .
#   docker run --rm --read-only --tmpfs /tmp --cap-drop=ALL \
#     -p 127.0.0.1:8765:8765 -e PUBLIC_HOST=mcp.example.com polygate-connector

FROM python:3.12-slim

RUN useradd --create-home --shell /usr/sbin/nologin app

WORKDIR /build
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .

USER app
WORKDIR /home/app

# Inside the container the process must bind all interfaces; the port mapping
# above is what keeps it loopback-only on the host.
ENV BIND_HOST=0.0.0.0
EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/healthz', timeout=4)"]

CMD ["polygate-connector"]
