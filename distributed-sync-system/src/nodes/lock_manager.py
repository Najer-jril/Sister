# src/nodes/lock_manager.py
import asyncio
import json
import logging
import time
import uuid
from enum import Enum, auto
from dataclasses import dataclass, field
from typing import Dict, Any, List, Set, Tuple, Optional

from aiohttp import web
from src.consensus.raft import RaftNode, Role

logger = logging.getLogger(__name__)

class LockType(Enum):
    """Enumeration representing the type of lock."""
    SHARED = "SHARED"
    EXCLUSIVE = "EXCLUSIVE"

@dataclass
class LockState:
    """Dataclass representing the current state of a lock."""
    lock_id: str
    resource_id: str
    lock_type: LockType
    holder_ids: Set[str] = field(default_factory=set)
    acquired_at: float = field(default_factory=time.time)
    timeout_seconds: float = 30.0

    def is_expired(self) -> bool:
        """
        Checks if the lock has expired based on its acquired time and timeout.
        
        Returns:
            bool: True if expired, False otherwise.
        """
        if self.timeout_seconds <= 0:
            return False
        return time.time() > (self.acquired_at + self.timeout_seconds)

    def to_dict(self) -> Dict[str, Any]:
        """Serializes the lock state to a dictionary."""
        return {
            "lock_id": self.lock_id,
            "resource_id": self.resource_id,
            "lock_type": self.lock_type.value,
            "holder_ids": list(self.holder_ids),
            "acquired_at": self.acquired_at,
            "timeout_seconds": self.timeout_seconds
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "LockState":
        """Deserializes a dictionary into a LockState."""
        return cls(
            lock_id=data["lock_id"],
            resource_id=data["resource_id"],
            lock_type=LockType(data["lock_type"]),
            holder_ids=set(data.get("holder_ids", [])),
            acquired_at=data.get("acquired_at", time.time()),
            timeout_seconds=data.get("timeout_seconds", 30.0)
        )

class LockManager(RaftNode):
    """
    Distributed Lock Manager built on top of Raft consensus.
    Provides shared/exclusive locking, deadlock detection, and lock expiration.
    """
    def __init__(self, node_id: str, host: str, port: int, peer_urls: List[str], redis_url: str) -> None:
        """
        Initializes the LockManager node.

        Args:
            node_id: Unique identifier for the node.
            host: HTTP host to bind.
            port: HTTP port to bind.
            peer_urls: List of peer node HTTP URLs.
            redis_url: Redis connection string for persistent state.
        """
        super().__init__(node_id, host, port, peer_urls, redis_url)
        
        # Add REST API routes for locks
        self._app.router.add_post('/locks/acquire', self.api_acquire_lock)
        self._app.router.add_delete('/locks/{lock_id}', self.api_release_lock)
        self._app.router.add_get('/locks/{resource_id}/status', self.api_get_status)
        self._app.router.add_get('/locks/deadlocks', self.api_get_deadlocks)

        self._deadlock_task: Optional[asyncio.Task] = None
        self._expiry_task: Optional[asyncio.Task] = None
        self._wait_events: Dict[str, List[asyncio.Event]] = {}
        self._wait_graph: Dict[str, Set[str]] = {}  # holder_id -> waiters

    async def start(self) -> None:
        """Starts the Lock Manager, including background tasks for deadlocks and expiry."""
        await super().start()
        self._deadlock_task = asyncio.create_task(self._deadlock_loop())
        # self._expiry_task = asyncio.create_task(self._expiry_loop())
        logger.info(f"LockManager {self.node_id} started.")

    async def stop(self) -> None:
        """Stops the Lock Manager and clean up tasks."""
        if self._deadlock_task:
            self._deadlock_task.cancel()
        if self._expiry_task:
            self._expiry_task.cancel()
        await super().stop()

    async def _redis_get_lock(self, resource_id: str) -> Optional[LockState]:
        """Retrieves a lock state from Redis."""
        data = await self.redis.hget("locks", resource_id)
        if data:
            return LockState.from_dict(json.loads(data))
        return None

    async def _redis_set_lock(self, resource_id: str, state: LockState) -> None:
        """Saves a lock state to Redis."""
        await self.redis.hset("locks", resource_id, json.dumps(state.to_dict()))

    async def _redis_delete_lock(self, resource_id: str) -> None:
        """Deletes a lock state from Redis."""
        await self.redis.hdel("locks", resource_id)

    async def execute_command(self, command: Dict[str, Any]) -> Any:
        """
        Overrides RaftNode state machine execution to handle lock commands.
        Pengecekan konflik dilakukan di sini agar bersifat atomik dalam log Raft.
        
        Args:
            command: The command dictionary applied from the Raft log.
            
        Returns:
            Any: The result of the operation (Success dict or Conflict error).
        """
        op = command.get("op")
        resource_id = command.get("resource_id")

        if op == "ACQUIRE_LOCK":
            new_state_dict = command["state"]
            holder_id = command["holder_id"]
            
            current_lock = await self._redis_get_lock(resource_id)
            
            if current_lock and not current_lock.is_expired():
                if current_lock.lock_type == LockType.EXCLUSIVE:
                    return {
                        "success": False, 
                        "error": "CONFLICT", 
                        "message": "Resource is locked exclusively"
                    }
                
                if new_state_dict["lock_type"] == "EXCLUSIVE":
                    return {
                        "success": False, 
                        "error": "CONFLICT", 
                        "message": "Resource is locked shared, exclusive denied"
                    }
                
                if new_state_dict["lock_type"] == "SHARED" and current_lock.lock_type == LockType.SHARED:
                    current_lock.holder_ids.add(holder_id)
                    await self._redis_set_lock(resource_id, current_lock)
                    self._notify_waiters(resource_id)
                    return {"success": True, "lock_id": current_lock.lock_id}
            
            state = LockState.from_dict(new_state_dict)
            await self._redis_set_lock(resource_id, state)
            self._notify_waiters(resource_id)
            return {"success": True, "lock_id": state.lock_id}

        elif op == "RELEASE_LOCK":
            resource_id = command["resource_id"]
            remaining_holders = command["remaining_holders"]
            
            if remaining_holders:
                state = await self._redis_get_lock(resource_id)
                if state:
                    state.holder_ids = set(remaining_holders)
                    await self._redis_set_lock(resource_id, state)
            else:
                await self._redis_delete_lock(resource_id)
            
            self._notify_waiters(resource_id)
            return "OK"
            
        else:
            return await super().execute_command(command)

    async def _submit_to_raft(self, command: Dict[str, Any]) -> bool:
        if self.role != Role.LEADER:
            return False
            
        term = await self._get_current_term()
        index = await self._append_log_entry(term, command)
        
        result = await self.execute_command(command)
        
        self.commit_index = index
        self.last_applied = index
        return result

    def _notify_waiters(self, resource_id: str) -> None:
        """Wakes up any tasks waiting on a specific resource."""
        if resource_id in self._wait_events:
            for event in self._wait_events[resource_id]:
                event.set()

    async def try_lock(self, resource_id: str, lock_type: LockType, holder_id: str, timeout: float = 30.0) -> Dict[str, Any]:
        """
        Attempts to acquire a lock by submitting a command to Raft.
        The actual conflict validation happens in the state machine (execute_command).

        Args:
            resource_id: Identifier of the resource to lock.
            lock_type: SHARED or EXCLUSIVE.
            holder_id: Identifier of the entity requesting the lock.
            timeout: Maximum duration the lock should be held.

        Returns:
            Dict containing success status and lock details or error message.
        """
        if self.role != Role.LEADER:
            return {"success": False, "error": "Not the leader", "leader_id": self.leader_id}

        new_state = LockState(
            lock_id=str(uuid.uuid4()),
            resource_id=resource_id,
            lock_type=lock_type,
            holder_ids={holder_id},
            acquired_at=time.time(),
            timeout_seconds=timeout
        )

        cmd = {
            "op": "ACQUIRE_LOCK",
            "resource_id": resource_id,
            "holder_id": holder_id,
            "state": new_state.to_dict()
        }
        
        result = await self._submit_to_raft(cmd)

        if isinstance(result, dict):
            return result
        
        if result is True:
            return {"success": True, "lock_id": new_state.lock_id, "holder_id": holder_id}
        else:
            return {"success": False, "error": "Failed to replicate lock operation"}

    async def acquire_lock(self, resource_id: str, lock_type: LockType, holder_id: str, timeout: float = 30.0) -> Dict[str, Any]:
        """
        Attempts to acquire a lock, blocking if necessary until available or timeout is reached.

        Args:
            resource_id: Identifier of the resource to lock.
            lock_type: SHARED or EXCLUSIVE.
            holder_id: Identifier of the entity requesting the lock.
            timeout: Max time in seconds to wait for the lock.

        Returns:
            Dict containing success, lock_id, and optional error.
        """
        start_time = time.time()
        
        # Wait-for graph tracking for deadlock detection
        if holder_id not in self._wait_graph:
            self._wait_graph[holder_id] = set()

        while True:
            # Time out check
            if time.time() - start_time > timeout:
                return {"success": False, "error": "Timeout waiting for lock"}

            result = await self.try_lock(resource_id, lock_type, holder_id)
            if result["success"]:
                # Remove from deadlock wait graph if acquired
                if holder_id in self._wait_graph:
                    del self._wait_graph[holder_id]
                return result

            if result.get("error") == "Not the leader":
                return result

            # Register waiting relation for deadlock detector
            current_lock = await self._redis_get_lock(resource_id)
            if current_lock:
                self._wait_graph[holder_id].update(current_lock.holder_ids)

            # Wait for changes
            event = asyncio.Event()
            if resource_id not in self._wait_events:
                self._wait_events[resource_id] = []
            self._wait_events[resource_id].append(event)
            
            try:
                # Sleep briefly or wait for event wake up before retrying
                await asyncio.wait_for(event.wait(), timeout=1.0)
            except asyncio.TimeoutError:
                pass
            finally:
                if event in self._wait_events.get(resource_id, []):
                    self._wait_events[resource_id].remove(event)
                    if not self._wait_events[resource_id]:
                        del self._wait_events[resource_id]

    async def release_lock(self, lock_id: str, holder_id: str) -> bool:
        """
        Releases a lock held by a specific holder.

        Args:
            lock_id: The UUID of the lock.
            holder_id: The identifier of the holder releasing it.

        Returns:
            bool: True if released, False if not found or unauthorized.
        """
        if self.role != Role.LEADER:
            return False

        # Find lock linearly for now (Redis hash mapping is resource_id -> state, so requires iterate or secondary index)
        # Given this is a manager, we search the keys
        keys = await self.redis.hkeys("locks")
        for key in keys:
            data = await self.redis.hget("locks", key)
            if not data:
                continue
            state = LockState.from_dict(json.loads(data))
            
            if state.lock_id == lock_id:
                if holder_id not in state.holder_ids:
                    return False # Unauthorized
                
                remaining = list(state.holder_ids - {holder_id})
                cmd = {
                    "op": "RELEASE_LOCK",
                    "resource_id": state.resource_id,
                    "remaining_holders": remaining
                }
                
                return await self._submit_to_raft(cmd)
                
        return False

    async def get_lock_status(self, resource_id: str) -> Dict[str, Any]:
        print(f"Checking status for: {resource_id}") # Debug terminal
        state = await self._redis_get_lock(resource_id)
        
        if state:
            print(f"Found state in Redis: {state}")
            return state.to_dict()
        
        print("No state found in Redis for this resource.")
        return {"status": "not_found", "message": f"Resource {resource_id} is not locked"}

    def _detect_deadlock(self) -> List[Tuple[str, str]]:
        """
        Builds a wait-for graph and detects cycles using DFS to identify deadlocks.
        
        Returns:
            List[Tuple]: List of deadlocked (waiter_id, resource_holder_id) pairs.
        """
        visited = set()
        stack = set()
        deadlocks = []

        def dfs(node: str, path: List[str]) -> None:
            visited.add(node)
            stack.add(node)
            path.append(node)

            for neighbor in self._wait_graph.get(node, []):
                if neighbor not in visited:
                    dfs(neighbor, path)
                elif neighbor in stack:
                    # Cycle detected
                    cycle_start = path.index(neighbor)
                    cycle_nodes = path[cycle_start:]
                    for i in range(len(cycle_nodes)):
                        deadlocks.append((cycle_nodes[i], cycle_nodes[(i + 1) % len(cycle_nodes)]))

            stack.remove(node)
            path.pop()

        for node in list(self._wait_graph.keys()):
            if node not in visited:
                dfs(node, [])

        return deadlocks

    async def _deadlock_loop(self) -> None:
        """Background task running every 5 seconds to detect deadlocks."""
        try:
            while True:
                await asyncio.sleep(5.0)
                if self.role == Role.LEADER:
                    deadlocks = self._detect_deadlock()
                    if deadlocks:
                        logger.warning(f"Deadlocks detected: {deadlocks}")
                        # In a full system, you would preempt one of the locks.
                        # For now, we detect and log.
        except asyncio.CancelledError:
            pass

    async def _expire_locks(self) -> int:
        """
        Iterates over all locks and releases expired ones.
        
        Returns:
            int: The number of locks automatically expired.
        """
        if self.role != Role.LEADER:
            return 0

        expired_count = 0
        keys = await self.redis.hkeys("locks")
        
        for key in keys:
            data = await self.redis.hget("locks", key)
            if not data:
                continue
                
            state = LockState.from_dict(json.loads(data))
            if state.is_expired():
                # Release all holders
                cmd = {
                    "op": "RELEASE_LOCK",
                    "resource_id": state.resource_id,
                    "remaining_holders": []
                }
                success = await self._submit_to_raft(cmd)
                if success:
                    expired_count += 1
                    logger.info(f"Auto-expired lock on resource {state.resource_id}")

        return expired_count

    async def _expiry_loop(self) -> None:
        """Background task running to check for expired locks."""
        try:
            while True:
                await asyncio.sleep(2.0)
                if self.role == Role.LEADER:
                    await self._expire_locks()
        except asyncio.CancelledError:
            pass

    # --- REST API Endpoints ---
    async def api_acquire_lock(self, request: web.Request) -> web.Response:
        """HTTP POST endpoint to acquire a lock."""
        if self.role != Role.LEADER:
            return web.json_response({"error": "Service Unavailable: Not Raft Leader", "leader_id": self.leader_id}, status=503)

        try:
            data = await request.json()
            resource_id = data["resource_id"]
            lock_type = LockType(data.get("lock_type", "EXCLUSIVE"))
            holder_id = data["holder_id"]
            timeout = data.get("timeout", 30.0)

            result = await self.acquire_lock(resource_id, lock_type, holder_id, timeout)
            
            if result.get("success"):
                return web.json_response(result, status=200)
            else:
                return web.json_response(result, status=409)  # Conflict

        except KeyError as e:
            return web.json_response({"error": f"Missing required field: {str(e)}"}, status=400)
        except Exception as e:
            logger.error(f"Error acquiring lock", exc_info=e)
            return web.json_response({"error": str(e)}, status=500)

    async def api_release_lock(self, request: web.Request) -> web.Response:
        """HTTP DELETE endpoint to release a lock."""
        if self.role != Role.LEADER:
            return web.json_response({"error": "Service Unavailable: Not Raft Leader", "leader_id": self.leader_id}, status=503)

        lock_id = request.match_info['lock_id']
        holder_id = request.query.get('holder_id')
        
        if not holder_id:
            return web.json_response({"error": "Missing holder_id in query params"}, status=400)

        success = await self.release_lock(lock_id, holder_id)
        if success:
            return web.json_response({"status": "success"}, status=200)
        else:
            return web.json_response({"error": "Failed to release lock, unauthorized or not found"}, status=400)

    async def api_get_status(self, request: web.Request) -> web.Response:
        """HTTP GET endpoint for checking a resource's lock status."""
        resource_id = request.match_info['resource_id']
        status = await self.get_lock_status(resource_id)
        return web.json_response(status, status=200)

    async def api_get_deadlocks(self, request: web.Request) -> web.Response:
        """HTTP GET endpoint to view currently detected deadlocks."""
        if self.role != Role.LEADER:
            return web.json_response({"error": "Service Unavailable: Not Raft Leader", "leader_id": self.leader_id}, status=503)

        deadlocks = self._detect_deadlock()
        formatted = [{"waiter": pair[0], "holder": pair[1]} for pair in deadlocks]
        return web.json_response({"deadlocks": formatted}, status=200)