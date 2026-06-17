# UAS Distributed Systems — Pub-Sub Log Aggregator

Distributed event aggregator with Redis pub/sub queue, PostgreSQL deduplication, and multi-worker async consumers.

## Quick Start

```bash
docker compose up --build
```

## Services

| Service     | Port           | Description                          |
|-------------|----------------|--------------------------------------|
| aggregator  | 8080           | FastAPI + N consumer workers         |
| broker      | internal only  | Redis 7 — event queue                |
| storage     | internal only  | PostgreSQL 16 — persistent store     |
| publisher   | internal only  | Event simulator (runs once, exits)   |

## API Endpoints

| Method | Path                      | Description                      |
|--------|---------------------------|----------------------------------|
| POST   | /publish                  | Single or batch event (async)    |
| GET    | /events?topic=X           | Processed unique events          |
| GET    | /stats                    | Counters + uptime                |
| GET    | /health                   | Liveness / readiness probe       |

## Run Tests

```bash
docker compose exec aggregator pytest /app/tests -v

DATABASE_URL=postgresql://user:pass@localhost:5432/aggregatordb \
REDIS_URL=redis://localhost:6379 \
pytest tests/ -v
```

## Persistence Proof

```bash
docker compose up --build -d
docker compose down           
docker compose up -d storage broker aggregator
curl http://localhost:8080/stats
```

## Architecture Decisions

| Decision | Choice | Reason |
|---|---|---|
| Dedup mechanism | Postgres `UNIQUE (topic, event_id)` | Atomic, persistent, works across N workers |
| Transaction isolation | READ COMMITTED (Postgres default) | UNIQUE constraint handles concurrency; SERIALIZABLE overhead not needed |
| Stats updates | SQL arithmetic `count = count + 1` | Prevents lost-update without OCC/pessimistic locks |
| Worker model | N asyncio tasks + Redis BLPOP | Simple, backpressure-safe, horizontally scalable |
| Network | `internal: true` bridge | Zero external egress — matches spec requirement |
| Volumes | Named `uas_pg_data`, `uas_broker_data` | Survive `docker compose down` without `--volumes` |

## Configuration

See `.env.example` for all environment variables and defaults.

## Report

See `./report/report.md` for the report

## Lampiran

link video youtube : https://youtu.be/zr6xEGfg7Kw
link repo github : 