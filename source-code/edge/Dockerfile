# P04.05: edge runtime container. Digest-pinned base (python:3.12-slim),
# pinned dependency closure (requirements-lock.txt), non-root, no build
# toolchain in the final image. Budget: 0.25 CPU / 256 MiB request,
# 0.75 CPU / 512 MiB limit per logical edge instance (ADR-0006,
# docs/environment/RESOURCE_BUDGET.md) - enforced by the container run
# command (--cpus/--memory), not by this image.

FROM python:3.12-slim@sha256:2f17fc044b579bab302c2e8054d3a686e2cb9a83de48e70534b94cd8ebbe06a9 AS base

RUN groupadd --gid 10001 edge && \
    useradd --uid 10001 --gid edge --no-create-home --shell /usr/sbin/nologin edge

WORKDIR /app
COPY edge/requirements-lock.txt /app/requirements-lock.txt
RUN pip install --disable-pip-version-check --no-cache-dir --no-deps -r /app/requirements-lock.txt

COPY edge/ /app/edge/
COPY contracts/ /app/contracts/
ENV EDGE_CONTRACTS_ROOT=/app/contracts
ENV PYTHONPATH=/app
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

USER edge:edge
ENTRYPOINT ["python3", "-m", "edge.main"]
