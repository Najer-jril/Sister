# src/communication/message_passing.py
import asyncio
import logging
import uuid
import time
from enum import Enum, auto
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Callable, Awaitable
import aiohttp

logger = logging.getLogger(__name__)

class MessageType(Enum):
    """Types of messages used in the distributed sync system."""
    HEARTBEAT = "HEARTBEAT"
    REQUEST = "REQUEST"
    RESPONSE = "RESPONSE"
    BROADCAST = "BROADCAST"
    ACK = "ACK"

@dataclass
class Message:
    """Dataclass representing a standard message envelope."""
    type: MessageType
    sender: str
    receiver: str
    payload: Dict[str, Any]
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: float = field(default_factory=time.time)

    def __repr__(self) -> str:
        """String representation of the message."""
        return (f"Message(id='{self.id}', type={self.type.name}, "
                f"sender='{self.sender}', receiver='{self.receiver}', "
                f"timestamp={self.timestamp})")

    def to_dict(self) -> Dict[str, Any]:
        """Serializes the message to a dictionary suitable for JSON encoding."""
        return {
            "id": self.id,
            "type": self.type.value,
            "sender": self.sender,
            "receiver": self.receiver,
            "payload": self.payload,
            "timestamp": self.timestamp
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Message":
        """Deserializes a dictionary into a Message object."""
        return cls(
            id=data.get("id", str(uuid.uuid4())),
            type=MessageType(data["type"]),
            sender=data["sender"],
            receiver=data["receiver"],
            payload=data["payload"],
            timestamp=data.get("timestamp", time.time())
        )

class MessageBus:
    """
    Message bus for managing outgoing network communication with built-in retry logic.
    Provides subscriptions for local event routing.
    """
    def __init__(self) -> None:
        self._session: Optional[aiohttp.ClientSession] = None
        self._subscribers: Dict[MessageType, List[Callable[[Message], Awaitable[None]]]] = {
            m_type: [] for m_type in MessageType
        }

    async def start(self) -> None:
        """Initializes the connection pool (aiohttp ClientSession)."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=5.0)
            )
            logger.info("MessageBus started and connection pool initialized.")

    async def stop(self) -> None:
        """Closes the connection pool."""
        if self._session and not self._session.closed:
            await self._session.close()
            logger.info("MessageBus connection pool closed.")

    def subscribe(self, msg_type: MessageType, callback: Callable[[Message], Awaitable[None]]) -> None:
        """
        Subscribes a local callback to a specific incoming message type.
        
        Args:
            msg_type: The MessageType to listen for.
            callback: An async function accepting a Message.
        """
        if callback not in self._subscribers[msg_type]:
            self._subscribers[msg_type].append(callback)

    def unsubscribe(self, msg_type: MessageType, callback: Callable[[Message], Awaitable[None]]) -> None:
        """Unsubscribes a local callback."""
        if callback in self._subscribers[msg_type]:
            self._subscribers[msg_type].remove(callback)

    async def dispatch_local(self, message: Message) -> None:
        """Dispatches an incoming message to all local subscribers."""
        tasks = [callback(message) for callback in self._subscribers.get(message.type, [])]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def send(self, message: Message, target_url: str, max_retries: int = 3) -> bool:
        """
        Sends a message asynchronously with exponential backoff.
        
        Args:
            message: The Message to send.
            target_url: Destination URL.
            max_retries: Maximum number of delivery attempts.
            
        Returns:
            bool: True if sent successfully, False otherwise.
        """
        if not self._session:
            logger.error("MessageBus not started.")
            return False

        payload = message.to_dict()
        base_delay = 0.5

        for attempt in range(max_retries):
            try:
                async with self._session.post(target_url, json=payload) as response:
                    if response.status == 200:
                        return True
                    else:
                        logger.warning(f"Unexpected status {response.status} sending to {target_url}")
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                logger.debug(f"Attempt {attempt + 1}/{max_retries} failed sending to {target_url}: {e}")
            except Exception as e:
                logger.error(f"Critial error sending message to {target_url}", exc_info=e)
                break
            
            if attempt < max_retries - 1:
                delay = base_delay * (2 ** attempt)
                await asyncio.sleep(delay)

        logger.error(f"Failed to deliver message {message.id} to {target_url} after {max_retries} attempts.")
        return False

    async def broadcast(self, message: Message, target_urls: List[str]) -> None:
        """
        Broadcasts a message to multiple targets simultaneously.
        
        Args:
            message: The Message to broadcast.
            target_urls: A list of destination URLs.
        """
        try:
            tasks = [self.send(message, url) for url in target_urls]
            await asyncio.gather(*tasks, return_exceptions=True)
        except Exception as e:
            logger.error(f"Error during message broadcast", exc_info=e)