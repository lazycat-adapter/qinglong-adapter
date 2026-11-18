# syntax=docker/dockerfile:1
ARG BUILD_IMAGE=docker.io/library/node:22-bookworm
ARG RUNTIME_IMAGE=docker.io/whyour/qinglong:2.24.0
FROM ${BUILD_IMAGE} AS compile
WORKDIR /build
ARG PNPM_VERSION
ENV CI=true
ENV NODE_OPTIONS=--max-old-space-size=3000
COPY src/ ./
RUN --mount=type=cache,id=qinglong-adapter-pnpm,target=/root/.local/share/pnpm/store,sharing=locked \
    npm install --global "pnpm@${PNPM_VERSION}" \
    && pnpm install --frozen-lockfile --network-concurrency=5 --child-concurrency=2
RUN pnpm build:front && pnpm build:back && pnpm build:info \
    && test -s static/dist/index.html && test -s static/build/app.js

FROM ${RUNTIME_IMAGE}
ARG SOURCE_REVISION
ARG SOURCE_VERSION
ARG RECIPE
LABEL org.opencontainers.image.source="https://github.com/whyour/qinglong" \
    org.opencontainers.image.revision="${SOURCE_REVISION}" \
    org.opencontainers.image.version="${SOURCE_VERSION}" \
    cloud.lazycat.qinglong.recipe="${RECIPE}"
# Drop previous hashed bundles before adding the paired builds. The runtime
# base has the exact selected source tree and its native production dependencies.
RUN rm -rf /ql/static
COPY --from=compile /build/static/ /ql/static/
COPY --from=compile /build/src/pages/initialization/index.tsx /ql/src/pages/initialization/index.tsx
COPY --from=compile /build/src/pages/login/index.tsx /ql/src/pages/login/index.tsx
COPY --from=compile /build/src/pages/setting/security.tsx /ql/src/pages/setting/security.tsx
COPY --from=compile /build/src/utils/http.tsx /ql/src/utils/http.tsx
