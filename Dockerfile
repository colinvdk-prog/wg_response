# WG Response — interactive simulator, containerized for AWS App Runner.
# Stdlib-only at runtime: the simulator serves cached terrain tiles + road
# graphs and mocks every feed, so no third-party packages are installed.
FROM python:3.13-slim

WORKDIR /app

# Copy the package + UI + committed data (tiles/roads) + design doc.
# .dockerignore keeps tests/scratch/bench/.git/out out of the image.
COPY . /app

# Public demo defaults:
#  - bind all interfaces on App Runner's port
#  - dev-open auth (no key) — the simulator has no sensitive data / no live feeds
#  - hold more concurrent visitors than the local default of 4
ENV PYTHONUNBUFFERED=1 \
    WGRESP_DEV_OPEN=1 \
    WGRESP_SIM_MAX_SESSIONS=16 \
    PORT=8080

EXPOSE 8080

# App Runner sends traffic to $PORT; simui binds 0.0.0.0 so it's reachable.
CMD ["sh", "-c", "python -m wgresponse.cli simui --host 0.0.0.0 --port ${PORT}"]
