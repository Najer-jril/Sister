# CLI Demo

## SEGMEN 1 — Arsitektur

```bash
cat docker-compose.yml
```
```bash
grep -A 30 "SCHEMA_SQL" aggregator/src/database.py
```
```bash
cat aggregator/src/consumer/worker.py
```

---

## SEGMEN 2 — Build & Compose Up

```bash
docker compose down --volumes 2>/dev/null; docker rmi uas-aggregator:latest uas-publisher:latest 2>/dev/null; echo "Clean"
```
```bash
docker compose up --build
```
```bash
docker compose up -d
```

---

## SEGMEN 3 — Health, Stats, Events

```bash
curl -s http://localhost:8080/health | python3 -m json.tool
```
```bash
curl -s http://localhost:8080/stats | python3 -m json.tool
```
```bash
curl -s "http://localhost:8080/events?limit=5" | python3 -m json.tool
```
```bash
curl -s "http://localhost:8080/events?topic=auth&limit=3" | python3 -m json.tool
```

---

## SEGMEN 4 — Idempotency & Dedup

```bash
curl -s http://localhost:8080/stats | python3 -m json.tool
```

```bash
curl -s -X POST http://localhost:8080/publish \
  -H "Content-Type: application/json" \
  -d '{"topic":"demo-dedup","event_id":"demo-evt-001","timestamp":"2024-06-16T10:00:00","source":"live-demo","payload":{"msg":"event pertama"}}' \
  | python3 -m json.tool
```

```bash
sleep 1 && curl -s http://localhost:8080/stats | python3 -m json.tool
```

```bash
for i in 1 2 3; do
  curl -s -X POST http://localhost:8080/publish \
    -H "Content-Type: application/json" \
    -d '{"topic":"demo-dedup","event_id":"demo-evt-001","timestamp":"2024-06-16T10:00:00","source":"live-demo","payload":{"msg":"duplikat ke-'$i'"}}'; echo ""
done
```

```bash
sleep 1 && curl -s http://localhost:8080/stats | python3 -m json.tool
```

```bash
curl -s -X POST http://localhost:8080/publish \
  -H "Content-Type: application/json" \
  -d '[
    {"topic":"demo-batch","event_id":"batch-001","timestamp":"2024-06-16T11:00:00","source":"demo","payload":{"n":1}},
    {"topic":"demo-batch","event_id":"batch-002","timestamp":"2024-06-16T11:01:00","source":"demo","payload":{"n":2}},
    {"topic":"demo-batch","event_id":"batch-001","timestamp":"2024-06-16T11:02:00","source":"demo","payload":{"n":1}},
    {"topic":"demo-batch","event_id":"batch-003","timestamp":"2024-06-16T11:03:00","source":"demo","payload":{"n":3}},
    {"topic":"demo-batch","event_id":"batch-002","timestamp":"2024-06-16T11:04:00","source":"demo","payload":{"n":2}}
  ]' | python3 -m json.tool
```

```bash
sleep 1 && curl -s "http://localhost:8080/events?topic=demo-batch" | python3 -m json.tool
```

---

## SEGMEN 5 — Konkurensi Multi-Worker

```bash
# TAB-B — live log
docker compose logs -f aggregator | grep -E "(worker=|result=)"
```
```bash
# TAB-A — 20 request paralel, event_id identik
for i in $(seq 1 20); do
  curl -s -X POST http://localhost:8080/publish \
    -H "Content-Type: application/json" \
    -d '{"topic":"concurrent-test","event_id":"race-evt-999","timestamp":"2024-06-16T12:00:00","source":"race-demo","payload":{"test":"concurrent"}}' &
done
wait && echo "Semua 20 request selesai"
```
```bash
sleep 1 && curl -s http://localhost:8080/stats | python3 -m json.tool
```
```bash

docker compose exec aggregator pytest tests/test_transactions.py tests/test_dedup.py -v --tb=short

```

---

## SEGMEN 6 — Crash Recovery & Persistensi

```bash
curl -s http://localhost:8080/stats | python3 -m json.tool
```
```bash
docker compose kill storage && docker compose ps
```
```bash
curl -s http://localhost:8080/health | python3 -m json.tool
```
```bash
docker compose up -d storage
```
```bash
until curl -s http://localhost:8080/health | grep -q '"status":"ok"'; do echo "waiting..."; sleep 2; done && echo "Storage is back"

```
```bash
curl -s http://localhost:8080/stats | python3 -m json.tool
```
```bash
docker compose down
```
```bash
docker compose up -d storage broker aggregator
```
```bash
until curl -s http://localhost:8080/health | grep -q '"database": true'; do sleep 2; done && curl -s http://localhost:8080/stats | python3 -m json.tool
```

---

## SEGMEN 7 — Jaringan & Observability

```bash
docker compose port storage 5432 2>&1 || echo "Port 5432 tidak terekspos ke host"
```
```bash
docker compose port aggregator 8080
```
```bash
docker network inspect uas-aggregator_internal --format '{{range .Containers}}{{.Name}} {{.IPv4Address}}{{"\n"}}{{end}}'
```
```bash
# TAB-B
docker compose logs aggregator --tail=20
```
```bash
for i in $(seq 1 5); do
  curl -s -X POST http://localhost:8080/publish \
    -H "Content-Type: application/json" \
    -d '{"topic":"obs-test","event_id":"obs-'$i'","timestamp":"2024-06-16T13:0'$i':00","source":"obs","payload":{"i":'$i'}}' &
done
wait && sleep 1 && curl -s http://localhost:8080/stats | python3 -m json.tool
```

---

## SEGMEN 8 — Ringkasan Desain

```bash
cat README.md | grep -A 20 "Architecture Decisions"
```

---

## Darurat

```bash
# Reset total
docker compose down --volumes && docker compose up --build
```
```bash
docker compose ps
```
```bash
docker compose logs aggregator --tail=30
```
```bash
lsof -i :8080 | grep LISTEN
```
```bash
docker compose exec broker redis-cli FLUSHALL
```
```bash
docker compose exec storage psql -U user -d aggregatordb -c "SELECT * FROM stats;"
```
```bash
docker compose exec storage psql -U user -d aggregatordb -c "SELECT topic, COUNT(*) FROM events GROUP BY topic ORDER BY topic;"
```
