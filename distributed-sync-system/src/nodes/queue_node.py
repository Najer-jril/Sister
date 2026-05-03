# src/nodes/queue_node.py
import asyncio
import hashlib
import json
import logging
import time
import uuid
from bisect import bisect
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Dict, List, Optional, Tuple

import aiohttp
from aiohttp import web
from redis.asyncio import Redis

from src.nodes.base_node import BaseNode, NodeState

logger = logging.getLogger(__name__)

class MessageStatus(Enum):
    PENDING = "PENDING"
    DELIVERED = "DELIVERED"
    FAILED = "FAILED"
    DEAD = "DEAD"

@dataclass
class Message:
    queue_name: str
    payload: Dict[str, Any]
    message_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    created_at: float = field(default_factory=time.time)
    delivery_count: int = 0
    max_deliveries: int = 3
    status: MessageStatus = MessageStatus.PENDING

    def is_dead_letter(self) -> bool:
        """Determines if the message has exceeded its maximum delivery attempts."""
        return self.delivery_count >= self.max_deliveries or self.status == MessageStatus.DEAD

    def to_dict(self) -> Dict[str, Any]:
        """Serializes the message to a dictionary."""
        return {
            "message_id": self.message_id,
            "queue_name": self.queue_name,
            "payload": self.payload,
            "created_at": self.created_at,
            "delivery_count": self.delivery_count,
            "max_deliveries": self.max_deliveries,
            "status": self.status.value
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Message":
        """Deserializes a dictionary into a Message."""
        return cls(
            queue_name=data["queue_name"],
            payload=data.get("payload", {}),
            message_id=data.get("message_id", str(uuid.uuid4())),
            created_at=data.get("created_at", time.time()),
            delivery_count=data.get("delivery_count", 0),
            max_deliveries=data.get("max_deliveries", 3),
            status=MessageStatus(data.get("status", "PENDING"))
        )

class ConsistentHashRing:
    """Implement consistent hashing for mapping queues to nodes."""
    
    def __init__(self) -> None:
        self._ring: List[Tuple[int, str]] = []
        self._nodes: set = set()

    def _hash(self, key: str) -> int:
        return int(hashlib.md5(key.encode('utf-8')).hexdigest(), 16)

    def add_node(self, node_id: str, weight: int = 100) -> None:
        """Adds a node to the ring with the given weight (number of virtual nodes)."""
        if node_id in self._nodes:
            return
        self._nodes.add(node_id)
        for i in range(weight):
            v_node_key = f"{node_id}:{i}"
            h = self._hash(v_node_key)
            self._ring.append((h, node_id))
        self._ring.sort()

    def remove_node(self, node_id: str) -> None:
        """Removes a node and all its virtual nodes from the ring."""
        if node_id not in self._nodes:
            return
        self._nodes.remove(node_id)
        self._ring = [(h, n) for h, n in self._ring if n != node_id]

    def get_node(self, key: str) -> str:
        """Returns the ID of the node responsible for the given key."""
        if not self._ring:
            raise ValueError("Hash ring is empty")
        h = self._hash(key)
        idx = bisect([node[0] for node in self._ring], h)
        if idx == len(self._ring):
            idx = 0
        return self._ring[idx][1]

    def get_nodes(self, key: str, n: int = 2) -> List[str]:
        """Returns the top N responsible nodes for the given key, avoiding duplicates."""
        if not self._ring:
            raise ValueError("Hash ring is empty")
        h = self._hash(key)
        idx = bisect([node[0] for node in self._ring], h)
        
        nodes = []
        count = 0
        while len(nodes) < n and count < len(self._ring):
            node_id = self._ring[idx % len(self._ring)][1]
            if node_id not in nodes:
                nodes.append(node_id)
            idx += 1
            count += 1
        return nodes

class QueueNode(BaseNode):
    """
    Distributed Queue System node using consistent hashing for partition assignment.
    Provides at-least-once delivery guarantees with un-acked message requeuing.
    """

    def __init__(self, node_id: str, host: str, port: int, redis_url: str, peers: Dict[str, str]) -> None:
        """
        Args:
            node_id: Node UUID.
            host: HTTP host to bind.
            port: HTTP port to bind.
            redis_url: Connection URL for Redis.
            peers: A mapping of peer node_ids to their base HTTP URLs.
        """
        super().__init__(node_id, host, port, heartbeat_interval=5.0)
        self.redis_url = redis_url
        self.peers = peers  # {node_id: url}
        self.redis: Optional[Redis] = None
        self.ring = ConsistentHashRing()
        
        # Add self and peers to the hash ring
        self.ring.add_node(self.node_id)
        for p_id in peers.keys():
            self.ring.add_node(p_id)

        self._app.router.add_post('/queues/{queue_name}/messages', self.api_produce)
        self._app.router.add_get('/queues/{queue_name}/messages/next', self.api_consume)
        self._app.router.add_post('/queues/messages/{message_id}/ack', self.api_ack)
        self._app.router.add_post('/queues/messages/{message_id}/reject', self.api_reject)
        self._app.router.add_get('/queues/{queue_name}/stats', self.api_stats)

        self._ack_monitor_task: Optional[asyncio.Task] = None
        self.ack_timeout_seconds = 30.0

    async def start(self) -> None:
        """Starts the queue node and internal processing tasks."""
        self.redis = Redis.from_url(self.redis_url, decode_responses=True)
        await super().start()
        
        await self._recover_in_flight()
        self._ack_monitor_task = asyncio.create_task(self._ack_timeout_loop())
        logger.info(f"QueueNode {self.node_id} started.")

    async def stop(self) -> None:
        """Stops the proxy and cleans up resources."""
        if self._ack_monitor_task:
            self._ack_monitor_task.cancel()
        if self.redis:
            await self.redis.close()
        await super().stop()

    async def _forward_request(self, method: str, url: str, **kwargs) -> Any:
        """Utility to forward requests to other nodes with error handling."""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.request(method, url, **kwargs) as response:
                    if response.status >= 400:
                        err = await response.text()
                        logger.error(f"Forward request failed with {response.status}: {err}")
                        return None
                    return await response.json()
        except Exception as e:
            logger.error(f"Proxy request to {url} failed", exc_info=e)
            return None

    # --- Core Queue Logic ---

    async def produce(self, queue_name: str, payload: Dict[str, Any], priority: int = 0) -> str:
        """Handles producing a message to the specified queue."""
        msg = Message(queue_name=queue_name, payload=payload)
        target_nodes = self.ring.get_nodes(queue_name, n=2)

        for target_id in target_nodes:
            if target_id == self.node_id:
                # Local persistence
                await self.redis.lpush(f"queue:{queue_name}", json.dumps(msg.to_dict()))
                return msg.message_id
            elif target_id in self.peers:
                # Forward to responsible peer
                peer_url = f"{self.peers[target_id]}/queues/{queue_name}/messages"
                res = await self._forward_request('POST', peer_url, json={"payload": payload, "priority": priority})
                if res and "message_id" in res:
                    return res["message_id"]
        
        # Fallback to local if all peers fail
        logger.warning(f"All target nodes failed for {queue_name}. Falling back to local storage.")
        await self.redis.lpush(f"queue:{queue_name}", json.dumps(msg.to_dict()))
        return msg.message_id

    async def consume(self, queue_name: str, consumer_id: str, timeout: int = 5) -> Optional[Message]:
        """Handles consuming a message from the specified queue (blocks up to timeout)."""
        target_nodes = self.ring.get_nodes(queue_name, n=2)

        if target_nodes[0] != self.node_id and target_nodes[0] in self.peers:
            peer_url = f"{self.peers[target_nodes[0]]}/queues/{queue_name}/messages/next?consumer_id={consumer_id}&timeout={timeout}"
            res = await self._forward_request('GET', peer_url)
            if res and "message_id" in res:
                return Message.from_dict(res)
            # If primary peer fails or times out, try secondary or fallback local

        # Determine responsible or fallback locally
        result = await self.redis.brpop([f"queue:{queue_name}"], timeout=timeout)
        if not result:
            return None

        _, raw_msg = result
        msg = Message.from_dict(json.loads(raw_msg))
        
        msg.delivery_count += 1
        msg.status = MessageStatus.DELIVERED
        
        inflight_data = {
            "message": msg.to_dict(),
            "consumer_id": consumer_id,
            "fetched_at": time.time()
        }
        await self.redis.hset(f"inflight:{queue_name}", msg.message_id, json.dumps(inflight_data))
        
        return msg

    async def acknowledge(self, message_id: str, consumer_id: str) -> bool:
        """Acknowledges successful processing of a message."""
        cursor = 0
        while True:
            cursor, keys = await self.redis.scan(
                cursor=cursor, match="inflight:*", count=100
            )
            for inflight_key in keys:
                raw_inflight = await self.redis.hget(inflight_key, message_id)
                if raw_inflight:
                    await self.redis.hdel(inflight_key, message_id)
                    return True
            if cursor == 0:
                break
        return False


    async def reject(self, message_id: str, consumer_id: str, requeue: bool = True) -> bool:
        """Rejects a message, optionally requeuing it or moving to a DLQ."""
        cursor = 0
        while True:
            cursor, keys = await self.redis.scan(
                cursor=cursor, match="inflight:*", count=100
            )
            for inflight_key in keys:
                raw_inflight = await self.redis.hget(inflight_key, message_id)
                if raw_inflight:
                    inflight_data = json.loads(raw_inflight)
                    msg = Message.from_dict(inflight_data["message"])
                    queue_name = msg.queue_name

                    await self.redis.hdel(inflight_key, message_id)

                    if requeue and not msg.is_dead_letter():
                        msg.status = MessageStatus.PENDING
                        await self.redis.rpush(
                            f"queue:{queue_name}", json.dumps(msg.to_dict())
                        )
                    else:
                        msg.status = MessageStatus.DEAD
                        await self.redis.rpush(
                            f"dlq:{queue_name}", json.dumps(msg.to_dict())
                        )
                    return True
            if cursor == 0:
                break
        return False

    async def get_queue_stats(self, queue_name: str) -> Dict[str, Any]:
        """Returns statistics for a specific queue."""
        # Route to owner node if not us
        target_id = self.ring.get_node(queue_name)
        if target_id != self.node_id and target_id in self.peers:
            url = f"{self.peers[target_id]}/queues/{queue_name}/stats"
            res = await self._forward_request('GET', url)
            if res:
                return res

        pending = await self.redis.llen(f"queue:{queue_name}")
        in_flight = await self.redis.hlen(f"inflight:{queue_name}")
        dlq = await self.redis.llen(f"dlq:{queue_name}")

        return {
            "queue_name": queue_name,
            "pending_count": pending,
            "in_flight_count": in_flight,
            "dlq_count": dlq,
            "throughput_per_sec": 0  # Requires sliding window metrics over time
        }

    async def _recover_in_flight(self) -> None:
        """Re-queues messages that were stuck in-flight (e.g. from a node crash)."""
        cursor = "0"
        recovered = 0
        while True:
            cursor, keys = await self.redis.scan(cursor=cursor, match="inflight:*", count=100)
            for inflight_key in keys:
                queue_name = inflight_key.split("inflight:")[1]
                fields = await self.redis.hgetall(inflight_key)
                for msg_id, raw_data in fields.items():
                    data = json.loads(raw_data)
                    msg = Message.from_dict(data["message"])
                    msg.status = MessageStatus.PENDING
                    await self.redis.rpush(f"queue:{queue_name}", json.dumps(msg.to_dict()))
                    await self.redis.hdel(inflight_key, msg_id)
                    recovered += 1
        
        if recovered > 0:
            logger.info(f"Node {self.node_id} recovered {recovered} in-flight messages on startup.")

    async def _process_ack_timeouts(self) -> None:
        """Scans in-flight hashes for timed-out messages."""
        now = time.time()
        cursor = "0"
        while True:
            cursor, keys = await self.redis.scan(cursor=cursor, match="inflight:*", count=100)
            for inflight_key in keys:
                queue_name = inflight_key.split("inflight:")[1]
                fields = await self.redis.hgetall(inflight_key)
                
                for msg_id, raw_data in fields.items():
                    data = json.loads(raw_data)
                    fetched_at = data.get("fetched_at", 0)
                    
                    if now - fetched_at > self.ack_timeout_seconds:
                        # Timed out, remove from inflight
                        await self.redis.hdel(inflight_key, msg_id)
                        
                        msg = Message.from_dict(data["message"])
                        if msg.is_dead_letter():
                            msg.status = MessageStatus.DEAD
                            await self.redis.rpush(f"dlq:{queue_name}", json.dumps(msg.to_dict()))
                            logger.warning(f"Message {msg_id} moved to DLQ (max deliveries).")
                        else:
                            msg.status = MessageStatus.PENDING
                            await self.redis.rpush(f"queue:{queue_name}", json.dumps(msg.to_dict()))
                            logger.info(f"Message {msg_id} requeued (ACK timeout).")

    async def _ack_timeout_loop(self) -> None:
        """Background loop continuously scanning for ACK timeouts."""
        try:
            while self.state == NodeState.RUNNING:
                await asyncio.sleep(5.0)
                await self._process_ack_timeouts()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Error in ACK timeout loop on {self.node_id}", exc_info=e)

    # --- REST API Handlers ---

    async def process_message(self, message: Any) -> None:
        """Implement BaseNode abstract method. Handled largely by REST in this subsystem."""
        pass

    async def api_produce(self, request: web.Request) -> web.Response:
        """POST /queues/{queue_name}/messages"""
        queue_name = request.match_info['queue_name']
        try:
            data = await request.json()
            payload = data.get("payload", {})
            priority = data.get("priority", 0)
            
            msg_id = await self.produce(queue_name, payload, priority)
            return web.json_response({"message_id": msg_id, "status": "accepted"}, status=201)
        except Exception as e:
            logger.error(f"Produce API error", exc_info=e)
            return web.json_response({"error": str(e)}, status=400)

    async def api_consume(self, request: web.Request) -> web.Response:
        """GET /queues/{queue_name}/messages/next"""
        queue_name = request.match_info['queue_name']
        consumer_id = request.query.get("consumer_id", f"consumer-{uuid.uuid4().hex[:8]}")
        timeout = int(request.query.get("timeout", 5))
        
        try:
            msg = await self.consume(queue_name, consumer_id, timeout)
            if msg:
                return web.json_response(msg.to_dict(), status=200)
            else:
                return web.json_response({"message": "Queue empty"}, status=204)
        except Exception as e:
            logger.error(f"Consume API error", exc_info=e)
            return web.json_response({"error": str(e)}, status=500)

    async def api_ack(self, request: web.Request) -> web.Response:
        """POST /queues/messages/{message_id}/ack"""
        message_id = request.match_info['message_id']
        data = await request.json()
        consumer_id = data.get("consumer_id", "")
        
        success = await self.acknowledge(message_id, consumer_id)
        if success:
            return web.json_response({"status": "acknowledged"}, status=200)
        else:
            return web.json_response({"error": "Message not found in-flight"}, status=404)

    async def api_reject(self, request: web.Request) -> web.Response:
        """POST /queues/messages/{message_id}/reject"""
        message_id = request.match_info['message_id']
        data = await request.json()
        consumer_id = data.get("consumer_id", "")
        requeue = data.get("requeue", True)
        
        success = await self.reject(message_id, consumer_id, requeue)
        if success:
            return web.json_response({"status": "rejected"}, status=200)
        else:
            return web.json_response({"error": "Message not found in-flight"}, status=404)

    async def api_stats(self, request: web.Request) -> web.Response:
        """GET /queues/{queue_name}/stats"""
        queue_name = request.match_info['queue_name']
        stats = await self.get_queue_stats(queue_name)
        return web.json_response(stats, status=200)