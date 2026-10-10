# Khaya collector + dashboard.
# Stdlib-only Python app (Python 3.10+); the only OS dependency is the `ping` binary.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# iputils-ping provides the `ping` binary used by network/monitor.py for ICMP checks.
# libcap2-bin is used to grant ping NET_RAW so it works as a non-root user.
RUN apt-get update \
    && apt-get install -y --no-install-recommends iputils-ping libcap2-bin \
    && setcap cap_net_raw+ep /bin/ping \
    && apt-get purge -y --auto-remove libcap2-bin \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Runtime sources only: API/collector package, static dashboard, heartbeat agent script.
COPY network/ ./network/
COPY static/ ./static/
COPY heartbeat_agent.py ./

# Writable location for the default SQLite path (data/network.db).
RUN useradd --create-home --shell /usr/sbin/nologin appuser \
    && mkdir -p /app/data \
    && chown -R appuser:appuser /app
VOLUME /app/data

USER appuser

EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/api/snapshot', timeout=5)"

# Bind 0.0.0.0 inside the container so the dashboard is reachable via published ports.
# Override with: docker run ... khaya --host 127.0.0.1 --port 8766 --db /app/data/custom.db
CMD ["python3", "-m", "network.server", "--host", "0.0.0.0", "--port", "8765"]
