# Phlo Observatory web application.
#
# Three stages: dependencies install from the lockfile (npm ci, no upgrades),
# the build stage compiles the TanStack Start client + server bundles, and the
# runtime stage carries only production dependencies and the built output. The
# process entrypoint is `serve.mjs` — a srvx Node server that runs the compiled
# fetch handler, serves the client assets, and proxies /api/observatory to
# phlo-api inside the deployment network.

FROM node:22-slim AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

FROM node:22-slim AS build
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY package.json package-lock.json tsconfig.json vite.config.ts components.json ./
COPY src ./src
RUN npm run build

FROM node:22-slim AS runtime-deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --omit=dev

FROM node:22-slim
WORKDIR /app
ENV NODE_ENV=production HOST=0.0.0.0 PORT=3000
COPY --from=runtime-deps /app/node_modules ./node_modules
COPY --from=build /app/dist ./dist
COPY package.json serve.mjs ./
EXPOSE 3000
CMD ["node", "serve.mjs"]
