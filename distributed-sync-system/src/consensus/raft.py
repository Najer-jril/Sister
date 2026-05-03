# src/consensus/raft.py
import asyncio
import json
import logging
import random
import time
from enum import Enum, auto
from typing import List, Dict, Any, Optional
from aiohttp import web

from redis.asyncio import Redis

from src.nodes.base_node import BaseNode, NodeState
from src.communication.message_passing import Message, MessageType

logger = logging.getLogger(__name__)

class Role(Enum):
    FOLLOWER = auto()
    CANDIDATE = auto()
    LEADER = auto()

class RaftNode(BaseNode):
    """
    Complete Raft consensus implementation.
    Manages leader election, log replication, and a Redis-backed state machine.
    """
    def __init__(self, node_id: str, host: str, port: int, peer_urls: List[str], redis_url: str) -> None:
        super().__init__(node_id, host, port, heartbeat_interval=0.05)
        self.peer_urls = peer_urls
        self.redis_url = redis_url
        self.redis: Optional[Redis] = None
        
        self._app.router.add_get('/raft/status', self.api_raft_status)

        # Volatile state
        self.role: Role = Role.FOLLOWER
        self.leader_id: Optional[str] = None
        self.commit_index: int = 0
        self.last_applied: int = 0
        
        # Leader-only volatile state mapping peer_url to index
        self.next_index: Dict[str, int] = {}
        self.match_index: Dict[str, int] = {}
        
        # Timing constants
        self.election_timeout_min = 0.150  # 150ms
        self.election_timeout_max = 0.300  # 300ms
        self.heartbeat_interval = 0.050    # 50ms
        
        # Internal state
        self._election_timer_task: Optional[asyncio.Task] = None
        self._leader_loop_task: Optional[asyncio.Task] = None
        self._last_heartbeat_received: float = time.time()
        self._votes_received: int = 0

    async def start(self) -> None:
        """Starts the Raft node, initializing Redis and background tasks."""
        self.redis = Redis.from_url(self.redis_url, decode_responses=True)
        await super().start()
        self._reset_election_timer()

    async def stop(self) -> None:
        """Stops the Raft node and cleans up resources."""
        if self._election_timer_task:
            self._election_timer_task.cancel()
        if self._leader_loop_task:
            self._leader_loop_task.cancel()
        if self.redis:
            await self.redis.close()
        await super().stop()

    # --- Persistent State (Redis) ---
    async def _get_current_term(self) -> int:
        val = await self.redis.get(f"{self.node_id}:currentTerm")
        return int(val) if val else 0

    async def _set_current_term(self, term: int) -> None:
        await self.redis.set(f"{self.node_id}:currentTerm", term)

    async def _get_voted_for(self) -> Optional[str]:
        return await self.redis.get(f"{self.node_id}:votedFor")

    async def _set_voted_for(self, candidate_id: Optional[str]) -> None:
        if candidate_id:
            await self.redis.set(f"{self.node_id}:votedFor", candidate_id)
        else:
            await self.redis.delete(f"{self.node_id}:votedFor")

    async def _get_log_len(self) -> int:
        return await self.redis.llen(f"{self.node_id}:log")

    async def _get_log_entry(self, index: int) -> Optional[Dict[str, Any]]:
        # Raft uses 1-based indexing typically, but list is 0-based
        if index < 1:
            return None
        val = await self.redis.lindex(f"{self.node_id}:log", index - 1)
        return json.loads(val) if val else None

    async def _get_last_log_index(self) -> int:
        return await self._get_log_len()

    async def _get_last_log_term(self) -> int:
        last_idx = await self._get_last_log_index()
        if last_idx == 0:
            return 0
        entry = await self._get_log_entry(last_idx)
        return entry["term"] if entry else 0

    async def _append_log_entry(self, term: int, command: Dict[str, Any]) -> int:
        entry = json.dumps({"term": term, "command": command})
        await self.redis.rpush(f"{self.node_id}:log", entry)
        return await self._get_log_len()

    async def _truncate_log(self, from_index: int) -> None:
        """Truncates the log to keep only entries up to from_index - 1."""
        if from_index <= 1:
            await self.redis.delete(f"{self.node_id}:log")
        else:
            await self.redis.ltrim(f"{self.node_id}:log", 0, from_index - 2)

    # --- Timers and Elections ---
    def _reset_election_timer(self) -> None:
        if self._election_timer_task:
            self._election_timer_task.cancel()
        
        timeout = random.uniform(self.election_timeout_min, self.election_timeout_max)
        self._last_heartbeat_received = time.time()
        self._election_timer_task = asyncio.create_task(self._election_timer_loop(timeout))

    async def _election_timer_loop(self, timeout: float) -> None:
        try:
            await asyncio.sleep(timeout)
            if self.role != Role.LEADER:
                await self.start_election()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Error in election timer on {self.node_id}", exc_info=e)

    async def start_election(self) -> None:
        self.role = Role.CANDIDATE
        self.leader_id = None
        current_term = await self._get_current_term() + 1
        await self._set_current_term(current_term)
        await self._set_voted_for(self.node_id)
        self._votes_received = 1
        
        logger.info(f"Node {self.node_id} starting election for term {current_term}")
        self._reset_election_timer()

        last_log_index = await self._get_last_log_index()
        last_log_term = await self._get_last_log_term()

        payload = {
            "action": "RequestVote",
            "term": current_term,
            "candidateId": self.node_id,
            "lastLogIndex": last_log_index,
            "lastLogTerm": last_log_term
        }

        # Broadcast RequestVote
        msg = Message(type=MessageType.REQUEST, sender=self.node_id, receiver="ALL", payload=payload)
        for url in self.peer_urls:
            asyncio.create_task(self.send_message(msg, url))

    # --- Message Processing ---
    async def process_message(self, message: Message) -> None:
        payload = message.payload
        action = payload.get("action")
        
        if action == "RequestVote":
            response = await self.request_vote(
                term=payload["term"],
                candidateId=payload["candidateId"],
                lastLogIndex=payload["lastLogIndex"],
                lastLogTerm=payload["lastLogTerm"]
            )
            reply = Message(type=MessageType.RESPONSE, sender=self.node_id, receiver=message.sender, payload=response)
            # Naive: assuming message.sender has a predictable URL or we can extract it. 
            # In reality, MessageBus needs to route to the correct URL.
            # We omit direct reply URL mapping here, assuming target_url is handled by sender's domain.
            # We use an internal queue approach for responses by checking sender ID if peered.
            peer_url = next((url for url in self.peer_urls if message.sender in url), None)
            if peer_url:
                await self.send_message(reply, peer_url)

        elif action == "RequestVoteResponse":
            await self._handle_vote_response(payload["term"], payload["voteGranted"])

        elif action == "AppendEntries":
            response = await self.append_entries(
                term=payload["term"],
                leaderId=payload["leaderId"],
                prevLogIndex=payload["prevLogIndex"],
                prevLogTerm=payload["prevLogTerm"],
                entries=payload["entries"],
                leaderCommit=payload["leaderCommit"]
            )
            response["action"] = "AppendEntriesResponse"
            response["peer_url"] = next((url for url in self.peer_urls if self.node_id in url), self.host) # Self identifying for leader
            
            peer_url = next((url for url in self.peer_urls if message.sender in url), None)
            if peer_url:
                reply = Message(type=MessageType.RESPONSE, sender=self.node_id, receiver=message.sender, payload=response)
                await self.send_message(reply, peer_url)

        elif action == "AppendEntriesResponse":
            await self._handle_append_entries_response(
                sender_url=payload.get("peer_url"),
                term=payload["term"],
                success=payload["success"],
                match_index=payload.get("matchIndex", 0)
            )

    # --- Core RPC Methods ---
    async def request_vote(self, term: int, candidateId: str, lastLogIndex: int, lastLogTerm: int) -> Dict[str, Any]:
        """Handles an incoming RequestVote RPC."""
        current_term = await self._get_current_term()
        
        if term > current_term:
            await self._set_current_term(term)
            self.role = Role.FOLLOWER
            await self._set_voted_for(None)
            self.leader_id = None
            
        voted_for = await self._get_voted_for()
        my_last_log_term = await self._get_last_log_term()
        my_last_log_index = await self._get_last_log_index()

        log_is_up_to_date = (lastLogTerm > my_last_log_term) or \
                            (lastLogTerm == my_last_log_term and lastLogIndex >= my_last_log_index)

        vote_granted = False
        if term == current_term and (voted_for is None or voted_for == candidateId) and log_is_up_to_date:
            vote_granted = True
            await self._set_voted_for(candidateId)
            self._reset_election_timer()

        return {
            "action": "RequestVoteResponse",
            "term": current_term,
            "voteGranted": vote_granted
        }

    async def _handle_vote_response(self, term: int, vote_granted: bool) -> None:
        current_term = await self._get_current_term()
        if term > current_term:
            await self._set_current_term(term)
            self.role = Role.FOLLOWER
            await self._set_voted_for(None)
            self._reset_election_timer()
            return

        if self.role == Role.CANDIDATE and term == current_term and vote_granted:
            self._votes_received += 1
            majority = (len(self.peer_urls) + 1) // 2 + 1
            if self._votes_received >= majority:
                await self._become_leader()

    async def _become_leader(self) -> None:
        logger.info(f"Node {self.node_id} became LEADER for term {await self._get_current_term()}")
        self.role = Role.LEADER
        self.leader_id = self.node_id
        
        if self._election_timer_task:
            self._election_timer_task.cancel()

        last_log_index = await self._get_last_log_index()
        for url in self.peer_urls:
            self.next_index[url] = last_log_index + 1
            self.match_index[url] = 0

        # Send immediate heartbeat
        await self.send_heartbeats()
        
        # Start leader heartbeat/append loop
        if self._leader_loop_task:
            self._leader_loop_task.cancel()
        self._leader_loop_task = asyncio.create_task(self._leader_loop())

    async def append_entries(self, term: int, leaderId: str, prevLogIndex: int, prevLogTerm: int, entries: List[Dict[str, Any]], leaderCommit: int) -> Dict[str, Any]:
        """Handles an incoming AppendEntries RPC."""
        current_term = await self._get_current_term()

        if term < current_term:
            return {"term": current_term, "success": False}

        self._reset_election_timer()

        if term > current_term or self.role != Role.FOLLOWER:
            await self._set_current_term(term)
            self.role = Role.FOLLOWER
            await self._set_voted_for(None)
        
        self.leader_id = leaderId

        # Reply false if log doesn’t contain an entry at prevLogIndex whose term matches prevLogTerm
        if prevLogIndex > 0:
            my_last_index = await self._get_last_log_index()
            if prevLogIndex > my_last_index:
                return {"term": await self._get_current_term(), "success": False}
            prev_entry = await self._get_log_entry(prevLogIndex)
            if prev_entry and prev_entry["term"] != prevLogTerm:
                return {"term": await self._get_current_term(), "success": False}

        # Truncate conflicts and append new entries
        if entries:
            idx = prevLogIndex
            for entry in entries:
                idx += 1
                existing = await self._get_log_entry(idx)
                if existing and existing["term"] != entry["term"]:
                    await self._truncate_log(idx)
                    existing = None
                if not existing:
                    await self._append_log_entry(entry["term"], entry["command"])

        if leaderCommit > self.commit_index:
            last_new_index = prevLogIndex + len(entries)
            self.commit_index = min(leaderCommit, last_new_index)
            await self.apply_committed_entries()

        return {
            "term": await self._get_current_term(),
            "success": True,
            "matchIndex": prevLogIndex + len(entries)
        }

    async def _handle_append_entries_response(self, sender_url: str, term: int, success: bool, match_index: int) -> None:
        if not sender_url or self.role != Role.LEADER:
            return

        current_term = await self._get_current_term()
        if term > current_term:
            await self._set_current_term(term)
            self.role = Role.FOLLOWER
            await self._set_voted_for(None)
            self._reset_election_timer()
            return

        if success:
            self.next_index[sender_url] = match_index + 1
            self.match_index[sender_url] = match_index
            await self._update_commit_index()
        else:
            # Decrement nextIndex and retry (done implicitly on next tick)
            if self.next_index[sender_url] > 1:
                self.next_index[sender_url] -= 1

    async def _update_commit_index(self) -> None:
        if self.role != Role.LEADER:
            return
            
        current_term = await self._get_current_term()
        for i in range(self.commit_index + 1, await self._get_last_log_index() + 1):
            entry = await self._get_log_entry(i)
            if entry and entry["term"] == current_term:
                # Count peers that have this replicated
                match_count = 1  # Self
                for url in self.peer_urls:
                    if self.match_index.get(url, 0) >= i:
                        match_count += 1
                majority = (len(self.peer_urls) + 1) // 2 + 1
                if match_count >= majority:
                    self.commit_index = i

        await self.apply_committed_entries()

    # --- Leader Methods ---
    async def _leader_loop(self) -> None:
        try:
            while self.role == Role.LEADER and self.state == NodeState.RUNNING:
                await self.send_heartbeats()
                await asyncio.sleep(self.heartbeat_interval)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Error in leader loop on {self.node_id}", exc_info=e)

    async def send_heartbeats(self) -> None:
        """Sends AppendEntries (heartbeats) to all followers."""
        current_term = await self._get_current_term()
        reachable_peers = 1 # Self is reachable

        tasks = []
        for url in self.peer_urls:
            next_idx = self.next_index.get(url, 1)
            prev_log_idx = next_idx - 1
            prev_log_term = 0
            if prev_log_idx > 0:
                prev_entry = await self._get_log_entry(prev_log_idx)
                if prev_entry:
                    prev_log_term = prev_entry["term"]
            
            # Fetch entries to send
            entries = []
            last_idx = await self._get_last_log_index()
            if last_idx >= next_idx:
                for i in range(next_idx, last_idx + 1):
                    entry = await self._get_log_entry(i)
                    if entry:
                        entries.append(entry)

            payload = {
                "action": "AppendEntries",
                "term": current_term,
                "leaderId": self.node_id,
                "prevLogIndex": prev_log_idx,
                "prevLogTerm": prev_log_term,
                "entries": entries,
                "leaderCommit": self.commit_index
            }
            
            msg = Message(type=MessageType.HEARTBEAT if not entries else MessageType.REQUEST, 
                          sender=self.node_id, receiver="ALL", payload=payload)
            tasks.append(self._send_and_track_reachability(msg, url))

        results = await asyncio.gather(*tasks, return_exceptions=True)
        reachable_peers += sum(1 for res in results if isinstance(res, bool) and res)

        # Network partition check: Step down if lost majority
        majority = (len(self.peer_urls) + 1) // 2 + 1
        if reachable_peers < majority:
            logger.warning(f"Node {self.node_id} lost majority ({reachable_peers}/{len(self.peer_urls)+1}). Stepping down.")
            self.role = Role.FOLLOWER
            self._reset_election_timer()

    async def _send_and_track_reachability(self, msg: Message, url: str) -> bool:
        return await self.send_message(msg, url)

    # --- State Machine & Client APIs ---
    async def apply_committed_entries(self) -> None:
        """Applies committed log entries to the Redis state machine."""
        while self.last_applied < self.commit_index:
            self.last_applied += 1
            entry = await self._get_log_entry(self.last_applied)
            if entry and "command" in entry:
                await self.execute_command(entry["command"])

    async def execute_command(self, command: Dict[str, Any]) -> Any:
        """
        Executes a simple key-value store command backed by Redis.
        Format: {"op": "SET/GET", "key": "k", "value": "v"}
        """
        op = command.get("op")
        key = f"sm_{command.get('key')}"
        if op == "SET":
            await self.redis.set(key, command.get("value"))
            return "OK"
        elif op == "GET":
            val = await self.redis.get(key)
            return val
        return "UNKNOWN_OP"

    async def get_status(self) -> Dict[str, Any]:
        """Returns the full node state dictionary."""
        return {
            "node_id": self.node_id,
            "state": self.state.name,
            "role": self.role.name,
            "current_term": await self._get_current_term(),
            "leader_id": self.leader_id,
            "commit_index": self.commit_index,
            "last_applied": self.last_applied,
            "log_length": await self._get_log_len(),
            "peers": self.peer_urls
        }

    async def api_raft_status(self, request: web.Request) -> web.Response:
        """HTTP GET endpoint for checking the Raft node's status."""
        try:
            status = await self.get_status()
            return web.json_response(status, status=200)
        except Exception as e:
            logger.error("Failed to get raft status", exc_info=e)
            return web.json_response({"error": str(e)}, status=500)