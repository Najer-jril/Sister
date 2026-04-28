
---

## 🎯 OBJECTIVE (CLARIFIED)

Bangun sistem distributed berbasis Python (asyncio) dengan 3 node minimum yang mampu:

* Consensus (Raft) → untuk lock coordination
* Data movement (Queue) → consistent hashing + persistence
* State consistency (Cache coherence)
* Fault tolerance → node failure & partition
* Observable → metrics + logging

**Target realistis:**
Bukan “perfect system”, tapi:

* Semua fitur jalan
* Failure scenario bisa didemokan
* Arsitektur jelas & defensible

---

## 🧱 SYSTEM ARCHITECTURE (HIGH LEVEL)

Komponen utama:

1. **Node (Generic Service)**

   * Bisa jadi leader / follower (Raft)
   * Jalankan:

     * Lock Manager
     * Queue Handler
     * Cache Node

2. **Consensus Layer (Raft)**

   * Leader election
   * Log replication
   * Digunakan oleh:

     * Distributed Lock

3. **Communication Layer**

   * HTTP (aiohttp) atau gRPC
   * JSON message passing
   * Heartbeat + failure detection

4. **Redis (Shared infra)**

   * Metadata / backup state
   * Persistence queue (fallback)

---

## 📁 PROJECT STRUCTURE (FINAL)

Gunakan ini sebagai baseline:

```bash
distributed-sync-system/
├── src/
│   ├── nodes/
│   │   ├── base_node.py
│   │   ├── lock_manager.py
│   │   ├── queue_node.py
│   │   └── cache_node.py
│   ├── consensus/
│   │   └── raft.py
│   ├── communication/
│   │   ├── message_passing.py
│   │   └── failure_detector.py
│   └── utils/
│       ├── config.py
│       └── metrics.py
├── docker/
│   ├── Dockerfile.node
│   └── docker-compose.yml
├── tests/
├── benchmarks/
├── docs/
├── README.md
```

---

## ⚙️ IMPLEMENTATION PLAN (STEP-BY-STEP)

### STEP 1 — Base Node (Foundation)

Tujuan: semua node bisa jalan & komunikasi

Implement:

* `BaseNode`

  * node_id
  * peer list
  * REST API (aiohttp)
  * send_message()

Critical endpoints:

```
/heartbeat
/message
/status
```

---

### STEP 2 — Raft Consensus (CORE RISK AREA)

Implement minimal Raft:

State:

* follower
* candidate
* leader

Fitur WAJIB:

* leader election
* heartbeat
* term management

Tidak perlu full log replication kompleks → cukup untuk:
👉 koordinasi lock

File: `raft.py`

Key methods:

```python
start_election()
request_vote()
append_entries()  # heartbeat
become_leader()
```

---

### STEP 3 — Distributed Lock Manager

Gunakan Raft leader sebagai authority.

Logic:

* Client request → node
* Jika bukan leader → redirect ke leader
* Leader:

  * grant lock
  * broadcast ke followers

Support:

* shared lock
* exclusive lock

Deadlock detection (simple, jangan overengineering):

* timeout-based detection
* graph optional (kalau sempat)

---

### STEP 4 — Distributed Queue (Consistent Hashing)

Core idea:

* message → hash(key) → node

Implement:

* hash ring
* multiple producers / consumers

Features:

* enqueue
* dequeue
* replication (minimal 1 backup node)
* persistence:

  * Redis OR local file (simplify)

Delivery:

* at-least-once:

  * ack system
  * retry if no ack

---

### STEP 5 — Cache Coherence (Simplify but Valid)

Jangan terlalu ambisius. Pilih:
👉 **MESI (simplified)**

State:

* Modified
* Exclusive
* Shared
* Invalid

Mechanism:

* write → invalidate other nodes
* read → sync if needed

Tambahkan:

* LRU cache (wajib untuk poin)
* metrics:

  * hit/miss
  * latency

---

### STEP 6 — Failure Handling (WAJIB DEMO)

Minimal implement:

* node down
* leader failure
* network partition (simulasi)

Gunakan:

* heartbeat timeout
* re-election

---

### STEP 7 — Dockerization

Dockerfile:

* Python + dependencies
* expose port

docker-compose:

* 3 nodes
* 1 Redis

Support scaling:

```bash
docker-compose up --scale node=5
```

---

### STEP 8 — Metrics & Monitoring

Implement:

* latency
* throughput
* error rate

Output:

* log JSON
* optional: Prometheus

---

### STEP 9 — Testing Strategy

Jangan skip ini (ini yang bikin nilai naik):

1. Unit test:
   * raft election
   * lock acquire/release

2. Integration:
   * 3 nodes communication

3. Performance:
   * load test (locust)

---

### STEP 10 — Benchmark & Analysis

Test scenario:

1. Single node vs distributed
2. Node failure
3. High load queue

Metrics:

* latency
* throughput
* recovery time

---

## 🎥 VIDEO STRATEGY (IMPORTANT)

Urutan demo:

1. Jalankan 3 node (Docker)
2. Tunjukkan:

   * leader election
3. Lock demo:

   * 2 client → conflict
4. Queue demo:

   * producer-consumer
5. Kill 1 node:

   * system tetap jalan
6. Cache demo:

   * invalidation

---

## ⚠️ REAL RISKS (YOU'RE LIKELY UNDERESTIMATING)

1. **Raft implementation**
   → ini bottleneck utama
   → kalau gagal, semuanya collapse

2. **Distributed debugging**
   → tanpa logging = kamu buta

3. **Time constraint (5 jam itu ilusi)**
   → realistis: 12–20 jam

---

## 🎯 PRIORITY ORDER (NON-NEGOTIABLE)

1. Raft (basic, working)
2. Lock Manager (pakai Raft)
3. Queue (simplified but works)
4. Failure demo
5. Cache (last, jangan overbuild)

---