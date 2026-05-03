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
    Distributed Cache node using MESI protocol.
    """
    def __init__(self, node_id: str, host: str, port: int, redis_url: str, directory_url: str, is_directory: bool = False, peers: Dict[str, str] = None) -> None:
        super().__init__(node_id, host, port, heartbeat_interval=10.0)
        self.redis_url = redis_url
        self.directory_url = directory_url
        self.is_directory = is_directory
        self.peers = peers or {}  # Maps node_id to url
        
        self.redis: Optional[Redis] = None
        self.cache = LRUCache(capacity=1000)
        self.directory_ctrl = DirectoryController() if is_directory else None
        
        self.invalidation_events: Dict[str, asyncio.Event] = {}
        self.expected_acks: Dict[str, int] = {}
        self.invalidations_received = 0
        
        self.total_read_latency = 0.0
        self.total_reads = 0

        # REST API Routes
        self._app.router.add_get('/cache/{key}', self.api_get)
        self._app.router.add_put('/cache/{key}', self.api_put)
        self._app.router.add_delete('/cache/{key}', self.api_delete)
        self._app.router.add_get('/cache/metrics', self.api_metrics)
        self._app.router.add_get('/cache/coherence-state', self.api_coherence)
        
        # Internal MESI Routes
        self._app.router.add_post('/internal/invalidate', self.internal_invalidate)
        self._app.router.add_post('/internal/invalidate_ack', self.internal_invalidate_ack)
        self._app.router.add_post('/internal/downgrade', self.internal_downgrade)
        self._app.router.add_get('/internal/fetch/{key}', self.internal_fetch)
        
        # Internal Directory Routes (if this node is acting as directory)
        if self.is_directory:
            self._app.router.add_post('/dir/read', self.dir_read)
            self._app.router.add_post('/dir/write', self.dir_write)

    async def start(self) -> None:
        self.redis = Redis.from_url(self.redis_url, decode_responses=True)
        await super().start()
        logger.info(f"CacheNode {self.node_id} started. Is Directory: {self.is_directory}")

    async def stop(self) -> None:
        if self.redis:
            await self.redis.close()
        await super().stop()

    async def process_message(self, message: Any) -> None:
        pass

    async def _forward(self, method: str, url: str, **kwargs) -> Any:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.request(method, url, **kwargs) as response:
                    if response.status >= 400:
                        return None
                    return await response.json()
        except Exception as e:
            logger.error(f"Failed to forward {method} to {url}", exc_info=e)
            return None

    async def _read_from_redis(self, key: str) -> Optional[str]:
        return await self.redis.get(f"cache:{key}")

    async def _write_to_redis(self, key: str, value: str) -> None:
        await self.redis.set(f"cache:{key}", value)

    # --- Core Cache Operations ---

    async def read(self, key: str) -> Dict[str, Any]:
        """Reads a value from the cache, coordinating with the directory if necessary."""
        start_time = time.time()
        line = self.cache.get(key)
        
        if line and line.state != CacheLineState.INVALID:
            latency = time.time() - start_time
            self.total_reads += 1
            self.total_read_latency += latency
            return {"value": line.value, "cache_hit": True}

        # Miss or Invalid - contact directory
        req_data = {"key": key, "node_id": self.node_id}
        dir_res = await self._forward('POST', f"{self.directory_url}/dir/read", json=req_data)
        
        value = None
        new_state = CacheLineState.SHARED
        
        if not dir_res:
            # Fallback to direct DB read if directory is dead
            value = await self._read_from_redis(key)
            new_state = CacheLineState.EXCLUSIVE
        else:
            action = dir_res["action"]
            state_str = dir_res["state"]
            new_state = CacheLineState[state_str]
            
            if action == "FETCH_FROM_DB":
                value = await self._read_from_redis(key)
            elif action == "FETCH_FROM_NODE" or action == "DOWNGRADE_AND_FETCH":
                target_node = dir_res.get("node_id")
                if target_node and target_node in self.peers:
                    t_url = self.peers[target_node]
                    if action == "DOWNGRADE_AND_FETCH":
                        await self._forward('POST', f"{t_url}/internal/downgrade", json={"key": key})
                    
                    fetch_res = await self._forward('GET', f"{t_url}/internal/fetch/{key}")
                    if fetch_res and "value" in fetch_res:
                        value = fetch_res["value"]
                    else:
                        value = await self._read_from_redis(key) # fallback
                else:
                    value = await self._read_from_redis(key)

        if value is not None:
            new_line = CacheLine(key=key, value=value, state=new_state, node_id=self.node_id, size_bytes=sys.getsizeof(value))
            evicted = self.cache.put(key, new_line)
            if evicted and evicted.state == CacheLineState.MODIFIED:
                await self.flush(evicted.key, evicted)
        
        latency = time.time() - start_time
        self.total_reads += 1
        self.total_read_latency += latency
        return {"value": value, "cache_hit": False}

    async def write(self, key: str, value: str) -> bool:
        """Writes a value to the cache and invalidates other sharers."""
        line = self.cache.cache.get(key)  # Direct access to avoid updating access stats just yet
        
        if line and line.state in (CacheLineState.MODIFIED, CacheLineState.EXCLUSIVE):
            # Silent upgrade to MODIFIED if EXCLUSIVE, or just update if MODIFIED
            line.value = value
            line.state = CacheLineState.MODIFIED
            line.last_accessed = time.time()
            line.access_count += 1
            self.cache.cache[key] = line # move to end indirectly or keep order
            self.cache.cache.move_to_end(key)
            await self._write_to_redis(key, value)
            return True

        # Need directory coordination
        req_data = {"key": key, "node_id": self.node_id}
        dir_res = await self._forward('POST', f"{self.directory_url}/dir/write", json=req_data)
        
        if dir_res and dir_res.get("action") == "INVALIDATE":
            sharers = dir_res.get("sharers", [])
            if sharers:
                await self._broadcast_invalidate_and_wait(key, sharers)

        # Update local
        new_line = CacheLine(key=key, value=value, state=CacheLineState.MODIFIED, node_id=self.node_id, size_bytes=sys.getsizeof(value))
        evicted = self.cache.put(key, new_line)
        if evicted and evicted.state == CacheLineState.MODIFIED and evicted.key != key:
            await self.flush(evicted.key, evicted)
            
        await self._write_to_redis(key, value)
        return True

    async def _broadcast_invalidate_and_wait(self, key: str, sharers: List[str]) -> None:
        """Sends INVALIDATE to all sharers and waits for ACKs."""
        event = asyncio.Event()
        self.invalidation_events[key] = event
        self.expected_acks[key] = len(sharers)
        
        tasks = []
        for s_id in sharers:
            if s_id in self.peers:
                url = f"{self.peers[s_id]}/internal/invalidate"
                tasks.append(self._forward('POST', url, json={"key": key, "requester": self.node_id}))
                
        await asyncio.gather(*tasks, return_exceptions=True)
        
        # Wait for all ACKs or timeout
        try:
            await asyncio.wait_for(event.wait(), timeout=2.0)
        except asyncio.TimeoutError:
            logger.warning(f"Timeout waiting for invalidation ACKs for key {key}")
        finally:
            self.invalidation_events.pop(key, None)
            self.expected_acks.pop(key, None)

    async def invalidate(self, key: str) -> bool:
        """Processes an incoming INVALIDATE request."""
        line = self.cache.cache.get(key)
        if line:
            line.state = CacheLineState.INVALID
        self.invalidations_received += 1
        return True

    async def flush(self, key: str, line: Optional[CacheLine] = None) -> bool:
        """Writes a dirty line back to storage and downgrades to INVALID."""
        if not line:
            line = self.cache.cache.get(key)
            
        if line and line.state == CacheLineState.MODIFIED:
            await self._write_to_redis(key, line.value)
            line.state = CacheLineState.INVALID
            return True
        return False

    # --- Internal MESI API Handlers ---

    async def internal_invalidate(self, request: web.Request) -> web.Response:
        data = await request.json()
        key = data["key"]
        requester = data["requester"]
        
        await self.invalidate(key)
        
        # Send ACK back to requester
        if requester in self.peers:
            ack_url = f"{self.peers[requester]}/internal/invalidate_ack"
            asyncio.create_task(self._forward('POST', ack_url, json={"key": key, "node_id": self.node_id}))
            
        return web.json_response({"status": "ok"})

    async def internal_invalidate_ack(self, request: web.Request) -> web.Response:
        data = await request.json()
        key = data["key"]
        
        if key in self.expected_acks:
            self.expected_acks[key] -= 1
            if self.expected_acks[key] <= 0:
                if key in self.invalidation_events:
                    self.invalidation_events[key].set()
                    
        return web.json_response({"status": "ok"})

    async def internal_downgrade(self, request: web.Request) -> web.Response:
        """Forces a MODIFIED line to write back and become SHARED."""
        data = await request.json()
        key = data["key"]
        line = self.cache.cache.get(key)
        if line and line.state == CacheLineState.MODIFIED:
            await self._write_to_redis(key, line.value)
            line.state = CacheLineState.SHARED
        return web.json_response({"status": "ok"})

    async def internal_fetch(self, request: web.Request) -> web.Response:
        """Serves a read request from another node."""
        key = request.match_info['key']
        line = self.cache.cache.get(key)
        if line and line.state != CacheLineState.INVALID:
            return web.json_response({"value": line.value})
        return web.json_response({"error": "Not found or invalid"}, status=404)

    # --- Directory Handlers (Active if is_directory=True) ---

    async def dir_read(self, request: web.Request) -> web.Response:
        data = await request.json()
        result = self.directory_ctrl.handle_read(data["key"], data["node_id"])
        return web.json_response(result)

    async def dir_write(self, request: web.Request) -> web.Response:
        data = await request.json()
        result = self.directory_ctrl.handle_write(data["key"], data["node_id"])
        return web.json_response(result)


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
        # Simplistic invalidate broadcast to clean caches
        if self.is_directory:
            sharers = self.directory_ctrl.directory.get(key, {}).get("sharers", set())
            await self._broadcast_invalidate_and_wait(key, list(sharers))
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
        """Debug endpoint showing all local cache states."""
        states = {k: v.to_dict() for k, v in self.cache.cache.items()}
        return web.json_response({
            "node_id": self.node_id,
            "directory_state": self.directory_ctrl.directory if self.is_directory else "Not a directory",
            "local_lines": states
        })
