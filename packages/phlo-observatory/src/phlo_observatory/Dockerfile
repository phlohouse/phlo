# Build stage
FROM node:24-alpine@sha256:ebfe2f90462722a7a4de65e91990e97fe0d401c70e0e762c5b53302f905ec1c1 AS builder

WORKDIR /app

# Copy package files
COPY package*.json ./

# Install dependencies
RUN npm ci

# Copy source files
COPY . .

# Build the application
RUN npm run build

# Production stage
FROM node:24-alpine@sha256:ebfe2f90462722a7a4de65e91990e97fe0d401c70e0e762c5b53302f905ec1c1 AS runtime

WORKDIR /app

# Required for service discovery/status code paths that run `docker ps`.
# Alpine packages stay unpinned: the branch archive drops superseded versions,
# so an exact version becomes a hard build failure for consumers running
# `phlo services start --build` weeks after the phlo release. `apk upgrade`
# below resolves the current branch versions even with a digest-pinned base.
# hadolint ignore=DL3018
RUN apk upgrade --no-cache \
    && apk add --no-cache docker-cli

# Install only the runtime dependency graph. The build-only Vite, Nitro, and
# scaffolding packages remain in the builder stage.
COPY --from=builder /app/package*.json ./
RUN npm ci --omit=dev --ignore-scripts

# Copy built application
COPY --from=builder /app/.output /app/.output

# npm is a build tool; its bundled CLI dependencies are not needed at runtime.
RUN rm -rf /usr/local/lib/node_modules/npm /usr/local/bin/npm /usr/local/bin/npx

# Set environment
ENV NODE_ENV=production
ENV HOST=0.0.0.0
ENV PORT=3000

# Expose port
EXPOSE 3000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
  CMD wget --no-verbose --tries=1 --spider http://127.0.0.1:3000/healthz

# Run the application
CMD ["node", ".output/server/index.mjs"]
