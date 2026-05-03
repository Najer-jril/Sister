# src/communication/failure_detector.py
import time
import math
import logging
from collections import deque
from typing import Dict, Deque, List, Callable

logger = logging.getLogger(__name__)

class PhiAccrualFailureDetector:
    """
    Implementation of the Phi Accrual Failure Detector algorithm.
    Used to calculate the probability that a node has crashed based on heartbeat history.
    """

    def __init__(self, phi_threshold: float = 8.0, window_size: int = 100) -> None:
        """
        Initializes the failure detector.

        Args:
            phi_threshold: The phi value above which a node is considered failed (default: 8.0).
            window_size: The number of recent heartbeat intervals to keep for the sliding window.
        """
        self.phi_threshold = phi_threshold
        self.window_size = window_size
        
        # State tracking per node
        self._history: Dict[str, Deque[float]] = {}
        self._last_arrival: Dict[str, float] = {}
        
        # Callbacks triggered when a node is marked as failed
        self._on_failure_callbacks: List[Callable[[str], None]] = []
        
        # Keep track of nodes currently considered failed to avoid duplicate triggers
        self._failed_nodes: set = set()

    def register_on_failure_callback(self, callback: Callable[[str], None]) -> None:
        """Registers a function to be called when a node fails."""
        if callback not in self._on_failure_callbacks:
            self._on_failure_callbacks.append(callback)

    def heartbeat(self, node_id: str) -> None:
        """
        Registers a heartbeat arrival for the given node.

        Args:
            node_id: The identifier of the node that sent the heartbeat.
        """
        try:
            now = time.time()
            if node_id in self._last_arrival:
                interval = now - self._last_arrival[node_id]
                
                if node_id not in self._history:
                    self._history[node_id] = deque(maxlen=self.window_size)
                    
                self._history[node_id].append(interval)

            self._last_arrival[node_id] = now
            
            # If node recovers, remove from failed set
            if node_id in self._failed_nodes:
                self._failed_nodes.remove(node_id)
                logger.info(f"Node {node_id} has recovered and is sending heartbeats again.")
                
        except Exception as e:
            logger.error(f"Error processing heartbeat for node {node_id}", exc_info=e)

    def is_alive(self, node_id: str) -> bool:
        """
        Determines if a node is alive based on the phi threshold.
        Triggers failure callbacks if the node transitions to a failed state.

        Args:
            node_id: The identifier of the node to check.

        Returns:
            bool: True if alive (phi < threshold), False otherwise.
        """
        try:
            if node_id not in self._last_arrival:
                return False

            if node_id not in self._history or len(self._history[node_id]) < 2:
                # Not enough history, give it the benefit of the doubt optionally
                # Here we assume it's alive if we've seen at least one recent heartbeat
                time_since_last = time.time() - self._last_arrival[node_id]
                return time_since_last < (self.phi_threshold * 1.0) # Fallback arbitrary timeout

            now = time.time()
            time_since_last = now - self._last_arrival[node_id]

            # Calculate mean and variance
            intervals = list(self._history[node_id])
            mean = sum(intervals) / len(intervals)
            
            variance = sum((x - mean) ** 2 for x in intervals) / len(intervals)
            std_dev = math.sqrt(variance)
            
            # Avoid divide-by-zero
            if std_dev == 0:
                std_dev = 0.1

            # Calculate Phi using exponential approximation derived from normal distribution
            y = (time_since_last - mean) / std_dev
            e = math.exp(-y * (1.5956 + 0.070566 * y * y))
            
            if time_since_last > mean:
                prob = e / (1.0 + e)
            else:
                prob = 1.0 - 1.0 / (1.0 + e)
                
            phi = -math.log10(prob) if prob > 0 else float('inf')

            alive = phi < self.phi_threshold

            if not alive and node_id not in self._failed_nodes:
                self._failed_nodes.add(node_id)
                self._trigger_failure_callbacks(node_id)

            return alive

        except Exception as e:
            logger.error(f"Error calculating phi for node {node_id}", exc_info=e)
            return False

    def _trigger_failure_callbacks(self, node_id: str) -> None:
        """Internally triggers all registered failure callbacks."""
        logger.warning(f"Failure detector flagged node {node_id} as FAILED.")
        for callback in self._on_failure_callbacks:
            try:
                callback(node_id)
            except Exception as e:
                logger.error(f"Error executing on_failure callback for node {node_id}", exc_info=e)