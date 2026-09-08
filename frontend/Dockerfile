# Kenya maize yield PWA — static bundle behind nginx.
#
# Build:  docker build -t maize-pwa frontend
# Run:    docker run -p 8080:8080 -e PORT=8080 maize-pwa
#
# VITE_API_BASE_URL is baked in at build time. On Railway, set it as a service
# variable and it is passed through as a build argument (declared below), so the
# same repository can serve a staging and a production front end.

# ---------------------------------------------------------------------------
# build
# ---------------------------------------------------------------------------
FROM node:20-alpine AS build

WORKDIR /app

# Dependencies first: this layer is cached until the lockfile changes.
COPY package.json package-lock.json* ./
RUN npm ci --no-audit --no-fund || npm install --no-audit --no-fund

COPY . .

ARG VITE_API_BASE_URL=https://web-production-7dae9.up.railway.app
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL
RUN npm run build

# ---------------------------------------------------------------------------
# serve
# ---------------------------------------------------------------------------
FROM nginx:1.27-alpine AS runtime

# The official image renders every *.template in this directory with envsubst at
# startup, which is how $PORT (injected by Railway) reaches the nginx config.
COPY nginx.conf.template /etc/nginx/templates/default.conf.template
COPY --from=build /app/dist /usr/share/nginx/html

ENV PORT=8080 \
    NGINX_ENVSUBST_OUTPUT_DIR=/etc/nginx/conf.d

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD wget -qO- "http://127.0.0.1:${PORT:-8080}/" > /dev/null || exit 1
