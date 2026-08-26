# FamilyGraph agent sidecar.
# No VOLUME declared: the sidecar mounts nothing (no DB, no uploads).
# Only outbound reach: FastAPI internal network + approved Provider egress.

FROM node:24-slim AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY tsconfig.json tsconfig.build.json ./
COPY src ./src
RUN npm run build

FROM node:24-slim AS runtime
ENV NODE_ENV=production
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --omit=dev && npm cache clean --force
COPY --from=build /app/dist ./dist
USER node
# Health port only; no data ports, no shell tools exposed.
EXPOSE 8080
CMD ["node", "dist/main.js"]
