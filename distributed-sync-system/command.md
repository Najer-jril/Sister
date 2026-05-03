// Lock Manager
# Acquire exclusive lock (client-A)
curl -X POST http://localhost:8003/locks/acquire \
  -H "Content-Type: application/json" \
  -d '{"resource_id":"database","lock_type":"EXCLUSIVE","holder_id":"client-A","timeout":30}'

# Simpan lock_id dari response di atas

# Check lock status
curl http://localhost:8003/locks/database/status

# Try acquire same resource (client-B)
curl -X POST http://localhost:8003/locks/acquire \
  -H "Content-Type: application/json" \
  -d '{"resource_id":"database","lock_type":"EXCLUSIVE","holder_id":"client-B","timeout":30}'

# Try acquire dari non-leader (port 8002) — harus 503
curl -X POST http://localhost:8001/locks/acquire \
  -H "Content-Type: application/json" \
  -d '{"resource_id":"test","lock_type":"EXCLUSIVE","holder_id":"client-C","timeout":30}'

# Shared lock pada resource berbeda — harus sukses
curl -X POST http://localhost:8003/locks/acquire \
  -H "Content-Type: application/json" \
  -d '{"resource_id":"config","lock_type":"SHARED","holder_id":"client-A","timeout":30}'

curl -X POST http://localhost:8003/locks/acquire \
  -H "Content-Type: application/json" \
  -d '{"resource_id":"config","lock_type":"SHARED","holder_id":"client-B","timeout":30}'

# Check deadlocks
curl http://localhost:8003/locks/deadlocks

// Queue System
# Produce message 1
curl -X POST http://localhost:8091/queues/orders/messages \
  -H "Content-Type: application/json" \
  -d '{"payload":{"order_id":"ORD-001","item":"laptop","amount":1500}}'

# Produce message 2
curl -X POST http://localhost:8091/queues/orders/messages \
  -H "Content-Type: application/json" \
  -d '{"payload":{"order_id":"ORD-002","item":"phone","amount":800}}'

# Check stats before consume
curl http://localhost:8091/queues/orders/stats

# Consume message (simpan message_id dari response)
curl "http://localhost:8091/queues/orders/messages/next?consumer_id=worker-1&timeout=5"

# Acknowledge (ganti {MESSAGE_ID} dengan message_id dari response consume)
curl -X POST http://localhost:8091/queues/messages/{ec63f09e-78a3-467b-b880-ba556d7533ac}/ack \
  -H "Content-Type: application/json" \
  -d '{"consumer_id":"worker-1"}'

# Consume message 2
curl "http://localhost:8091/queues/orders/messages/next?consumer_id=worker-1&timeout=5"

# Reject dengan requeue (ganti {MESSAGE_ID})
curl -X POST http://localhost:8091/queues/messages/{MESSAGE_ID}/reject \
  -H "Content-Type: application/json" \
  -d '{"consumer_id":"worker-1","requeue":true}'

# Check stats after
curl http://localhost:8091/queues/orders/stats

// Cache MESI
# Write ke cache1 (directory node)
curl -X PUT http://localhost:8101/cache/user:100 \
  -H "Content-Type: application/json" \
  -d '{"value":"najer_profile_data"}'

# Read dari cache1 — harus cache_hit: true
curl http://localhost:8101/cache/user:100

# Read dari cache2 — harus cache_hit: false (miss, lalu fetch)
curl http://localhost:8102/cache/user:100

# Read dari cache2 lagi — sekarang harus cache_hit: true (sudah di-cache)
curl http://localhost:8102/cache/user:100

# Show coherence state (MESI states)
curl http://localhost:8101/cache/coherence-state

# Show metrics
curl http://localhost:8101/cache/metrics

# Write dari cache2 (trigger invalidation ke cache1)
curl -X PUT http://localhost:8102/cache/user:100 \
  -H "Content-Type: application/json" \
  -d '{"value":"updated_by_cache2"}'

# Read dari cache1 setelah invalidation — harus cache_hit: false
curl http://localhost:8101/cache/user:100

# Show coherence state lagi (lihat perubahan MESI)
curl http://localhost:8101/cache/coherence-state

// Unit test
PYTHONPATH=. pytest tests/unit/ -v