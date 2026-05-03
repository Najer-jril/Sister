# Deployment & Operations Guide

## Prerequisites checklist
- [ ] Docker (v20.x+)
- [ ] Docker Compose Plugin (v2.x+)
- [ ] Python 3.8+ (for local testing/benchmarks execution)
- [ ] Redis CLI (optional, for inspecting raw keys)

## Docker Deployment

1. Set up your `.env` configuration file using `.env.example` as a baseline.
2. Grant execution bounds to the orchestrator: `chmod +x docker/start-cluster.sh`
3. Execute: `./docker/start-cluster.sh`
4. Use `docker ps` to verify `redis`, `node1`, `node2`, `node3`, `prometheus` and `grafana` are actively running without endless restarts.

## Scaling Nodes (Adding Node4)
1. Add a new service block to `docker/docker-compose.yml`:
```yaml
  node4:
    build:
      context: ..
      dockerfile: docker/Dockerfile.node
    environment:
      - NODE_ID=node4
      - NODE_PORT=8004
    ports:
      - "8004:8004"
```
2. Update the `.env` `PEER_NODES` mapping on all existing configurations to include `node4:8004`.
3. Restart the cluster layout so configurations apply. Nodes dynamically adjust majority limits (e.g. going from 3 -> 4 shifts required consensus majority from 2 to 3).

## Monitoring setup (Prometheus & Grafana)
* **Prometheus** UI sits at `http://127.0.0.1:9090` and scrapes `/metrics` endpoints.
* **Grafana** sits independently on `http://127.0.0.1:3000`. Connect Prometheus via `Configuration -> Data Sources -> Add Prometheus` (URL: `http://prometheus:9090`). Create Dashboards querying `sync_system_messages_sent_total` and `sync_system_is_leader`.

## Troubleshooting

1. **Symptom: Nodes stuck in CANDIDATE state.**
   * *Resolution*: Check that the `PEER_NODES` array exactly matches network IPs/hostnames. In docker-compose, use component names (e.g., `node1`, `node2`).
2. **Symptom: Cannot acquire lock (503 Service Unavailable).**
   * *Resolution*: The node responding is not the Raft Leader. Check the `leader_id` returned in the JSON fault and resend your request explicitly to that port.
3. **Symptom: Messages immediately jumping to Dead Letter Queue (DLQ).**
   * *Resolution*: Ensure your consumer tasks are triggering the `/ack` endpoint before the `ack_timeout_seconds` triggers internally on the node.
4. **Symptom: Cache returning stale data across nodes.**
   * *Resolution*: Network partitioned invalidation block. Validate `/cache/coherence-state` to verify no node is isolating `MODIFIED` blocks indefinitely.
5. **Symptom: `redis:7-alpine` repeating exit code 1 or 137.**
   * *Resolution*: Usually caused by insufficient permissions on the bounded data volume `redis_data`. Clear the volume with `docker volume rm distributed_net_redis_data`.

## Performance Tuning Tips
- **Raft Election**: Adjust `ELECTION_TIMEOUT_MAX` if network ping latency climbs organically.
- **Cache Eviction**: Adjust `LRUCache(capacity=XXX)` depending on vertical RAM limits. Current limit defaults to generous `1000` payload blocks.
- **Failures / Phi Limit**: `PhiAccrualFailureDetector(phi_threshold=8.0)`. Lower to `5.0` for aggressive disconnects.
