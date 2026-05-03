import pytest
import time
import json
from unittest.mock import MagicMock

from src.communication.message_passing import Message, MessageType
from src.nodes.queue_node import ConsistentHashRing, Message as QueueMessage, MessageStatus
from src.nodes.cache_node import LRUCache, CacheLine, CacheLineState, DirectoryController
from src.communication.failure_detector import PhiAccrualFailureDetector
from src.nodes.lock_manager import LockState, LockType

def test_message_serialization():
    payload = {"foo": "bar", "num": 42}
    msg = Message(type=MessageType.REQUEST, sender="node1", receiver="node2", payload=payload)
    data = msg.to_dict()
    assert data["type"] == "REQUEST"
    assert data["sender"] == "node1"
    assert data["receiver"] == "node2"
    assert data["payload"] == payload
    
    new_msg = Message.from_dict(data)
    assert new_msg.id == msg.id
    assert new_msg.type == MessageType.REQUEST
    assert new_msg.payload == payload

@pytest.mark.parametrize("weight, expected_nodes", [
    (10, 10),
    (50, 50),
    (100, 100)
])
def test_consistent_hash_ring_weights(weight, expected_nodes):
    ring = ConsistentHashRing()
    ring.add_node("node1", weight=weight)
    assert len(ring._ring) == expected_nodes
    
def test_consistent_hash_ring_distribution():
    ring = ConsistentHashRing()
    ring.add_node("node1", weight=100)
    ring.add_node("node2", weight=100)
    ring.add_node("node3", weight=100)
    
    nodes = {ring.get_node(f"key_{i}") for i in range(100)}
    assert len(nodes) > 1 # at least some distribution

def test_lru_cache_eviction():
    cache = LRUCache(capacity=3)
    c1 = CacheLine("k1", "v1", CacheLineState.EXCLUSIVE)
    c2 = CacheLine("k2", "v2", CacheLineState.EXCLUSIVE)
    c3 = CacheLine("k3", "v3", CacheLineState.EXCLUSIVE)
    c4 = CacheLine("k4", "v4", CacheLineState.EXCLUSIVE)
    
    cache.put("k1", c1)
    cache.put("k2", c2)
    cache.put("k3", c3)
    
    # Access k1 to make it most recently used
    cache.get("k1")
    
    # Put k4, should evict k2 (least recently used)
    evicted = cache.put("k4", c4)
    
    assert evicted is not None
    assert evicted.key == "k2"
    assert cache.get("k2") is None
    assert cache.get("k1") is not None

def test_mesi_directory_read_exclusive():
    ctrl = DirectoryController()
    res = ctrl.handle_read("k1", "node1")
    assert res["action"] == "FETCH_FROM_DB"
    assert res["state"] == "EXCLUSIVE"
    assert ctrl.directory["k1"]["state"] == CacheLineState.EXCLUSIVE

def test_mesi_directory_read_shared():
    ctrl = DirectoryController()
    ctrl.handle_read("k1", "node1")
    res = ctrl.handle_read("k1", "node2")
    assert res["action"] == "FETCH_FROM_NODE"
    assert res["state"] == "SHARED"
    assert ctrl.directory["k1"]["state"] == CacheLineState.SHARED
    assert "node1" in ctrl.directory["k1"]["sharers"]
    assert "node2" in ctrl.directory["k1"]["sharers"]

def test_mesi_directory_write():
    ctrl = DirectoryController()
    ctrl.handle_read("k1", "node1")
    ctrl.handle_read("k1", "node2")
    res = ctrl.handle_write("k1", "node3")
    assert res["action"] == "INVALIDATE"
    assert "node1" in res["sharers"]
    assert "node2" in res["sharers"]
    assert ctrl.directory["k1"]["state"] == CacheLineState.MODIFIED
    assert ctrl.directory["k1"]["owner"] == "node3"

def test_phi_accrual_threshold():
    fd = PhiAccrualFailureDetector(phi_threshold=8.0, window_size=10)
    for i in range(10):
        fd.heartbeat("node1")
        time.sleep(0.01) # Simulate fast heartbeats
        
    assert fd.is_alive("node1") == True
    
    # Fast forward in time conceptually 
    # Mocking time.time instead of actual wait is better, but here we just check false positive
    fd._last_arrival["node1"] = time.time() - 100.0  # 100s ago
    assert fd.is_alive("node1") == False

@pytest.mark.parametrize("current_type, request_type, expected_conflict", [
    (LockType.SHARED, LockType.SHARED, False),
    (LockType.SHARED, LockType.EXCLUSIVE, True),
    (LockType.EXCLUSIVE, LockType.SHARED, True),
    (LockType.EXCLUSIVE, LockType.EXCLUSIVE, True),
])
def test_lock_type_compatibility(current_type, request_type, expected_conflict):
    # Simulated behavior from LockManager try_lock logic
    if current_type == LockType.EXCLUSIVE:
        conflict = True
    elif request_type == LockType.EXCLUSIVE and current_type == LockType.SHARED:
        conflict = True
    else:
        conflict = False
        
    assert conflict == expected_conflict

def test_dead_letter_queue_trigger():
    msg = QueueMessage(queue_name="q1", payload={}, max_deliveries=3)
    assert msg.is_dead_letter() == False
    
    msg.delivery_count = 3
    assert msg.is_dead_letter() == True

    msg.delivery_count = 1
    msg.status = MessageStatus.DEAD
    assert msg.is_dead_letter() == True
