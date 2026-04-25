# Pub-Sub Log Aggregator dengan Idempotent Consumer & Deduplication

## Project Overview

Layanan agregasi log berbasis Python (FastAPI + Asyncio) yang berjalan di dalam kontainer Docker. Sistem ini menjamin konsistensi data menggunakan mekanisme deduplikasi berbasis SQLite untuk menangani skenario *at-least-once delivery*.

## Key Features

- **Idempotency:** Menghindari pemrosesan duplikat dari pesan log yang sama.
- **Durable Dedup Store (SQLite):** Menggunakan basis data SQLite yang ringan untuk melacak histori UUID pesan dan mengidentifikasi duplikasi bahkan saat terjadi *restart*.
- **At-least-once Simulation:** Mampu memproses event dari publisher secara berulang dengan aman tanpa menghasilkan *duplicate processing/output*.
- **Observability (Stats):** Dilengkapi metrik pengamatan sistem (seperti jumlah event yang *received*, *unique*, *dropped*, dan *uptime* sistem).

## Prerequisites

Untuk menjalankan proyek ini, Anda memerlukan:
- Docker
- Docker Compose

## Quick Start

Anda dapat menjalankan sistem ini menggunakan metode Docker standar atau menggunakan Docker Compose (opsional).

**Menggunakan Docker:**
1. Build image kontainer:
   ```bash
   docker build -t uts-aggregator . --no-cache
   ```
2. Jalankan aplikasi:
   ```bash
   docker run -p 8080:8080 \
    --name aggregator \
    -v $(pwd)/data:/app/data \
    -e DEDUP_DB_PATH=/app/data/dedup.db \
    uts-aggregator
   ```

**Menggunakan Docker Compose:**
1. Jalankan layanan:
   ```bash
   docker-compose up --build -d
   ```
   Layanan akan otomatis berjalan di port konfigurasi (umumnya `8080`).

## API Endpoints

| Method | Endpoint | Description | JSON Schema / Parameters |
|:---|:---|:---|:---|
| **POST** | `/publish` | Mengirim event log baru (bisa *single message* atau *batching*). | `{ "event_id": "uuid", "topic": "string", "message": "string" }` |
| **GET** | `/events` | Mengambil event log yang valid dan unik untuk suatu topik. | `?topic=nama_topik` |
| **GET** | `/stats` | Mengamati metrik operasi sistem (*received, unique, dropped, uptime*, dll). | *None* |

## Project Structure

```text
uts-aggregator/
├── data/
├── docker-compose.yml
├── Dockerfile
├── pytest.ini
├── README.md
├── requirements.txt
├── scripts/
│   └── stress_test.py
├── src/
│   ├── __init__.py
│   ├── dedup_store.py
│   ├── main.py
│   ├── models.py
│   ├── queue_manager.py
│   └── stats.py
└── tests/
    ├── __init__.py
    └── test_aggregator.py
```

## Running Tests

Proyek ini menggunakan **Pytest** untuk *testing*. Anda dapat menjalankan tes baik di lingkungan lokal (dengan Python virtual env) maupun di dalam kontainer Docker.

- **Menjalankan pytest di luar kontainer (pastikan requirements terinstall):**
  ```bash
  pytest tests/
  ```
- **Menjalankan pytest di dalam kontainer (contoh bila menggunakan compose):**
  ```bash
  docker-compose run --rm app pytest
  ```

## Assumptions & Design Decisions

- **Mengapa menggunakan SQLite untuk persistensi?**
  SQLite digunakan sebagai *Durable Dedup Store* karena ringan, tidak memerlukan pengaturan server eksternal, dan langsung tersimpan di disk (*file-based*). Hal ini membuat mekanisme pencegahan duplikasi sangat andal di antara fase *restart* aplikasi, sangat pas untuk melacak ID (UUID) *collision-resistant* mendeteksi pesan duplikat dengan *throughput* yang memadai.

- **Penanganan Ordering (Partial Ordering vs Total Ordering):**
  Dalam sistem asinkron, menjamin urutan total (*Total Ordering*) sangat mahal dan rentan terhadap penundaan karena masalah sinkronisasi jam global. Oleh karena itu, arsitektur ini lebih bertumpu pada *Partial Ordering*, yang memelihara urutan *event* per topik yang dikontrol langsung berdasarkan histori *queue_manager*. Sistem difokuskan pada keandalan pesan masuk (terjamin *idempotency* dengan deduplikasi) ketimbang jaminan mutlak soal waktu eksekusi global dari produsen yang saling berlainan.



/ 1 create
docker build -t uts-aggregator . --no-cache

/ 2 run
docker run -p 8080:8080 \
  --name aggregator \
  -v $(pwd)/data:/app/data \
  -e DEDUP_DB_PATH=/app/data/dedup.db \
  uts-aggregator


/ 3 sent 1 publish
curl -X POST http://localhost:8080/publish \
  -H "Content-Type: application/json" \
  -d '{
    "topic": "najerisme",
    "event_id": "evt-001",
    "timestamp": "2024-01-01T00:00:00",
    "source": "app",
    "payload": {"msg": "hello"}
  }'


/ 4 sent dupe
curl -X POST http://localhost:8080/publish \
  -H "Content-Type: application/json" \
  -d '{"topic":"najerisme","event_id":"evt-001","timestamp":"2024-01-01T00:00:00","source":"app","payload":{}}'

curl -X POST http://localhost:8080/publish \
  -H "Content-Type: application/json" \
  -d '{"topic":"najerisme","event_id":"evt-001","timestamp":"2024-01-01T00:00:00","source":"app","payload":{}}'


/ test-aggregator
pytest tests/ -v

/ stress-test
python scripts/stress_test.py

/ persisten
docker stop aggregator
docker rm aggregator

docker run -p 8080:8080 \
  --name aggregator \
  -v $(pwd)/data:/app/data \
  -e DEDUP_DB_PATH=/app/data/dedup.db \
  uts-aggregator

curl -X POST http://localhost:8080/publish \
  -H "Content-Type: application/json" \
  -d '{
    "topic": "najerisme",
    "event_id": "evt-001",
    "timestamp": "2024-01-01T00:00:00",
    "source": "app",
    "payload": {"msg": "hello"}
  }'