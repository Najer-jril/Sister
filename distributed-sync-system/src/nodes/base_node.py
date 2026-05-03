# src/nodes/base_node.py
import asyncio
import logging
from abc import ABC, abstractmethod
from enum import Enum, auto
from typing import Optional, Dict, Any
from aiohttp import web

from src.utils.metrics import MetricsCollector
from src.communication.message_passing import MessageBus, Message, MessageType

logger = logging.getLogger(__name__)

class NodeState(Enum):
    """Enumeration representing the lifecycle states of a node."""
    STARTING = auto()
    RUNNING = auto()
    STOPPED = auto()
    FAILED = auto()

class BaseNode(ABC):
    """
    Abstract base class for a node in the distributed sync system.
    Manages lifecycle, basic HTTP server for health checks, and a message bus.
    """

    def __init__(self, node_id: str, host: str, port: int, heartbeat_interval: float = 5.0) -> None:
        """
        Initializes the node configuration.

        Args:
            node_id: Unique string identifier for the node.
            host: Host address to bind the HTTP server.
            port: Port to bind the HTTP server.
            heartbeat_interval: Interval in seconds between heartbeats.
        """
        self.node_id = node_id
        self.host = host
        self.port = port
        self.heartbeat_interval = heartbeat_interval
        
        self.state: NodeState = NodeState.STOPPED
        self.metrics: MetricsCollector = MetricsCollector(node_id)
        self.message_bus: MessageBus = MessageBus()
        
        self._app = web.Application()
        self._app.router.add_get('/health', self.health_check)
        self._app.router.add_post('/message', self.handle_incoming_http_message)
        
        self._runner: Optional[web.AppRunner] = None
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._internal_queue: asyncio.Queue[Message] = asyncio.Queue()
        self._queue_processor_task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        """Starts the node, its HTTP server, message bus, and background tasks."""
        try:
            if self.state != NodeState.STOPPED:
                logger.warning(f"Node {self.node_id} is already in state {self.state}")
                return

            self.state = NodeState.STARTING
            logger.info(f"Starting node {self.node_id} on {self.host}:{self.port}")

            await self.message_bus.start()
            
            # Start HTTP Server
            self._runner = web.AppRunner(self._app)
            await self._runner.setup()
            site = web.TCPSite(self._runner, self.host, self.port)
            await site.start()

            self.state = NodeState.RUNNING
            
            # Start background tasks
            self._heartbeat_task = asyncio.create_task(self.heartbeat_loop())
            self._queue_processor_task = asyncio.create_task(self._process_internal_queue())
            
            logger.info(f"Node {self.node_id} successfully started.")
        except Exception as e:
            self.state = NodeState.FAILED
            logger.error(f"Failed to start node {self.node_id}", exc_info=e)
            raise

    async def stop(self) -> None:
        """Stops the node, clears tasks, and shuts down the HTTP server."""
        try:
            logger.info(f"Stopping node {self.node_id}...")
            self.state = NodeState.STOPPED

            if self._heartbeat_task:
                self._heartbeat_task.cancel()
            if self._queue_processor_task:
                self._queue_processor_task.cancel()
                
            await self.message_bus.stop()

            if self._runner:
                await self._runner.cleanup()
                
            logger.info(f"Node {self.node_id} successfully stopped.")
        except Exception as e:
            self.state = NodeState.FAILED
            logger.error(f"Error while stopping node {self.node_id}", exc_info=e)
            raise

    async def send_message(self, message: Message, target_url: str) -> bool:
        """
        Sends a message to a specific target via the message bus.

        Args:
            message: The Message object to send.
            target_url: The destination endpoint (e.g., http://host:port/message).
            
        Returns:
            bool: True if the message was sent successfully, False otherwise.
        """
        try:
            success = await self.message_bus.send(message, target_url)
            if success:
                self.metrics.record_message_sent(message.type.name)
            return success
        except Exception as e:
            logger.error(f"Error sending message from {self.node_id}", exc_info=e)
            return False

    async def receive_message(self, message: Message) -> None:
        """
        Accepts a message into the internal async queue for processing.

        Args:
            message: The received Message object.
        """
        try:
            await self._internal_queue.put(message)
            self.metrics.record_message_received(message.type.name)
        except Exception as e:
            logger.error(f"Error receiving message on {self.node_id}", exc_info=e)

    async def _process_internal_queue(self) -> None:
        """Background task to continuously process messages from the internal queue."""
        while self.state == NodeState.RUNNING:
            try:
                message = await self._internal_queue.get()
                await self.process_message(message)
                self._internal_queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error processing message from queue on {self.node_id}", exc_info=e)

    @abstractmethod
    async def process_message(self, message: Message) -> None:
        """
        Abstract method to be implemented by subclasses to define specific message handling behavior.
        
        Args:
            message: The message to process.
        """
        pass

    async def health_check(self, request: web.Request) -> web.Response:
        """
        HTTP endpoint for health checks.

        Args:
            request: The incoming aiohttp Request.
            
        Returns:
            web.Response: A JSON response containing the node status.
        """
        return web.json_response({
            "node_id": self.node_id,
            "state": self.state.name,
            "status": "healthy" if self.state == NodeState.RUNNING else "unhealthy"
        })

    async def handle_incoming_http_message(self, request: web.Request) -> web.Response:
        """
        HTTP POST endpoint for receiving messages from other nodes.

        Args:
            request: The incoming aiohttp Request containing JSON payload.
            
        Returns:
            web.Response: A 200 OK response if accepted, 400 or 500 otherwise.
        """
        try:
            data = await request.json()
            message = Message.from_dict(data)
            await self.receive_message(message)
            return web.Response(status=200, text="OK")
        except Exception as e:
            logger.error(f"Failed to handle incoming HTTP message on {self.node_id}", exc_info=e)
            return web.Response(status=400, text=str(e))

    async def heartbeat_loop(self) -> None:
        """Background loop continuously sending heartbeats if the node is running."""
        while self.state == NodeState.RUNNING:
            try:
                # Actual broadcast target URLs would be loaded from configuration/discovery
                # This ensures the loop runs and subclasses can hook into it
                await asyncio.sleep(self.heartbeat_interval)
                logger.debug(f"Node {self.node_id} heartbeat active.")
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in heartbeat loop on node {self.node_id}", exc_info=e)