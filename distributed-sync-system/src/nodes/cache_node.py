# src/nodes/cache_node.py
import asyncio
import json
import logging
import time
import sys
from collections import OrderedDict
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Dict, List, Optional, Set, Tuple

import aiohttp
from aiohttp import web
from redis.asyncio import Redis

from src.nodes.base_node import BaseNode, NodeState

logger = logging.getLogger(__name__)

class CacheLineState(Enum):
    MODIFIED = "MODIFIED"
    EXCLUSIVE = "EXCLUSIVE"
    SHARED = "SHARED"
    INVALID = "INVALID"

@dataclass
class CacheLine:
    key: str
    value: Any
    state: CacheLineState
    last_accessed: float = field(default_factory=time.time)
    access_count: int = 0
    size_bytes: int = 0
    node_id: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "key": self.key,
            "value": self.value,
            "state": self.state.value,
            "last_accessed": self.last_accessed,
            "access_count": self.access_count,
            "size_bytes": self.size_bytes,
            "node_id": self.node_id
        }

class LRUCache:
    """Capacity-bounded LRU Cache."""
    def __init__(self, capacity: int = 1000):
        self.capacity = capacity
        self.cache: OrderedDict[str, CacheLine] = OrderedDict()
        self.hits = 0
        self.misses = 0
        self.evictions = 0

    def get(self, key: str) -> Optional[CacheLine]:
        if key in self.cache:
            line = self.cache.pop(key)
            if line.state != CacheLineState.INVALID:
                line.last_accessed = time.time()
                line.access_count += 1
                self.cache[key] = line  # Move to end (most recently used)
                self.hits += 1
                return line
            else:
                # Treat invalid as miss, put it back to not destroy reference entirely, but it's logically missing
                self.cache[key] = line
        self.misses += 1
        return None

    def put(self, key: str, line: CacheLine) -> Optional[CacheLine]:
        evicted = None
        if key in self.cache:
            self.cache.pop(key)
        elif len(self.cache) >= self.capacity:
            evicted = self.evict_lru()
        self.cache[key] = line
        return evicted

    def evict_lru(self) -> Optional[CacheLine]:
        if not self.cache:
            return None
        key, line = self.cache.popitem(last=False)  # pop from beginning (least recently used)
        self.evictions += 1
        return line

    def get_stats(self) -> Dict[str, Any]:
        total = self.hits + self.misses
        hit_rate = (self.hits / total) if total > 0 else 0.0
        return {
            "hits": self.hits,
            "misses": self.misses,
            "evictions": self.evictions,
            "hit_rate": hit_rate,
            "size": len(self.cache),
            "capacity": self.capacity
        }

class DirectoryController:
    """
    In a real system, this could be distributed or sharded. 
    Here it acts as a central directory running on a designated node.
    """
    def __init__(self) -> None:
        # Format: key -> {"state": CacheLineState, "sharers": set[str], "owner": str}
        self.directory: Dict[str, Dict[str, Any]] = {}

    def handle_read(self, key: str, requester_id: str) -> Dict[str, Any]:
        if key not in self.directory:
            self.directory[key] = {
                "state": CacheLineState.EXCLUSIVE,
                "sharers": {requester_id},
                "owner": requester_id
            }
            return {"action": "FETCH_FROM_DB", "state": "EXCLUSIVE"}

        entry = self.directory[key]
        if entry["state"] in (CacheLineState.EXCLUSIVE, CacheLineState.SHARED):
            entry["state"] = CacheLineState.SHARED
            entry["sharers"].add(requester_id)
            # Find an owner to provide the data, or DB
            provider = entry["owner"] if entry["owner"] else list(entry["sharers"])[0]
            return {"action": "FETCH_FROM_NODE", "node_id": provider, "state": "SHARED"}
            
        elif entry["state"] == CacheLineState.MODIFIED:
            # Requires downgrade of the current owner
            old_owner = entry["owner"]
            entry["state"] = CacheLineState.SHARED
            entry["sharers"].add(requester_id)
            entry["owner"] = None  # Or keep as one of them
            return {"action": "DOWNGRADE_AND_FETCH", "node_id": old_owner, "state": "SHARED"}
            
        return {"action": "FETCH_FROM_DB", "state": "SHARED"}

    def handle_write(self, key: str, requester_id: str) -> Dict[str, Any]:
        if key not in self.directory:
            self.directory[key] = {
                "state": CacheLineState.MODIFIED,
                "sharers": {requester_id},
                "owner": requester_id
            }
            return {"action": "NO_INVALIDATION_NEEDED", "sharers": []}

        entry = self.directory[key]
        sharers_to_invalidate = [s for s in entry["sharers"] if s != requester_id]
        
        entry["state"] = CacheLineState.MODIFIED
        entry["sharers"] = {requester_id}
        entry["owner"] = requester_id

        return {"action": "INVALIDATE", "sharers": sharers_to_invalidate}

    def handle_invalidate_ack(self, key: str, node_id: str) -> None:
        if key in self.directory:
            self.directory[key]["sharers"].discard(node_id)


class CacheNode(BaseNode):
    """
    Distributed Cache node using MESI-like Snooping via Redis Pub/Sub.
    """
    def __init__(self, node_id: str, host: str, port: int, redis_url: str, directory_url: str, is_directory: bool = False, peers: Dict[str, str] = None) -> None:
        super().__init__(node_id, host, port, heartbeat_interval=10.0)
        self.redis_url = redis_url
        self.directory_url = directory_url
        self.is_directory = is_directory
        self.peers = peers or {} 
        
        self.redis: Optional[Redis] = None
        self.cache = LRUCache(capacity=1000)
        self.directory_ctrl = DirectoryController() if is_directory else None
        
        self.invalidations_received = 0
        self.total_read_latency = 0.0
        self.total_reads = 0
        self._bus_task: Optional[asyncio.Task] = None

        # REST API Routes - Pastikan rute statis di atas rute dinamis {key}
        self._app.router.add_get('/cache/metrics', self.api_metrics)
        self._app.router.add_get('/cache/coherence-state', self.api_coherence)
        self._app.router.add_get('/cache/{key}', self.api_get)
        self._app.router.add_put('/cache/{key}', self.api_put)
        self._app.router.add_delete('/cache/{key}', self.api_delete)
        
        # Internal HTTP Routes (Fallback, tapi sekarang kita utamakan Redis Bus)
        self._app.router.add_post('/internal/invalidate', self.internal_invalidate)
        self._app.router.add_post('/internal/downgrade', self.internal_downgrade)
        self._app.router.add_get('/internal/fetch/{key}', self.internal_fetch)

    async def start(self) -> None:
        self.redis = Redis.from_url(self.redis_url, decode_responses=True)
        await super().start()
        
        # [FIX FINAL] Jalankan pendengar Bus Sinkronisasi via Redis
        self._bus_task = asyncio.create_task(self._redis_bus_listener())
        logger.info(f"CacheNode {self.node_id} started with Redis Pub/Sub Bus.")

    async def stop(self) -> None:
        if self._bus_task:
            self._bus_task.cancel()
        if self.redis:
            await self.redis.close()
        await super().stop()

    async def _redis_bus_listener(self) -> None:
        """Mendengarkan broadcast invalidation dari node lain via Redis (Bus Snooping)."""
        try:
            pubsub = self.redis.pubsub()
            await pubsub.subscribe("mesi_snooping_bus")
            async for message in pubsub.listen():
                if message["type"] == "message":
                    data = json.loads(message["data"])
                    # Jika ada instruksi INVALIDATE dari node LAIN
                    if data.get("action") == "INVALIDATE" and data.get("sender") != self.node_id:
                        key = data["key"]
                        line = self.cache.cache.get(key)
                        if line:
                            line.state = CacheLineState.INVALID
                        self.invalidations_received += 1
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Redis Bus listener error: {e}")

    async def process_message(self, message: Any) -> None:
        pass

    async def _read_from_redis(self, key: str) -> Optional[str]:
        return await self.redis.get(f"cache:{key}")

    async def _write_to_redis(self, key: str, value: str) -> None:
        await self.redis.set(f"cache:{key}", value)

    # --- Core Cache Operations ---

    async def read(self, key: str) -> Dict[str, Any]:
        start_time = time.time()
        line = self.cache.get(key)
        
        # Kalau ada di local cache dan TIDAK INVALID
        if line and line.state != CacheLineState.INVALID:
            latency = time.time() - start_time
            self.total_reads += 1
            self.total_read_latency += latency
            return {"value": line.value, "cache_hit": True}

        # Kalau MISS atau INVALID, ambil dari Redis
        value = await self._read_from_redis(key)
        
        if value is not None:
            new_line = CacheLine(key=key, value=value, state=CacheLineState.EXCLUSIVE, node_id=self.node_id, size_bytes=sys.getsizeof(value))
            evicted = self.cache.put(key, new_line)
            if evicted and evicted.state == CacheLineState.MODIFIED:
                await self.flush(evicted.key, evicted)
        
        latency = time.time() - start_time
        self.total_reads += 1
        self.total_read_latency += latency
        return {"value": value, "cache_hit": False}

    async def write(self, key: str, value: str) -> bool:
        """Menulis data dan langsung teriak ke Redis Bus agar semua node lain menghapus cachenya."""
        
        # 1. Update ke Redis utama duluan
        await self._write_to_redis(key, value)

        # 2. Update local cache
        line = self.cache.cache.get(key)
        if line:
            line.value = value
            line.state = CacheLineState.MODIFIED
            line.last_accessed = time.time()
            line.access_count += 1
            self.cache.cache.move_to_end(key)
        else:
            new_line = CacheLine(key=key, value=value, state=CacheLineState.MODIFIED, node_id=self.node_id, size_bytes=sys.getsizeof(value))
            evicted = self.cache.put(key, new_line)
            if evicted and evicted.state == CacheLineState.MODIFIED and evicted.key != key:
                await self.flush(evicted.key, evicted)
                
        # 3. [FIX FINAL] Broadcast pesan INVALIDATE lewat Redis Pub/Sub
        msg = json.dumps({
            "action": "INVALIDATE",
            "key": key,
            "sender": self.node_id
        })
        await self.redis.publish("mesi_snooping_bus", msg)
        
        return True

    async def flush(self, key: str, line: Optional[CacheLine] = None) -> bool:
        if not line:
            line = self.cache.cache.get(key)
        if line and line.state == CacheLineState.MODIFIED:
            await self._write_to_redis(key, line.value)
            line.state = CacheLineState.INVALID
            return True
        return False

    # --- Internal MESI API Handlers (Fallback) ---

    async def internal_invalidate(self, request: web.Request) -> web.Response:
        data = await request.json()
        key = data["key"]
        line = self.cache.cache.get(key)
        if line:
            line.state = CacheLineState.INVALID
        self.invalidations_received += 1
        return web.json_response({"status": "ok"})

    async def internal_downgrade(self, request: web.Request) -> web.Response:
        data = await request.json()
        key = data["key"]
        line = self.cache.cache.get(key)
        if line and line.state == CacheLineState.MODIFIED:
            await self._write_to_redis(key, line.value)
            line.state = CacheLineState.SHARED
        return web.json_response({"status": "ok"})

    async def internal_fetch(self, request: web.Request) -> web.Response:
        key = request.match_info['key']
        line = self.cache.cache.get(key)
        if line and line.state != CacheLineState.INVALID:
            return web.json_response({"value": line.value})
        return web.json_response({"error": "Not found or invalid"}, status=404)

    # --- Public REST API ---

    async def api_get(self, request: web.Request) -> web.Response:
        key = request.match_info['key']
        result = await self.read(key)
        if result["value"] is not None:
            return web.json_response(result, status=200)
        return web.json_response({"error": "Key not found"}, status=404)

    async def api_put(self, request: web.Request) -> web.Response:
        key = request.match_info['key']
        try:
            data = await request.json()
            value = data["value"]
            await self.write(key, value)
            return web.json_response({"status": "written"}, status=200)
        except Exception as e:
            return web.json_response({"error": str(e)}, status=400)

    async def api_delete(self, request: web.Request) -> web.Response:
        key = request.match_info['key']
        await self.redis.delete(f"cache:{key}")
        # Broadcast delete lewat Redis Bus juga
        msg = json.dumps({"action": "INVALIDATE", "key": key, "sender": self.node_id})
        await self.redis.publish("mesi_snooping_bus", msg)
        return web.json_response({"status": "deleted"}, status=200)

    async def api_metrics(self, request: web.Request) -> web.Response:
        stats = self.cache.get_stats()
        avg_latency = (self.total_read_latency / self.total_reads) if self.total_reads > 0 else 0.0
        stats.update({
            "node_id": self.node_id,
            "invalidations_received": self.invalidations_received,
            "average_read_latency_sec": avg_latency
        })
        return web.json_response(stats)

    async def api_coherence(self, request: web.Request) -> web.Response:
        states = {k: v.to_dict() for k, v in self.cache.cache.items()}
        return web.json_response({
            "node_id": self.node_id,
            "directory_state": "Snooping Bus Active",
            "local_lines": states
        })