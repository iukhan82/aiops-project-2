# P11.01: the operations console as static files served by an unprivileged nginx.
#
# Build context: source-code/frontend/  (docker build -t aiops-frontend frontend)
#
#   - both base images pinned by digest; the Node build stage is discarded, only the built files reach the final image;
#   - `npm ci --ignore-scripts` from package-lock.json (exact versions, no install-time script runs);
#   - the final image runs as nginx's own unprivileged user (uid 101) on port 8080, serves only /usr/share/nginx/html, needs a
#     read-only root file system with tmpfs for /tmp and /var/cache/nginx (Kubernetes gives it emptyDirs), and carries no shell
#     the console needs;
#   - `/api/` and `/scenario-control/` are proxied to the operator API and scenario-control services by name, exactly as Vite's dev
#     proxy does, so the browser only ever talks to one origin (no CORS) and the Bearer token goes cross-origin only to Keycloak.

FROM node:22-alpine@sha256:0a7108bf6c7bf5de370ffb1a3ed6be93d405b43ff159f681a8d18c0e2bc2e402 AS build
WORKDIR /build
COPY package.json package-lock.json ./
RUN npm ci --ignore-scripts
COPY index.html tsconfig.json vite.config.ts ./
COPY src src
ARG VITE_KEYCLOAK_URL=http://localhost:8180
ENV VITE_KEYCLOAK_URL=${VITE_KEYCLOAK_URL}
RUN npm run build
# The Content-Security-Policy names the identity provider's origin; it is fixed at build time (no run-time templating, so the root file system can stay read-only).
COPY security-headers.conf security-headers.conf.in
RUN sed "s#__KEYCLOAK_ORIGIN__#${VITE_KEYCLOAK_URL}#g" security-headers.conf.in > security-headers.conf

# nginx-unprivileged stable-alpine (nginx 1.30.5, Alpine 3.24.2), pinned by digest on 2026-09-25 - the previous pin carried 38 fixable HIGH findings.
FROM nginxinc/nginx-unprivileged@sha256:4714e0b1b2577eaa1a6131d07c958b67f0eb68e6d0521e90c6e5287db8cf0bc5

# The pinned base still ships libexpat 2.8.4, which Trivy reports HIGH with a fix in 2.8.5 (Alpine has it, the image does not yet). Take the fixed
# package from the distribution; drop this step when the pinned base carries it. `>=` so a later Alpine release does not break the build.
# apk timestamps every line of its own log: drop it, or two builds of the same inputs give two different images.
USER root
RUN apk add --no-cache "libexpat>=2.8.5-r0" && rm -f /var/log/apk.log
USER 101

ARG VERSION=0.0.0-dev
ARG REVISION=unknown
LABEL org.opencontainers.image.title="aiops-frontend" \
      org.opencontainers.image.description="Intelligent Traffic and Emergency Response Platform - operations console" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${REVISION}" \
      org.opencontainers.image.source="https://github.com/iukhan82/aiops-project-2" \
      org.opencontainers.image.licenses="LicenseRef-academic-prototype"

COPY --chmod=0644 nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /build/security-headers.conf /etc/nginx/security-headers.conf
COPY --from=build /build/dist /usr/share/nginx/html
EXPOSE 8080
