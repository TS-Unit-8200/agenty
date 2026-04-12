FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential curl \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY agenty ./agenty
COPY agents ./agents

RUN pip install --upgrade pip setuptools wheel \
    && pip install .

RUN mkdir -p /app/logs

EXPOSE 8080

CMD ["uvicorn", "agenty.api.server:app_factory", "--factory", "--host", "0.0.0.0", "--port", "8080"]
