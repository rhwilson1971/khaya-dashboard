# Khaya collector + dashboard.
# Python 3.12 app; PostgreSQL is the only supported database.
# The only OS dependency is the `ping` binary.
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

# PostgreSQL driver (psycopg 3).
COPY requirements.txt ./
RUN pip install --no-cache-dir --disable-pip-version-check -r requirements.txt

# Runtime sources: API/collector package, standalone database tooling, static
# dashboard, and the heartbeat agent script.
COPY network/ ./network/
COPY db/ ./db/
COPY static/ ./static/
COPY heartbeat_agent.py ./

RUN useradd --create-home --shell /usr/sbin/nologin appuser \
    && chown -R appuser:appuser /app

USER appuser

EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/api/snapshot', timeout=5)"

# Provide KHAYA_DATABASE_URL at runtime and initialize the database first:
#   docker run ... -e KHAYA_DATABASE_URL=postgresql://... khaya python3 -m db init
#   docker run ... -e KHAYA_DATABASE_URL=postgresql://... -p 8765:8765 khaya
# Bind 0.0.0.0 inside the container so the dashboard is reachable via published ports.
CMD ["python3", "-m", "network.server", "--host", "0.0.0.0", "--port", "8765"]
