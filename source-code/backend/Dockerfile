# P11.01: one image for every Python service of the platform (operator API, scenario control, ingestion, gateway, command
# executor, outcome verifier, platform correlator, remediation worker, platform probe). The service is chosen by the container's
# command, not by a different image: every service is built from the same reviewed source and the same pinned closure, so there is
# one thing to scan, sign and roll back.
#
# Build context: source-code/  (docker build -f backend/Dockerfile source-code)
#
#   - base image pinned by digest (the same python:3.12-slim the edge image uses);
#   - dependencies are exactly backend/requirements-lock.txt (generated from the service code's imports at the root lock's versions,
#     scripts/build_service_lock.py), installed with --no-deps so nothing outside the closure can arrive;
#   - non-root (uid 10002), no shell login, no build toolchain in the final image, read-only-root-filesystem friendly (no bytecode
#     written, /tmp is the only writable path a workload needs and Kubernetes gives it an emptyDir);
#   - the source tree keeps its repository layout under /srv/repo/source-code, because `backend/observability.py` and others find
#     `contracts/` and `policy/aiops/model/data.json` relative to it;
#   - no secret, no certificate, no model: those are mounted (Kubernetes Secret/ConfigMap/volume) at run time. The fitted detector
#     (`models/registry/ops-anomaly-detector/1.0.0/model.joblib`) is git-ignored and arrives the same way.
#
# No .pyc is compiled at install time (--no-compile): compiled files embed the build moment and would make two clean builds differ.
#
# Version: the image is tagged and labelled with the release version and the git revision by the build script
# (security/supply_chain/build_release.py); the labels below are filled by --build-arg.

FROM python:3.12-slim@sha256:2f17fc044b579bab302c2e8054d3a686e2cb9a83de48e70534b94cd8ebbe06a9

ARG VERSION=0.0.0-dev
ARG REVISION=unknown
LABEL org.opencontainers.image.title="aiops-backend" \
      org.opencontainers.image.description="Intelligent Traffic and Emergency Response Platform - Python services" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${REVISION}" \
      org.opencontainers.image.source="https://github.com/iukhan82/aiops-project-2" \
      org.opencontainers.image.licenses="LicenseRef-academic-prototype"

RUN groupadd --gid 10002 aiops && \
    useradd --uid 10002 --gid aiops --no-create-home --shell /usr/sbin/nologin aiops

WORKDIR /srv/repo/source-code
COPY --chmod=0644 backend/requirements-lock.txt /tmp/requirements-lock.txt
RUN pip install --disable-pip-version-check --no-cache-dir --no-compile --no-deps -r /tmp/requirements-lock.txt && rm /tmp/requirements-lock.txt

COPY backend/ backend/
COPY database/ database/
COPY contracts/ contracts/
COPY models/__init__.py models/__init__.py
COPY models/operations_detector/ models/operations_detector/
COPY models/operations_dataset/__init__.py models/operations_dataset/signals.py models/operations_dataset/load.py models/operations_dataset/
COPY policy/aiops/model/data.json policy/aiops/model/data.json
# The gateway's durable outbox (edge package) and the scenario-control service's replay engine are the only parts of edge/ and simulator/ a service imports.
COPY edge/__init__.py edge/outbox.py edge/
COPY simulator/manifest/replay.py simulator/manifest/replay.py
# The operator API reads the UX inventory (roles, screens, endpoints) that the console is built from: one file, shared, and the API refuses to start without it.
COPY frontend/src/config/inventory.json frontend/src/config/inventory.json
# Source files arrive with whatever mode the checkout gave them: 0777 from a Windows-mounted tree, 0664 from a Linux one. Normalise, so the
# image carries no world-writable or executable source (found when the same inputs gave a different digest on the target host) and two
# machines build the same image. Nothing here is run by its own path: every service is `python3 <script>`.
RUN find /srv/repo -type d -exec chmod 0755 {} + && find /srv/repo -type f -exec chmod 0644 {} +

ENV PYTHONPATH=/srv/repo/source-code \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

USER aiops:aiops
# No default service: the workload names its command (see infra/k8s/workloads.yaml).
ENTRYPOINT ["python3"]
CMD ["-c", "print('aiops-backend: name a service, e.g. backend/api/serve.py --host 0.0.0.0')"]
