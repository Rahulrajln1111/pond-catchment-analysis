# ---- Frontend build stage ----
FROM node:20-alpine AS frontend
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install
COPY frontend/ ./
RUN npm run build

# ---- Backend runtime stage ----
FROM python:3.12-slim
WORKDIR /srv

# Install Python dependencies first (better layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy backend + built frontend
COPY app/ ./app/
COPY --from=frontend /build/dist ./frontend/dist

# Terrain tile cache location
ENV TERRAIN_CACHE_DIR=/tmp/terrain_cache
ENV FRONTEND_DIST=/srv/frontend/dist

EXPOSE 4289
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "4289", "--workers", "2"]
