import httpx
import uuid
import random
import time
import json

BASE_URL = "http://localhost:8080"
TOTAL_EVENTS = 5000
DUPLICATE_RATIO = 0.2

def generate_events():
    unique_count = 4000
    dup_count = 1000

    unique_ids = [str(uuid.uuid4()) for _ in range(unique_count)]
    dup_ids = [unique_ids[i % unique_count] for i in range(dup_count)]
    all_ids = unique_ids + dup_ids
    random.shuffle(all_ids)

    return [
        {
            "topic": "stress-test",
            "event_id": eid,
            "timestamp": "2024-01-01T00:00:00",
            "source": "stress-test",
            "payload": {"index": i}
        }
        for i, eid in enumerate(all_ids)
    ]

def run_stress_test():
    print(f"Generating {TOTAL_EVENTS} events ({int(DUPLICATE_RATIO*100)}% duplicates)...")
    events = generate_events()

    print(f"Sending in batches of 100...")
    start = time.time()

    with httpx.Client(timeout=30) as client:
        try:
            client.get(f"{BASE_URL}/stats")
        except Exception:
            print("ERROR: Server tidak jalan! Jalankan Docker dulu.")
            return

        success = 0
        for i in range(0, len(events), 100):
            batch = events[i:i+100]
            try:
                res = client.post(f"{BASE_URL}/publish", json=batch)
                if res.status_code == 200:
                    success += res.json()["count"]
            except Exception as e:
                print(f"Batch {i//100} error: {e}")

            if (i + 100) % 1000 == 0:
                print(f"  Sent {i+100}/{TOTAL_EVENTS}...")

    elapsed = time.time() - start
    print(f"\nDone sending in {elapsed:.2f}s")
    print(f"Successfully queued: {success}/{TOTAL_EVENTS}")

    print("\nWaiting for consumer to process...")
    time.sleep(3)

    with httpx.Client() as client:
        res = client.get(f"{BASE_URL}/stats")
        stats = res.json()

    print("\n===== HASIL STRESS TEST =====")
    print(json.dumps(stats, indent=2))
    print(f"\nExpected unique_processed : ~4000")
    print(f"Expected duplicate_dropped: ~1000")
    print(f"Actual unique_processed   : {stats['unique_processed']}")
    print(f"Actual duplicate_dropped  : {stats['duplicate_dropped']}")

    assert 3900 <= stats["unique_processed"] <= 4000, f"unique_processed tidak sesuai: {stats['unique_processed']}"
    assert stats["duplicate_dropped"] >= 950, f"duplicate_dropped terlalu sedikit: {stats['duplicate_dropped']}"
    print("\nPASS: Stress test berhasil!")

if __name__ == "__main__":
    run_stress_test()