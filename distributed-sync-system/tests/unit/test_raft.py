import pytest
import asyncio
from unittest.mock import AsyncMock, patch, MagicMock
from src.consensus.raft import RaftNode, Role

@pytest.fixture
def raft_node():
    node = RaftNode("node1", "localhost", 8001, ["http://node2", "http://node3"], "redis://fake")
    node.redis = AsyncMock() # mock redis
    node.redis.get.return_value = "0"
    node.redis.llen.return_value = 0
    node.redis.lindex.return_value = None
    node.send_message = AsyncMock(return_value=True)
    return node

@pytest.mark.asyncio
async def test_raft_start_election(raft_node):
    await raft_node.start_election()
    assert raft_node.role == Role.CANDIDATE
    assert raft_node._votes_received == 1
    assert raft_node.leader_id is None
    # It should have sent RequestVote to peers
    assert raft_node.send_message.call_count == 2

@pytest.mark.asyncio
async def test_leader_election_majority(raft_node):
    await raft_node.start_election()
    # Mock current term
    raft_node.redis.get.return_value = "1"
    
    # Receive 1 vote (total 2 = majority for 3 nodes)
    await raft_node._handle_vote_response(term=1, vote_granted=True)
    assert raft_node.role == Role.LEADER
    assert raft_node.leader_id == "node1"
    assert raft_node._leader_loop_task is not None

@pytest.mark.asyncio
async def test_split_vote_retry(raft_node):
    await raft_node.start_election()
    raft_node.redis.get.return_value = "1"
    
    # Receive negative vote
    await raft_node._handle_vote_response(term=2, vote_granted=False)
    # Higher term means stepped down
    assert raft_node.role == Role.FOLLOWER

@pytest.mark.asyncio
async def test_leader_steps_down(raft_node):
    raft_node.role = Role.LEADER
    # Receive append entries with higher term
    res = await raft_node.append_entries(
        term=5, leaderId="node2", prevLogIndex=0, prevLogTerm=0, entries=[], leaderCommit=0
    )
    assert raft_node.role == Role.FOLLOWER
    assert raft_node.leader_id == "node2"

@pytest.mark.asyncio
async def test_network_partition_no_commit(raft_node):
    raft_node.role = Role.LEADER
    raft_node.peer_urls = ["http://node2", "http://node3", "http://node4", "http://node5"]
    # Fail sending to all
    raft_node.send_message = AsyncMock(return_value=False)
    
    await raft_node.send_heartbeats()
    # Should step down due to no majority
    assert raft_node.role == Role.FOLLOWER
