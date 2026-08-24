# Single-service deploy: builds the frontend, then serves it and the API
# out of one FastAPI process (backend/api.py mounts frontend/dist as static
# files). See README.md's "Deploying to Railway" section.

FROM node:20-slim AS frontend-build
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.11-slim AS backend
WORKDIR /app

COPY pyproject.toml README.md ./
COPY backend/ ./backend/
COPY ingestion/ ./ingestion/
RUN pip install --no-cache-dir .

COPY --from=frontend-build /app/frontend/dist ./frontend/dist

ENV PYTHONUNBUFFERED=1
EXPOSE 8000

# Shell form so $PORT (Railway sets this at runtime) actually expands.
CMD uvicorn backend.api:app --host 0.0.0.0 --port ${PORT:-8000}