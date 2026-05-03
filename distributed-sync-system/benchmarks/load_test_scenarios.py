from locust import HttpUser, task, between
import uuid
import random
import json
import time

class LockManagerUser(HttpUser):
    wait_time = between(0.1, 0.5)

    @task
    def acquire_release_lock(self):
        resource_id = f"res_{random.randint(1, 100)}"
        holder_id = f"holder_{uuid.uuid4().hex[:8]}"
        
        with self.client.post("/locks/acquire", json={
            "resource_id": resource_id,
            "lock_type": "EXCLUSIVE",
            "holder_id": holder_id,
            "timeout": 5.0
        }, catch_response=True) as response:
            if response.status_code == 200:
                data = response.json()
                lock_id = data.get("lock_id")
                
                # Hold it briefly
                time.sleep(0.05)
                
                # Release
                self.client.delete(f"/locks/{lock_id}?holder_id={holder_id}")
            elif response.status_code == 409:
                response.success() # Conflict is expected in load testing

class QueueUser(HttpUser):
    wait_time = between(0.05, 0.2)

    @task(3) # Produce more often
    def produce_message(self):
        queue_name = f"q_{random.randint(1, 5)}"
        self.client.post(f"/queues/{queue_name}/messages", json={
            "payload": {"data": f"load-test-data-{uuid.uuid4()}"},
            "priority": 1
        })

    @task(1)
    def consume_message(self):
        queue_name = f"q_{random.randint(1, 5)}"
        consumer_id = f"consumer_{uuid.uuid4().hex[:8]}"
        with self.client.get(f"/queues/{queue_name}/messages/next?consumer_id={consumer_id}&timeout=1", catch_response=True) as response:
            if response.status_code == 200:
                data = response.json()
                msg_id = data.get("message_id")
                if msg_id:
                    self.client.post(f"/queues/messages/{msg_id}/ack", json={"consumer_id": consumer_id})
            elif response.status_code == 204:
                response.success() # Empty queue is fine

class CacheUser(HttpUser):
    wait_time = between(0.01, 0.1)

    @task(8) # 80% Reads
    def read_cache(self):
        key = f"key_{random.randint(1, 1000)}"
        self.client.get(f"/cache/{key}")

    @task(2) # 20% Writes
    def write_cache(self):
        key = f"key_{random.randint(1, 1000)}"
        self.client.put(f"/cache/{key}", json={"value": f"val_{uuid.uuid4()}"})
