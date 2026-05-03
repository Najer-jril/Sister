import argparse
import asyncio
import logging
import sys
import os

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s'
)

from src.utils.config import load_config
from src.consensus.raft import RaftNode
from src.nodes.lock_manager import LockManager
from src.nodes.queue_node import QueueNode
from src.nodes.cache_node import CacheNode


async def main():
    parser = argparse.ArgumentParser(description="Distributed Sync System Node Runner")
    parser.add_argument("--node-type", choices=["lock", "queue", "cache", "raft"], required=True)
    parser.add_argument("--node-id", required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--peers", required=False, default="")
    args = parser.parse_args()

    config = load_config()
    host = "0.0.0.0"
    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")

    peer_list = [p for p in args.peers.split(',') if p]
    peer_urls = [f"http://{p}/message" for p in peer_list]
    peer_dict = {p.split(':')[0]: f"http://{p}/message" for p in peer_list}

    node = None

    if args.node_type == "raft":
        node = RaftNode(args.node_id, host, args.port, peer_urls, redis_url)
    elif args.node_type == "lock":
        node = LockManager(args.node_id, host, args.port, peer_urls, redis_url)
    elif args.node_type == "queue":
        node = QueueNode(args.node_id, host, args.port, redis_url, peers=peer_dict)
    elif args.node_type == "cache":
        directory_url = os.getenv("DIRECTORY_URL", "http://node1:8001")
        is_directory = args.node_id == "node1"
        node = CacheNode(
            args.node_id, host, args.port, redis_url,
            directory_url, is_directory, peers=peer_dict
        )

    if not node:
        logging.error("Unknown node type, exiting.")
        sys.exit(1)

    try:
        await node.start()
        logging.info(f"Node {args.node_id} running on port {args.port}")
        while True:
            await asyncio.sleep(3600)
    except KeyboardInterrupt:
        pass
    except Exception as e:
        logging.error(f"Fatal error on {args.node_id}", exc_info=e)
    finally:
        await node.stop()


if __name__ == "__main__":
    asyncio.run(main())