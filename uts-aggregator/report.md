# Analisis Komprehensif: Pub-Sub Log Aggregator dalam Distributed Systems

*Berdasarkan Tanenbaum & Van Steen, "Distributed Systems: Principles and Paradigms," 2nd Ed.*

---

## T1 (Bab 1): Karakteristik Utama Sistem Terdistribusi & Trade-off Pub-Sub Log Aggregator

### Karakteristik Utama

Buku mendefinisikan distributed system sebagai *"a collection of independent computers that appears to its users as a single coherent system."* Dari definisi ini dan Bab 1, ada empat karakteristik dan tujuan utama:

**1. Resource Sharing (Aksesibilitas)**
Semua komponen — broker, storage, consumer — harus dapat diakses secara terkontrol dan efisien. Untuk log aggregator, ini berarti event stream harus dapat dikonsumsi oleh banyak subscriber (analytics, alerting, auditing) secara bersamaan tanpa bottleneck.

**2. Distribution Transparency**
Buku mengidentifikasi delapan bentuk transparency (ISO, 1995): access, location, migration, replication, concurrency, failure, performance, dan scaling transparency. Untuk Pub-Sub aggregator:

| Transparency | Implementasi pada Aggregator |
|---|---|
| **Access** | API publish/consume seragam untuk semua producer/consumer |
| **Location** | Consumer tidak perlu tahu broker node mana yang menyimpan partition |
| **Replication** | Consumer tidak tahu bahwa log direplikasi ke multiple nodes |
| **Failure** | Retries dan re-election leader terjadi tanpa consumer sadar |
| **Concurrency** | Multiple consumer group membaca topic yang sama secara independen |

**3. Openness**
Sistem harus mengekspos interface standar (IDL/schema registry). Buku menekankan bahwa spesifikasi harus *complete* (semua yang diperlukan untuk implementasi) dan *neutral* (tidak memaksa implementasi tertentu) — relevan untuk mendefinisikan schema event Pub-Sub.

**4. Scalability**
Buku mengidentifikasi tiga dimensi: *size scalability* (lebih banyak producer/consumer), *geographical scalability* (distribusi lintas datacenter), dan *administrative scalability* (multiple tim/organisasi menggunakan sistem).

### Trade-off Desain Pub-Sub Log Aggregator

**Trade-off 1: Transparency vs. Performance**
> Buku menyatakan: *"There is also a trade-off between a high degree of transparency and the performance of a system."*

Menyembunyikan replication lag (replication transparency) dapat membuat consumer percaya data sudah konsisten, padahal sebenarnya ada jeda propagasi. Aggregator yang sangat transparan (misal, selalu menunggu semua replica acknowledge) akan memperlambat throughput secara signifikan.

**Trade-off 2: Centralized vs. Decentralized**
Buku memperingatkan bahwa *"centralized services, data, and algorithms"* adalah hambatan utama scalability. Jika broker tunggal menangani semua log routing:
- Satu server → satu bottleneck bandwidth
- Satu database log → saturasi I/O
- Satu algoritma routing → tidak scale

Solusi: partitioning topic ke multiple broker (seperti Kafka), di mana setiap partition adalah log terdistribusi mandiri.

**Trade-off 3: Consistency vs. Availability**
Buku menyebutkan (Chap. 7) bahwa merelaksasi konsistensi (eventual consistency) adalah satu-satunya cara praktis untuk sistem besar. Pada aggregator: apakah consumer harus selalu melihat event dalam urutan eksak, atau acceptable ada jeda konsistensi antar partition?

---

## T2 (Bab 2): Client-Server vs. Publish-Subscribe — Kapan Memilih Pub-Sub?

### Arsitektur Client-Server (Centralized)

Dari Bab 2, model dasar adalah: *"A client is a process that requests a service from a server by sending it a request and subsequently waiting for the server's reply."* Ini adalah **request-reply behavior** — sinkron dan tightly coupled.

Untuk log aggregator dalam model client-server:
- Producer mengirim event langsung ke server (HTTP POST atau RPC)
- Server memproses dan menyimpan
- Consumer melakukan polling atau request langsung ke server
- Server mengetahui siapa saja clientnya (stateful)

**Kelemahan teknis:**
- **Coupling spasial (space coupling):** Producer harus tahu alamat server; jika server berubah/scale, semua producer perlu diupdate
- **Coupling temporal (time coupling):** Consumer dan producer harus aktif bersamaan untuk komunikasi
- **Single point bottleneck:** Buku menyebutkan bahwa server tunggal menjadi bottleneck saat users/aplikasi tumbuh karena kapasitas komunikasi dan komputasinya terbatas
- **Sulit heterogeneous consumption:** Jika ada 10 jenis consumer (Elasticsearch, alerting, auditing), setiap consumer harus pull dari server yang sama dengan logika berbeda

### Arsitektur Publish-Subscribe

Buku menjelaskan: *"The basic idea is that processes publish events after which the middleware ensures that only those processes that subscribed to those events will receive them. The main advantage of event-based systems is that processes are loosely coupled... This is also referred to as being decoupled in space, or referentially decoupled."*

Untuk log aggregator Pub-Sub:
- **Decoupling spasial:** Producer tidak perlu tahu siapa konsumernya; cukup publish ke topic
- **Decoupling temporal:** Consumer bisa membaca log kapan saja (persistent log); tidak perlu aktif saat event diproduksi
- **Fan-out natural:** Satu event dapat dikonsumsi oleh N consumer group secara independen

### Kapan Memilih Pub-Sub? Alasan Teknis

**Pilih Pub-Sub ketika:**

1. **Heterogeneous consumers dengan lifecycle berbeda.** Jika Elasticsearch perlu real-time tapi audit system hanya berjalan nightly, model client-server akan mengharuskan server menyimpan state per-consumer (bermasalah dengan fault tolerance). Buku menyebutkan bahwa stateful servers rentan terhadap crash recovery issues.

2. **Fan-out N:M.** Buku menyebutkan masalah scalability dari broadcasting di LAN vs. WAN: Pub-Sub middleware dapat menangani distribusi efisien ke N subscriber tanpa producer harus mengirim N pesan terpisah.

3. **Decoupling untuk extensibility.** Sesuai prinsip *openness* dari Bab 1 — *"it should be easy to add new components or replace existing ones without affecting those components that stay in place"* — Pub-Sub memungkinkan penambahan consumer baru tanpa mengubah producer sama sekali.

4. **Asynchronous processing untuk hide latency.** Buku menyarankan: *"try to avoid waiting for responses to remote service requests as much as possible"* dengan asynchronous communication. Pub-Sub secara natural asinkron — producer publish dan lanjut, tidak perlu menunggu semua consumer selesai.

5. **Replay dan audit.** Persistent log pada Pub-Sub (seperti Kafka's commit log) memungkinkan consumer baru untuk re-read dari offset awal — tidak mungkin dalam model request-reply.

**Tetap gunakan Client-Server ketika:**
- Ada kebutuhan **response langsung** (confirmasi bahwa event sudah diproses, bukan hanya diterima broker)
- Consumer hanya satu atau dua dan interface sederhana
- Latency end-to-end sangat kritis (tambahan hop broker meningkatkan latency)

---

## T3 (Bab 3/4): At-Least-Once vs. Exactly-Once — Mengapa Idempotent Consumer Krusial?

### Definisi Delivery Semantics

Buku Bab 4 (RPC) dan Bab 8 (Fault Tolerance) membahas ini secara mendalam dalam konteks RPC failure:

**At-Most-Once:** Buku menyebutkan DCE RPC menggunakan *"at-most-once operation, in which case no call is ever carried out more than once, even in the face of system crashes."* Jika server crash, operasi mungkin tidak pernah dilakukan — acceptable untuk operasi non-kritis tapi berbahaya untuk log yang harus lengkap.

**At-Least-Once:** Buku menjelaskan masalahnya di Bab 2: *"The only thing we can do is possibly let the client resend the request when no reply message comes in. The problem, however, is that the client cannot detect whether the original request message was lost, or that transmission of the reply failed. If the reply was lost, then resending a request may result in performing the operation twice."*

Ini adalah root cause mengapa at-least-once menghasilkan **duplikasi**: retry tidak dapat membedakan "event belum sampai" dari "acknowledgement yang hilang."

**Exactly-Once:** Merupakan garansi terkuat. Dalam teori, setiap event diproses tepat satu kali. Dalam praktik, ini memerlukan distributed transaction atau 2-phase commit (Bab 8), yang mahal secara performa.

### Tabel Perbandingan

| Semantik | Duplikasi? | Data Loss? | Overhead | Cocok untuk |
|---|---|---|---|---|
| At-most-once | Tidak | Ya (mungkin) | Rendah | Metrics sampling, non-critical events |
| **At-least-once** | **Ya (mungkin)** | **Tidak** | **Sedang** | **Log aggregation (dengan idempotency)** |
| Exactly-once | Tidak | Tidak | Tinggi | Financial transactions, billing |

### Mengapa Idempotent Consumer Krusial di Presence of Retries?

Buku secara eksplisit mendefinisikan: *"When an operation can be repeated multiple times without harm, it is said to be idempotent."*

Dalam konteks log aggregator:

**Skenario tanpa idempotency:**
```
Producer → [Event E1, id=X] → Broker → Consumer processes E1
                                      ↓ (Broker crashes before ACK)
Producer → [Retry: Event E1, id=X] → Broker → Consumer processes E1 LAGI
```
Hasilnya: duplikasi di downstream (dua kali counter increment, dua kali row insert, dua kali alert trigger).

**Skenario dengan idempotent consumer:**
```
Consumer memeriksa: "Apakah event_id=X sudah pernah diproses?"
→ Ya: buang (no-op)
→ Tidak: proses dan catat di dedup store
```

**Teknis implementasi idempotency:**
1. **Dedup store (Redis/RocksDB):** Consumer menyimpan `SET processed_ids` dan memeriksa sebelum processing. TTL pada key mengontrol window dedup.
2. **Upsert vs. Insert:** Database write menggunakan `INSERT ... ON CONFLICT DO NOTHING` atau `UPSERT` berdasarkan `event_id` sebagai primary key.
3. **Idempotency key pada sisi producer:** Producer menyertakan `idempotency_key` dalam event yang deterministik (bukan random UUID per-retry), sehingga broker dapat dedup di level ingestion.

**Mengapa at-least-once + idempotency = praktis seperti exactly-once:**
- At-least-once menjamin tidak ada event hilang (kritis untuk audit log)
- Idempotency consumer menjamin duplikat tidak mengubah state akhir
- Hasilnya: *effective exactly-once semantics* tanpa overhead distributed transaction

---

## T4 (Bab 5): Skema Penamaan Topic & Event ID — Dampaknya terhadap Dedup

### Prinsip Naming dari Bab 5

Buku Bab 5 membedakan tiga konsep kunci:
- **Name:** Referensi human-readable ke entitas
- **Identifier:** Name yang secara unik mengidentifikasi entitas, *"each entity is referred to by exactly one identifier, an identifier refers to at most one entity, and an identifier always refers to the same entity"*
- **Address:** Lokasi fisik entitas

Untuk dedup yang efektif, `event_id` harus memenuhi kriteria **identifier**: unik, stabil (selalu merujuk event yang sama), dan collision-resistant.

### Skema Penamaan Topic

**Format yang direkomendasikan:**
```
{environment}.{domain}.{entity}.{event_type}.{version}

Contoh:
prod.payments.transaction.completed.v2
staging.auth.user.login_failed.v1
dev.inventory.item.stock_updated.v3
```

**Alasan teknis:**
- **Hierarchical naming** seperti DNS yang dibahas Buku Bab 5: memungkinkan *name resolution* bertahap dan routing berbasis prefix
- **Versioning eksplisit** (`.v2`): saat schema berubah, consumer lama dan baru bisa hidup berdampingan — sesuai prinsip *backward compatibility* dari openness
- **Environment prefix**: mencegah cross-contamination antar environment yang bisa menghasilkan false dedup positives

**Dampak terhadap dedup:**
Topic name yang terstruktur memungkinkan **scoped dedup**: `event_id` hanya perlu unik dalam konteks `(topic, partition)`, bukan globally. Ini mengurangi ukuran dedup store secara signifikan.

### Skema Event ID yang Collision-Resistant

**Rekomendasi: Composite ID dengan beberapa pendekatan:**

**Opsi 1: UUIDv7 (time-ordered)**
```
event_id = UUIDv7
Format: timestamp_ms (48 bit) | version (4 bit) | random (74 bit)
Contoh: 018e2b3a-f4c1-7abc-9012-34567890abcd
```
Keunggulan: time-sortable (berguna untuk ordering), globally unique dengan collision probability mendekati nol untuk 2^74 kemungkinan per millisecond.

**Opsi 2: Deterministic ID (lebih baik untuk dedup)**
```
event_id = SHA256(producer_id + timestamp_epoch_ms + sequence_counter + payload_hash)
           di-truncate ke 128 bit dan di-encode sebagai hex string
```

Keunggulan: **idempotent by construction** — jika producer mengirim ulang event yang sama persis, `event_id` identik, sehingga dedup otomatis tanpa perlu state producer.

**Opsi 3: Snowflake-style ID**
```
event_id = epoch_ms(41 bit) | datacenter_id(5 bit) | worker_id(5 bit) | sequence(12 bit)
```
Digunakan Twitter/Kafka: sortable, decentralized generation, tidak memerlukan koordinasi global — sesuai prinsip buku bahwa *"only decentralized algorithms should be used"*.

### Dampak Skema ID terhadap Dedup

| Properti ID | Dampak Dedup |
|---|---|
| **Globally unique** | Dedup bisa dilakukan cross-partition tanpa collision |
| **Deterministik pada retry** | Producer retry menghasilkan ID sama → dedup store mendeteksi duplikat |
| **Time-ordered** | Dedup window dapat di-GC (garbage collect) berdasarkan timestamp — tidak perlu simpan semua ID selamanya |
| **Short & fixed-length** | Overhead memori di dedup store kecil; Redis SET operasi O(1) |

**Struktur dedup store yang disarankan:**
```
Key: "dedup:{topic}:{event_id}"
Value: "1" (atau metadata kecil: consumer_id, processed_at)
TTL: max(processing_window) + safety_margin (misal: 24 jam + 1 jam)
```

Buku Bab 5 membahas bagaimana DHT (Distributed Hash Table) menggunakan 128-bit atau 160-bit random identifier untuk mencapai collision-resistance — prinsip yang sama berlaku untuk event_id design.

---

## T5 (Bab 6): Ordering — Kapan Total Ordering Tidak Diperlukan?

### Teori Ordering dari Buku

Bab 6 membahas Lamport's Logical Clocks dan Vector Clocks:

**Total Ordering:** Semua event di seluruh sistem memiliki urutan linear yang disepakati semua node. Dicapai melalui sequencer terpusat atau Lamport timestamps. Buku mengingatkan: *"totally-ordered multicasting using Lamport's logical clocks does not scale"* karena setiap node harus menunggu konfirmasi global.

**Causal Ordering:** Hanya menjamin bahwa jika event A *causally precedes* B (A → B), maka semua node melihat A sebelum B. Event yang tidak saling terkait boleh dilihat dalam urutan berbeda.

**FIFO Ordering:** Hanya menjamin bahwa pesan dari satu producer dilihat dalam urutan pengirimannya oleh semua consumer — tidak ada garansi antar producer berbeda.

### Kapan Total Ordering TIDAK Diperlukan?

**Kasus 1: Event dari domain berbeda yang independen**
Contoh: event `user.login` dari service Auth dan event `item.viewed` dari service Catalog. Kedua event tidak memiliki causal dependency — urutan relatif keduanya tidak bermakna untuk downstream consumer analytics.

**Kasus 2: Commutative operations pada consumer**
Jika consumer melakukan `COUNT(*)` atau `SUM(amount)`, urutan event tidak mempengaruhi hasil akhir. Ini adalah sifat komutativitas yang buku sebutkan dalam konteks monotonic writes.

**Kasus 3: Consumer yang idempotent dengan windowing**
Time-windowed analytics (misal: event per 1 menit) hanya perlu semua event dalam window hadir sebelum window ditutup (watermark). Urutan internal dalam window tidak penting.

**Kasus 4: Multi-partition consumption**
Kafka menjamin total ordering hanya dalam satu partition. Konsumsi dari multiple partition secara paralel menghasilkan interleaving — ini acceptable untuk kebanyakan use case analitik.

### Pendekatan Praktis: Event Timestamp + Monotonic Counter

**Skema yang diusulkan:**
```
event_timestamp = wall_clock_ms (dari producer, NTP-synchronized)
sequence_number = monotonic counter per-producer (reset saat restart)

sort_key = (event_timestamp, producer_id, sequence_number)
```

**Cara kerja:**
1. Producer menyertakan wall clock timestamp dan monotonic counter lokal dalam setiap event
2. Consumer menggunakan `sort_key` untuk re-order events dalam processing window
3. Watermark strategy: consumer menunggu X ms setelah window end sebelum menutup window — memberikan waktu untuk out-of-order events tiba

**Batasan pendekatan ini:**

| Batasan | Penjelasan |
|---|---|
| **Clock skew** | Buku Bab 6 menjelaskan bahwa *"it is impossible to get all the clocks exactly synchronized"*; perbedaan jam antar server bisa mencapai puluhan milidetik |
| **NTP adjustments** | NTP dapat menyebabkan clock jump backward — monotonic counter per-node membantu tapi tidak mencegah ambiguitas antar node |
| **Producer restart** | Monotonic counter reset saat producer restart — sequence gap detectable tapi ordering window menjadi ambigu |
| **Watermark tuning** | Window terlalu pendek → events sering terlambat; terlalu panjang → latency tinggi |

**Solusi praktis: Hybrid approach**
Gunakan **Lamport timestamp yang dilonggarkan**: setiap consumer menggabungkan `(event_timestamp, producer_logical_clock)`. Seperti yang buku bahas dengan vector clocks — representasi history yang kompak dan efisien untuk causal ordering tanpa memerlukan total global ordering.

---

## T6 (Bab 6/8): Failure Modes & Strategi Mitigasi

### Failure Models dari Bab 8

Buku Bab 8 mengklasifikasikan failure types:
1. **Crash failure:** Server berhenti dan tidak merespons lagi
2. **Omission failure:** Server gagal merespons beberapa request (send/receive omission)
3. **Timing failure:** Server merespons terlalu lambat
4. **Byzantine failure:** Server merespons dengan nilai yang salah/tidak terduga

Untuk log aggregator, tiga failure mode utama yang relevan:

---

### Failure Mode 1: Duplikasi Event

**Sebab:** Buku menjelaskan bahwa saat reply hilang, *"resending a request may result in performing the operation twice"* — network partition antara producer dan broker, atau broker crash sebelum kirim ACK.

**Manifestasi:**
- Producer retry → broker menerima event dua kali
- Consumer retry → downstream database diupdate dua kali
- Broker redelivery setelah consumer crash sebelum commit offset

**Mitigasi:**
```
Layer 1 (Broker): Idempotent producer API
  - Producer mengirim event dengan idempotency_key
  - Broker dedup di level ingestion dalam window (misal 5 menit)
  
Layer 2 (Consumer): Transactional processing
  - Read event → process → commit offset dalam satu atomic transaction
  - Atau: two-phase dedup dengan Redis (check → process → mark)
  
Layer 3 (Durable Dedup Store):
  - event_id disimpan di Redis/RocksDB dengan TTL
  - Consumer check sebelum processing
```

---

### Failure Mode 2: Out-of-Order Delivery

**Sebab:** Network paths berbeda memiliki latency berbeda; multiple producer mengirim ke partition yang sama; consumer restart dan re-read dari checkpoint lama.

**Manifestasi:**
- Event dengan `timestamp=T+1` tiba sebelum `timestamp=T`
- State machine di consumer mencapai state yang tidak valid

**Mitigasi:**
```
Strategy 1: Resequencing buffer
  - Consumer buffer events dalam window W (misal 500ms)
  - Sort berdasarkan (event_timestamp, sequence_number)
  - Flush setelah watermark tercapai
  
Strategy 2: Event sourcing dengan sequence validation
  - Consumer track expected_sequence per producer
  - Jika gap terdeteksi → trigger backfill atau alert
  
Strategy 3: Processing yang toleran terhadap order
  - Desain downstream logic agar kommutative/idempotent
  - Gunakan aggregation yang tidak order-sensitive
```

---

### Failure Mode 3: Consumer/Broker Crash

**Sebab:** Buku Bab 8 membahas berbagai bentuk crash dan recovery: *"A process that is crash failure is one that simply stops, and does not respond to messages."*

**Manifestasi:**
- Consumer crash setelah process tapi sebelum commit offset → redelivery saat restart
- Broker leader crash → partition unavailable selama leader election
- Consumer crash di tengah batch processing → partial state corruption

**Mitigasi:**

**Retry dengan Exponential Backoff:**
```
base_delay = 100ms
max_delay = 30s
jitter = random(0, base_delay)

delay(attempt) = min(base_delay * 2^attempt + jitter, max_delay)
```
Jitter mencegah thundering herd (semua consumer retry serentak setelah broker recovery).

**Dead Letter Queue (DLQ):**
```
Jika event gagal diproses setelah N retry:
  → Pindah ke DLQ topic: {original_topic}.dlq
  → Sertakan metadata: original_topic, retry_count, last_error, timestamp
  → Tim on-call dinotifikasi untuk manual inspection
```

**Checkpoint Strategy:**
```
Commit offset HANYA setelah:
  1. Event berhasil diproses
  2. Downstream write berhasil (jika applicable)
  3. Dedup store diupdate
  
Gunakan atomic commit bila memungkinkan (Kafka transactions)
```

---

## T7 (Bab 7): Eventual Consistency pada Aggregator — Peran Idempotency & Dedup

### Definisi Eventual Consistency dari Bab 7

Buku mendefinisikan: *"Data stores that are eventually consistent thus have the property that in the absence of updates, all replicas converge toward identical copies of each other. Eventual consistency essentially requires only that updates are guaranteed to propagate to all replicas."*

Untuk log aggregator: **eventual consistency berarti semua consumer group yang membaca topic yang sama akan akhirnya memiliki view yang konsisten**, meskipun pada satu titik waktu beberapa consumer mungkin lebih behind dibanding yang lain.

### Bagaimana Idempotency + Dedup Membantu Mencapai Konsistensi?

Buku Bab 7 mengidentifikasi masalah utama eventual consistency: *"problems arise when different replicas are accessed over a short period of time"* — consumer yang berganti-ganti membaca dari replica berbeda bisa melihat inkonsistensi.

Dalam konteks aggregator, analoginya adalah consumer yang crash dan restart dari checkpoint berbeda, atau multiple consumer instance dari group yang sama.

**Mekanisme Konvergensi:**

```
Kondisi tanpa idempotency:
  Consumer A processes E1, E2, E3
  Consumer A crashes → restart dari offset E2
  Consumer A re-processes E2, E3
  → State aggregator = E1 + E2 + E2 + E3 (INKONSISTEN, E2 dihitung dua kali)

Kondisi dengan idempotency + dedup:
  Consumer A processes E1, E2, E3
  Consumer A crashes → restart dari offset E2
  Consumer A: "E2 sudah ada di dedup store" → skip
  Consumer A: "E3 sudah ada di dedup store" → skip
  → State aggregator = E1 + E2 + E3 (KONSISTEN)
```

**Peran masing-masing mekanisme:**

| Mekanisme | Peran dalam Konvergensi |
|---|---|
| **Idempotent operations** | Memastikan operasi yang sama dapat diulang tanpa mengubah state final — ini adalah "convergence guarantee" |
| **Dedup store** | Menyediakan memory tentang events yang sudah diproses — membatasi "window of inconsistency" |
| **At-least-once delivery** | Menjamin semua events akhirnya sampai ke semua consumer — prasyarat untuk eventual consistency |
| **Monotonic read consistency** | Buku Bab 7: consumer tidak akan pernah melihat versi lebih lama dari yang sudah dilihat sebelumnya |

**Client-centric consistency models (dari Bab 7) yang relevan:**

**Read-Your-Writes:** Setelah producer publish event E, jika producer itu sendiri kemudian membaca dari aggregator, ia harus melihat E. Ini diimplementasikan dengan memastikan producer membaca dari replica yang sudah menerima write-nya.

**Monotonic Reads:** Consumer yang membaca offset N tidak boleh kemudian membaca offset < N (tidak boleh "mundur"). Ini dijamin oleh Kafka's offset semantics.

**Writes Follow Reads:** Jika consumer membaca event E1 dan kemudian melakukan write (misal publish ke topic output), write tersebut harus mencerminkan pengetahuan tentang E1. Diimplementasikan dengan menyertakan read offset dalam write metadata.

### Eventual Consistency sebagai Desain Sadar

Buku menyarankan untuk tidak selalu mengejar strong consistency: *"these data stores offer a very weak consistency model, called eventual consistency. By introducing special client-centric consistency models, it turns out that many inconsistencies can be hidden in a relatively cheap way."*

Untuk log aggregator, **acceptable inconsistency window** adalah:
- Analytics dashboard: boleh lag 30-60 detik
- Alerting: harus near-realtime (< 5 detik)
- Audit log: strong consistency required (acknowledge setelah durable write)

Desain yang baik mendefinisikan SLA konsistensi yang berbeda per use case, bukan satu model untuk semua.

---

## T8 (Bab 1–7): Metrik Evaluasi Sistem & Kaitannya dengan Keputusan Desain

### Framework Metrik

Buku Bab 1 menyebutkan bahwa *"scalability of a system can be measured along at least three different dimensions"* dan Bab 2 (Globule example) memperkenalkan *"performance metrics mk weighted by wk"* sebagai cara mengevaluasi replication policies. Berikut framework metrik komprehensif untuk Pub-Sub log aggregator:

---

### Kelompok Metrik 1: Throughput & Kapasitas

| Metrik | Formula | Target | Hubungan Desain |
|---|---|---|---|
| **Event ingestion rate** | events/sec per broker | > 100k eps/node | Menentukan jumlah partition & broker |
| **Consumer throughput** | events/sec per consumer | > 50k eps/consumer | Menentukan consumer parallelism |
| **Broker disk I/O** | MB/s write | < 80% kapasitas | Menentukan replication factor |
| **Network bandwidth** | MB/s cross-broker | < 70% kapasitas | Menentukan partition placement |

**Kaitan desain:** Jika ingestion rate mendekati batas, tambah partition (lebih banyak parallelism producer) atau broker (lebih banyak kapasitas). Buku memperingatkan bahwa *"using only a single server is sometimes unavoidable"* untuk data sensitif — tradeoff throughput vs. security.

---

### Kelompok Metrik 2: Latency

| Metrik | Definisi | Target | Hubungan Desain |
|---|---|---|---|
| **Publish latency** | Waktu dari producer.send() hingga broker ACK | P99 < 10ms | Menentukan sync vs. async ACK |
| **End-to-end latency** | Waktu dari event creation hingga consumer receipt | P99 < 500ms | Menentukan replication wait policy |
| **Consumer lag** | Selisih antara latest offset dan consumer offset | < 10k events | Menentukan consumer scaling trigger |
| **Rebalance time** | Waktu consumer group rebalance setelah crash | < 30s | Menentukan session timeout config |

**Kaitan desain:** Buku Bab 1 menyebutkan hiding communication latency sebagai teknik scaling utama. Pilihan antara *sync replication* (low latency consumer) vs. *async replication* (high throughput producer) langsung mempengaruhi end-to-end latency SLA.

---

### Kelompok Metrik 3: Correctness & Reliability

| Metrik | Formula | Target | Hubungan Desain |
|---|---|---|---|
| **Duplicate rate** | (duplicate_events / total_events) × 100% | < 0.01% | Menentukan investasi dedup infrastructure |
| **Out-of-order rate** | (reordered_events / total_events) × 100% | < 0.1% | Menentukan watermark window size |
| **Message loss rate** | (expected_events - received_events) / expected_events | = 0% | Menentukan min.insync.replicas |
| **DLQ rate** | (DLQ_events / total_events) × 100% | < 0.001% | Menentukan retry policy aggressiveness |

**Kaitan desain:** Duplicate rate yang tinggi mengindikasikan dedup store TTL terlalu pendek atau idempotency key tidak cukup deterministik. DLQ rate tinggi mengindikasikan schema incompatibility atau consumer logic bug.

---

### Kelompok Metrik 4: Availability & Fault Tolerance

| Metrik | Definisi | Target | Hubungan Desain |
|---|---|---|---|
| **Broker availability** | Uptime percentage | 99.99% | Menentukan replication factor (min 3) |
| **MTTR (Mean Time to Recover)** | Rata-rata waktu recovery dari failure | < 60s | Menentukan leader election timeout |
| **Partition unavailability** | Durasi partition tidak dapat ditulis/dibaca | < 30s per kejadian | Menentukan unclean leader election policy |
| **Data durability** | Probability of data loss per year | > 99.9999999% | Menentukan retention policy dan backup |

**Kaitan desain:** Buku Bab 8 menyebutkan bahwa *"masking failures is one of the hardest issues in distributed systems"* terutama membedakan node yang crash dari yang sangat lambat. Konfigurasi timeout yang terlalu agresif dapat menyebabkan false positive leader election (availability turun); terlalu toleran menyebabkan MTTR tinggi.

---

### Peta Hubungan Metrik → Keputusan Desain

```
HIGH throughput needed
  ↓
→ Lebih banyak partition
→ Async producer ACK (turunkan latency, naikkan throughput)
→ Batching di producer
→ Konsekuensi: lebih sulit jaga total ordering

LOW end-to-end latency needed  
  ↓
→ Sync replication (acks=all)
→ Smaller batch size
→ Konsekuensi: throughput turun, load lebih tinggi di broker

HIGH correctness (low duplicate rate)
  ↓
→ Exactly-once semantics (Kafka transactions)
→ Durable dedup store dengan TTL panjang
→ Idempotent consumer design
→ Konsekuensi: latency naik, kompleksitas naik

HIGH availability
  ↓
→ Replication factor 3+
→ min.insync.replicas = 2
→ Consumer group dengan multiple instances
→ Konsekuensi: resource cost naik, cross-datacenter traffic naik
```