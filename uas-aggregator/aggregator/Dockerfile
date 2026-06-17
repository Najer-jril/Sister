FROM python:3.11-slim

WORKDIR /app

RUN adduser --disabled-password --gecos '' --uid 1001 appuser

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev curl \
    && rm -rf /var/lib/apt/lists/*

COPY aggregator/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY aggregator/src/ ./src/
COPY tests/ ./tests/
COPY pytest.ini ./pytest.ini

RUN chown -R appuser:appuser /app
USER appuser

EXPOSE 8080

HEALTHCHECK --interval=10s --timeout=5s --retries=5 \
    CMD curl -f http://localhost:8080/health || exit 1

CMD ["python", "-m", "uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]
