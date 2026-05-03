# src/utils/metrics.py
import logging
from prometheus_client import Counter, Gauge, Histogram, start_http_server

logger = logging.getLogger(__name__)

class MetricsCollector:
    """
    Prometheus-compatible metrics collector class for the Distributed Sync System.
    Tracks network IO, Raft states, and request latency.
    """
    def __init__(self, node_id: str) -> None:
        """
        Initializes the metrics collector with specific metrics for a given node.

        Args:
            node_id: The unique identifier of the node exposing these metrics.
        """
        self.node_id = node_id

        # Counters
        self.messages_sent = Counter(
            'sync_system_messages_sent_total',
            'Total number of messages sent',
            ['node_id', 'message_type']
        )
        self.messages_received = Counter(
            'sync_system_messages_received_total',
            'Total number of messages received',
            ['node_id', 'message_type']
        )

        # Gauges
        self.current_term = Gauge(
            'sync_system_current_term',
            'The current Raft term of the node',
            ['node_id']
        )
        self.is_leader = Gauge(
            'sync_system_is_leader',
            'Indicates if the node is currently the leader (1) or not (0)',
            ['node_id']
        )

        # Histograms
        self.request_latency = Histogram(
            'sync_system_request_latency_seconds',
            'Latency of processed requests in seconds',
            ['node_id', 'request_type']
        )

    def start_server(self, port: int) -> None:
        """
        Starts the Prometheus metrics HTTP server on the specified port.

        Args:
            port: The port number to expose the metrics on.
        """
        start_http_server(port)
        logger.info(f"Metrics server started for node {self.node_id} on port {port}")

    def record_message_sent(self, message_type: str) -> None:
        """
        Records that a message of a specific type has been sent.

        Args:
            message_type: The type of the message sent (e.g., 'vote_request', 'append_entries').
        """
        self.messages_sent.labels(node_id=self.node_id, message_type=message_type).inc()

    def record_message_received(self, message_type: str) -> None:
        """
        Records that a message of a specific type has been received.

        Args:
            message_type: The type of the message received (e.g., 'vote_response', 'append_entries').
        """
        self.messages_received.labels(node_id=self.node_id, message_type=message_type).inc()

    def update_term(self, term: int) -> None:
        """
        Updates the node's current term in the metrics gauge.

        Args:
            term: The new term integer to set.
        """
        self.current_term.labels(node_id=self.node_id).set(term)

    def update_leadership_status(self, is_leader: bool) -> None:
        """
        Updates the node's leadership status in the metrics gauge.

        Args:
            is_leader: Boolean indicating whether the node is the leader.
        """
        self.is_leader.labels(node_id=self.node_id).set(1.0 if is_leader else 0.0)

    def observe_request_latency(self, request_type: str, time_seconds: float) -> None:
        """
        Records the latency for a specific request type.

        Args:
            request_type: The type of request (e.g., 'client_request', 'state_replication').
            time_seconds: The latency observed in seconds.
        """
        self.request_latency.labels(node_id=self.node_id, request_type=request_type).observe(time_seconds)