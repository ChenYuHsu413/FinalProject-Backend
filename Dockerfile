# AI SERVO PLATFORM backend — api image
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv/app

# Install runtime dependencies first (better layer caching).
COPY pyproject.toml README.md ./
COPY app ./app
RUN pip install --no-cache-dir .

# Non-root runtime user.
RUN useradd --create-home --uid 10001 appuser
USER appuser

EXPOSE 8000

# Overridden by docker-compose for dev (reload) / prod hardening.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
