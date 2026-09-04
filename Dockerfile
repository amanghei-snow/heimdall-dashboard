# ── Stage 1: Build React frontend ──────────────────────────
FROM node:22-slim AS frontend-build
WORKDIR /app/dashboard/frontend
COPY dashboard/frontend/package.json dashboard/frontend/package-lock.json* ./
RUN npm ci --ignore-scripts 2>/dev/null || npm install
COPY dashboard/frontend/ ./
RUN npm run build

# ── Stage 2: Python backend + static frontend ─────────────
FROM python:3.12-slim
WORKDIR /app

# System deps for psycopg2
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev gcc && \
    rm -rf /var/lib/apt/lists/*

# Python deps
COPY requirements-deploy.txt ./
RUN pip install --no-cache-dir -r requirements-deploy.txt

# Copy backend code
COPY dashboard/ ./dashboard/

# Create data dirs (populated at runtime via migrate)
RUN mkdir -p output data

# Copy built frontend into the path FastAPI expects
COPY --from=frontend-build /app/dashboard/frontend/dist ./dashboard/frontend/dist

# Expose port (Render uses PORT env var, default 8080 for local)
EXPOSE 8080

# Start — use $PORT if set (Render), else 8080
CMD uvicorn dashboard.backend.app:app --host 0.0.0.0 --port ${PORT:-8080}
