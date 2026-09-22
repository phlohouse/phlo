# Build stage
FROM node:24-alpine AS builder

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
FROM node:24-alpine AS runtime

WORKDIR /app

# Required for service discovery/status code paths that run `docker ps`.
# Alpine packages stay unpinned: the branch archive drops superseded versions,
# so an exact version becomes a hard build failure for consumers running
# `phlo services start --build` weeks after the phlo release. The floating base
# tag and `apk upgrade` below already resolve the current branch versions.
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
ENV PORT=3001

# Expose port
EXPOSE 3001

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
  CMD wget --no-verbose --tries=1 --spider http://localhost:3001/ || exit 1

# Run the application
CMD ["node", ".output/server/index.mjs"]
