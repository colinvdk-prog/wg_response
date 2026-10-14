# WG Response — one image, two App Runner services:
#   wg-simulator       CMD below (interactive simulator, cached tiles, mocks)
#   wg-response-watch  StartCommand override: `python -m wgresponse.cli watch
#                      ...` — the SHADOW-OPS worker (live feeds, real cycles,
#                      dry-notify: computes everything, sends nothing).
# The official python image via AWS's ECR Public mirror of the Docker
# Official Images: CodeBuild's shared IPs hit Docker Hub's anonymous pull
# limit (429 on the world14 build, 2026-10-14).
FROM public.ecr.aws/docker/library/python:3.13-slim

WORKDIR /app

# Copy the package + UI + committed data (tiles/roads) + design doc.
# .dockerignore keeps tests/scratch/bench/.git/out out of the image.
COPY . /app

# Live-terrain extras for the watch worker (LANDFIRE LFPS GeoTIFF decode:
# tifffile+numpy+imagecodecs; boto3 for the Phase-2 Dynamo registry). The
# simulator ignores them; core stays stdlib.
RUN pip install --no-cache-dir -r requirements.txt

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
