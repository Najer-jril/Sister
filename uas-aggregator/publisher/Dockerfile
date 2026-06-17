FROM python:3.11-slim

WORKDIR /app

RUN adduser --disabled-password --gecos '' --uid 1001 appuser

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/

RUN chown -R appuser:appuser /app
USER appuser

CMD ["python", "-m", "src.main"]
