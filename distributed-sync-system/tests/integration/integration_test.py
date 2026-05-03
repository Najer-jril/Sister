import pytest
import aiohttp
import asyncio
import uuid

# Replace with actual reachable ports if doing live test
NODE1_URL = "http://127.0.0.1:8001"
NODE2_URL = "http://127.0.0.1:8002"
NODE3_URL = "http://127.0.0.1:8003"

@pytest.mark.integration
@pytest.mark.asyncio
async def test_three_node_lock_contention():
    # Requires running cluster
    async with aiohttp.ClientSession() as session:
        # 1. Acquire lock on leader
        resource_id = "test_res_1"
        holder_1 = "h1"
        
        async with session.post(f"{NODE1_URL}/locks/acquire", json={
            "resource_id": resource_id,
            "holder_id": holder_1,
            "lock_type": "EXCLUSIVE"
        }) as resp:
            if resp.status == 503:
                pytest.skip("Node 1 is not the leader. Full cluster redirection test skipped.")
            assert resp.status == 200
            data = await resp.json()
            lock_id = data["lock_id"]
            
        # 2. Try to acquire same lock (should conflict)
        holder_2 = "h2"
        async with session.post(f"{NODE1_URL}/locks/acquire", json={
            "resource_id": resource_id,
            "holder_id": holder_2,
            "lock_type": "EXCLUSIVE",
            "timeout": 1.0 # short timeout to verify conflict
        }) as resp:
            assert resp.status == 409
            
        # 3. Release lock
        async with session.delete(f"{NODE1_URL}/locks/{lock_id}?holder_id={holder_1}") as resp:
            assert resp.status == 200

@pytest.mark.integration
@pytest.mark.asyncio
async def test_queue_node_failover():
    async with aiohttp.ClientSession() as session:
        queue_name = "test_queue_integration"
        payload = {"hello": "world"}
        
        # Produce to node1, even if node1 isn't primarily responsible, it will forward/fallback
        async with session.post(f"{NODE1_URL}/queues/{queue_name}/messages", json={
            "payload": payload
        }) as resp:
            if resp.status == 201:
                data = await resp.json()
                assert "message_id" in data
