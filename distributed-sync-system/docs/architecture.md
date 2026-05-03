# Architecture Documentation
## Distributed Synchronization System

---

## 1. System Overview

Sistem ini dibangun menggunakan arsitektur multi-cluster yang memisahkan setiap komponen utama ke dalam klaster node tersendiri. Terdapat tiga klaster aplikasi — Lock Cluster, Queue Cluster, dan Cache Cluster — masing-masing terdiri dari 3 node yang berjalan dalam container Docker terpisah dengan Dockerfile masing-masing. Pemisahan ini memastikan setiap komponen dapat di-scale secara independen dan kegagalan satu klaster tidak mempengaruhi klaster lainnya.

Keseluruhan sistem terdiri dari 12 container yang terhubung melalui Docker bridge network bernama `distributed_net`. Redis berperan sebagai backing store bersama untuk ketiga klaster. Prometheus mengumpulkan metrics dari semua node, sementara Grafana menyediakan visualisasi dashboard. Setiap permintaan dari client dapat diarahkan ke node manapun dalam klaster yang relevan — sistem akan menangani routing internal secara otomatis.

```mermaid
graph TB
    Client([Client / curl]) --> LC & QC & CC

    subgraph LC["Lock Cluster — Raft Consensus"]
        L1[lock1 LEADER :8001]
        L2[lock2 FOLLOWER :8002]
        L3[lock3 FOLLOWER :8003]
        L1 <-->|AppendEntries /message| L2
        L2 <-->|AppendEntries /message| L3
        L1 <-->|AppendEntries /message| L3
    end

    subgraph QC["Queue Cluster — Consistent Hashing"]
        Q1[queue1 :8091]
        Q2[queue2 :8092]
        Q3[queue3 :8093]
        Q1 <-->|Forward if not owner| Q2
        Q2 <-->|Forward if not owner| Q3
    end

    subgraph CC["Cache Cluster — MESI Protocol"]
        C1[cache1 Directory :8101]
        C2[cache2 :8102]
        C3[cache3 :8103]
        C1 <-->|INVALIDATE + ACK| C2
        C1 <-->|INVALIDATE + ACK| C3
    end

    R[(Redis :6379)]
    L1 & L2 & L3 -->|Persistent state| R
    Q1 & Q2 & Q3 -->|Queue storage| R
    C1 & C2 & C3 -->|Backing store| R

    P[Prometheus :9090]
    G[Grafana :3000]
    L1 & L2 & L3 -.->|/metrics| P
    Q1 & Q2 & Q3 -.->|/metrics| P
    C1 & C2 & C3 -.->|/metrics| P
    P --> G
```

---

## 2. Stack Teknologi

| Teknologi | Versi | Peran |
|---|---|---|
| Python | 3.11 | Runtime utama semua node |
| asyncio | stdlib | Concurrent I/O tanpa threading |
| aiohttp | 3.9.5 | HTTP server dan client antar node |
| Redis | 7.4 alpine | Persistent state, queue storage, cache backing |
| Docker | latest | Containerisasi setiap komponen |
| Docker Compose | v2 | Orchestration 12 container |
| Prometheus | latest | Metrics collection |
| Grafana | latest | Metrics visualization |

---

## 3. Raft Consensus — Lock Cluster

### 3.1 Gambaran Algoritma

Raft adalah algoritma konsensus yang dirancang untuk mudah dipahami. Setiap node memiliki satu dari tiga peran: FOLLOWER, CANDIDATE, atau LEADER. Pada saat startup semua node berstatus FOLLOWER. Apabila seorang FOLLOWER tidak menerima heartbeat dari LEADER dalam rentang waktu election timeout (150–300ms acak), ia bertransisi menjadi CANDIDATE, menginkremen term, memberi suara untuk dirinya sendiri, lalu mengirim RequestVote RPC ke semua peer.

Node yang berhasil mengumpulkan suara mayoritas (⌊n/2⌋ + 1) menjadi LEADER untuk term tersebut. LEADER bertanggung jawab menerima semua permintaan write dari client, mereplikasinya ke FOLLOWER melalui AppendEntries RPC, dan hanya mengkomit entry setelah mayoritas node mengkonfirmasi penerimaannya. Apabila LEADER kehilangan koneksi ke mayoritas node, ia secara otomatis mundur menjadi FOLLOWER untuk mencegah split-brain.

### 3.2 Sequence Diagram — Leader Election

```mermaid
sequenceDiagram
    participant lock2 as lock2 (Follower)
    participant lock1 as lock1 (Candidate→Leader)
    participant lock3 as lock3 (Follower)

    lock1->>lock1: Election timeout expires (150-300ms random)
    lock1->>lock1: Increment term, vote for self
    lock1->>lock2: RequestVote(term, candidateId, lastLogIndex, lastLogTerm)
    lock1->>lock3: RequestVote(term, candidateId, lastLogIndex, lastLogTerm)
    lock2-->>lock1: VoteGranted: true
    lock3-->>lock1: VoteGranted: true
    lock1->>lock1: Majority reached → become LEADER
    loop Every 50ms
        lock1->>lock2: AppendEntries (heartbeat)
        lock1->>lock3: AppendEntries (heartbeat)
        lock2-->>lock1: success: true
        lock3-->>lock1: success: true
    end
```

### 3.3 Sequence Diagram — Lock Acquisition

```mermaid
sequenceDiagram
    participant Client
    participant lock1 as lock1 (LEADER :8001)
    participant lock2 as lock2 (FOLLOWER :8002)
    participant lock3 as lock3 (FOLLOWER :8003)
    participant Redis

    Client->>lock1: POST /locks/acquire {resource_id, lock_type, holder_id}
    lock1->>Redis: HGET locks resource_id
    Redis-->>lock1: null (available)
    lock1->>lock1: Append ACQUIRE_LOCK to Raft log
    lock1->>lock2: AppendEntries(ACQUIRE_LOCK command)
    lock1->>lock3: AppendEntries(ACQUIRE_LOCK command)
    lock2-->>lock1: matchIndex updated
    lock3-->>lock1: matchIndex updated
    lock1->>lock1: Majority confirmed → commit
    lock1->>Redis: HSET locks resource_id LockState
    lock1-->>Client: 200 OK {success: true, lock_id: "uuid"}

    Note over Client,lock2: Request ke non-leader:
    Client->>lock2: POST /locks/acquire
    lock2-->>Client: 503 {error: "Not Raft Leader", leader_id: "lock1"}
```

### 3.4 Network Partition Behavior

Apabila lock1 (LEADER) dimatikan, lock2 dan lock3 akan memulai election baru. Namun dengan hanya 2 node tersisa, terjadi fenomena **split vote** — setiap node membutuhkan 2 suara dari total 2 node aktif, tetapi karena election timeout yang berbeda mereka sering melewatkan jendela election satu sama lain. Inilah alasan Raft memerlukan jumlah node ganjil: dengan 3 node, saat 1 mati, 2 yang tersisa masih dapat membentuk majority (2 dari 3).

---

## 4. Queue Cluster — Consistent Hashing

### 4.1 Gambaran Algoritma

Consistent Hashing Ring membagi ruang hash menjadi cincin virtual. Setiap node ditempatkan di beberapa titik pada cincin (virtual nodes, weight=100) untuk distribusi yang merata. Ketika sebuah pesan diproduksi untuk queue tertentu, nama queue di-hash menggunakan MD5 dan dipetakan ke node yang bertanggung jawab (owner). Jika node yang menerima request bukan owner, request diteruskan secara otomatis ke node yang tepat.

### 4.2 Sequence Diagram — Produce dengan Forwarding

```mermaid
sequenceDiagram
    participant Client
    participant queue1 as queue1 (:8091)
    participant queue3 as queue3 (:8093)
    participant Redis

    Client->>queue1: POST /queues/orders/messages {payload}
    queue1->>queue1: hash("orders") → queue3 is owner
    queue1->>queue3: Forward POST /queues/orders/messages
    queue3->>Redis: LPUSH queue:orders {message_json}
    queue3-->>queue1: 201 {message_id: "uuid"}
    queue1-->>Client: 201 {message_id: "uuid"}
```

### 4.3 Sequence Diagram — Consume + ACK (At-Least-Once)

```mermaid
sequenceDiagram
    participant Client
    participant queue1 as queue1 (:8091)
    participant Redis

    Client->>queue1: GET /queues/orders/messages/next?consumer_id=worker-1
    queue1->>Redis: BRPOP queue:orders timeout=5
    Redis-->>queue1: raw message JSON
    queue1->>Redis: HSET inflight:orders {message_id: data}
    queue1-->>Client: 200 {message_id, payload, delivery_count}

    alt ACK dalam 30 detik
        Client->>queue1: POST /queues/messages/{id}/ack
        queue1->>Redis: HDEL inflight:orders message_id
        queue1-->>Client: 200 {status: acknowledged}
    else ACK timeout
        queue1->>queue1: _ack_timeout_loop detects timeout
        queue1->>Redis: HDEL inflight:orders message_id
        alt delivery_count < 3
            queue1->>Redis: RPUSH queue:orders message
            Note over queue1: Requeued for retry
        else max_deliveries exceeded
            queue1->>Redis: RPUSH dlq:orders message
            Note over queue1: Moved to Dead Letter Queue
        end
    end
```

---

## 5. Cache Cluster — MESI Protocol

### 5.1 Gambaran Protokol

MESI (Modified, Exclusive, Shared, Invalid) adalah protokol cache coherence yang memastikan konsistensi data di antara beberapa cache node. Setiap cache line memiliki salah satu dari empat state:

- **Modified**: Node ini memiliki data terbaru, belum ditulis ke Redis (dirty). Tidak ada node lain yang punya salinan.
- **Exclusive**: Node ini memiliki data bersih yang sama dengan Redis. Tidak ada node lain yang punya salinan.
- **Shared**: Beberapa node memiliki salinan bersih yang sama. Tidak ada yang boleh menulis tanpa broadcasting INVALIDATE terlebih dahulu.
- **Invalid**: Data tidak valid, harus di-fetch ulang sebelum digunakan.

`cache1` berperan sebagai `DirectoryController` — pusat koordinasi yang melacak state dan daftar sharer untuk setiap key.

### 5.2 State Machine

```mermaid
stateDiagram-v2
    [*] --> INVALID
    INVALID --> EXCLUSIVE : Read miss, tidak ada salinan lain
    INVALID --> SHARED : Read miss, salinan sudah ada di node lain
    EXCLUSIVE --> MODIFIED : Local write (silent upgrade)
    EXCLUSIVE --> SHARED : Node lain membaca key yang sama
    SHARED --> MODIFIED : Local write (broadcast INVALIDATE dulu)
    SHARED --> INVALID : Terima INVALIDATE dari directory
    MODIFIED --> SHARED : Node lain baca (write-back dulu ke Redis)
    MODIFIED --> INVALID : Node lain tulis (write-back, lalu invalidate)
```

### 5.3 Sequence Diagram — Write dengan INVALIDATE Broadcast

```mermaid
sequenceDiagram
    participant Client
    participant cache2 as cache2 (:8102)
    participant cache1 as cache1 (Directory :8101)
    participant cache3 as cache3 (:8103)
    participant Redis

    Client->>cache2: PUT /cache/user:100 {value: "new_data"}
    cache2->>cache1: POST /dir/write {key, node_id: cache2}
    cache1->>cache1: sharers: [cache1, cache3]
    cache1-->>cache2: {action: INVALIDATE, sharers: [cache1, cache3]}
    cache2->>cache1: POST /internal/invalidate {key, requester: cache2}
    cache2->>cache3: POST /internal/invalidate {key, requester: cache2}
    cache1->>cache1: Set local state → INVALID
    cache3->>cache3: Set local state → INVALID
    cache1-->>cache2: POST /internal/invalidate_ack
    cache3-->>cache2: POST /internal/invalidate_ack
    cache2->>cache2: All ACKs received (asyncio.Event.set())
    cache2->>Redis: SET cache:user:100 "new_data"
    cache2->>cache2: Local state → MODIFIED
    cache2-->>Client: 200 {status: written}
```

### 5.4 Sequence Diagram — Read Miss → SHARED

```mermaid
sequenceDiagram
    participant Client
    participant cache2 as cache2 (:8102)
    participant cache1 as cache1 (Directory :8101)
    participant Redis

    Client->>cache2: GET /cache/user:100
    cache2->>cache2: LRU cache miss (state: INVALID)
    cache2->>cache1: POST /dir/read {key, node_id: cache2}
    cache1-->>cache2: {action: FETCH_FROM_DB, state: EXCLUSIVE}
    cache2->>Redis: GET cache:user:100
    Redis-->>cache2: "najer_profile_data"
    cache2->>cache2: Store in LRU, state → EXCLUSIVE
    cache2-->>Client: 200 {value: "najer_profile_data", cache_hit: false}

    Note over Client,cache2: Request berikutnya ke cache2:
    Client->>cache2: GET /cache/user:100
    cache2->>cache2: LRU cache hit (state: EXCLUSIVE)
    cache2-->>Client: 200 {value: "najer_profile_data", cache_hit: true}
```

---

## 6. Containerization

### 6.1 Dockerfile per Komponen

Terdapat tiga Dockerfile terpisah, masing-masing dengan ENTRYPOINT yang berbeda:

**Dockerfile.lock**
```dockerfile
ENTRYPOINT ["python", "-m", "src", "--node-type", "lock"]
```

**Dockerfile.queue**
```dockerfile
ENTRYPOINT ["python", "-m", "src", "--node-type", "queue"]
```

**Dockerfile.cache**
```dockerfile
ENTRYPOINT ["python", "-m", "src", "--node-type", "cache"]
```

Semua menggunakan base image `python:3.11-slim`, menginstal dependencies dari `requirements.txt`, dan memiliki HEALTHCHECK `curl /health` dengan `start_period: 15s` untuk memberi waktu Python app binding port.

### 6.2 Struktur Docker Compose

```
12 services:
├── redis          → port 6379 (healthcheck: redis-cli ping)
├── lock1          → port 8001 (Raft LEADER)
├── lock2          → port 8002 (Raft FOLLOWER)
├── lock3          → port 8003 (Raft FOLLOWER)
├── queue1         → port 8091
├── queue2         → port 8092
├── queue3         → port 8093
├── cache1         → port 8101 (DirectoryController)
├── cache2         → port 8102
├── cache3         → port 8103
├── prometheus     → port 9090
└── grafana        → port 3000

network: distributed_net (bridge driver)
volume: redis_data (persistent)
```

Semua node menggunakan `depends_on` dengan `condition: service_healthy` sehingga startup order terjamin — Redis harus healthy sebelum node manapun dimulai.

### 6.3 Scaling Node

Untuk menambah node keempat pada lock cluster, cukup duplikasi service block di `docker-compose.yml`:

```yaml
lock4:
  build:
    dockerfile: docker/Dockerfile.lock
  command: ["--node-id", "lock4", "--port", "8004",
            "--peers", "lock1:8001,lock2:8002,lock3:8003"]
  ports:
    - "8004:8004"
```

Catatan: Raft memerlukan jumlah node ganjil. Tambahkan lock4 dan lock5 bersamaan untuk menjaga quorum.

---

## 7. Design Decisions dan Trade-offs

**1. HTTP over gRPC**
aiohttp memberikan unified REST API plane — endpoint client dan peer communication menggunakan protokol yang sama. Trade-off: throughput lebih rendah dari gRPC, tetapi jauh lebih mudah di-debug dan di-test dengan curl.

**2. Redis sebagai Raft Log**
Menghindari kompleksitas file mount di multi-container setup. Redis juga digunakan bersama oleh queue dan cache sehingga hanya perlu satu backing service. Trade-off: Redis menjadi single point of failure — di production gunakan Redis Sentinel atau Cluster.

**3. Cluster Terpisah per Komponen**
Lock, queue, dan cache masing-masing memiliki cluster node sendiri dan Dockerfile sendiri. Trade-off: lebih banyak container, tetapi memenuhi rubrik "Dockerfile untuk setiap komponen" dan memungkinkan scaling independen.

**4. asyncio.Event untuk Invalidation ACK**
Zero CPU polling saat menunggu MESI invalidation acknowledgment. Trade-off: jika ACK tidak datang, `wait_for` timeout 2 detik akan melepas blokir secara paksa.

**5. DirectoryController hanya di cache1**
Assignment deterministik yang sederhana berdasarkan `node_id == "cache1"`. Trade-off: cache1 menjadi bottleneck pada skala besar — di production gunakan distributed directory dengan consistent hashing.