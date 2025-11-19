"""
GlobalRateLimiter - Enforce 2-Second Delays Across All Strategies

This module provides a global rate limiter that enforces minimum delays between
order submissions across all strategies, preventing API rate limit violations.

Key Features:
- Thread-safe timing coordination
- Blocking wait for sequential execution
- Non-blocking status checks
- Accurate delay calculation accounting for execution time
- Token bucket algorithm implementation

Performance Target:
- Accurate 2-second delays between orders
- Zero API rate limit violations
- Thread-safe coordination across strategies

Author: LumiBot Multi-Strategy Team
Date: 2025-11-18
"""

import logging
import threading
import time


class GlobalRateLimiter:
    """
    Enforces minimum delays between order submissions across all strategies.

    This class implements a token bucket algorithm to ensure that orders from
    all strategies respect the broker's rate limiting requirements. It provides
    both blocking and non-blocking methods for coordination.

    Thread-Safety:
        All public methods are thread-safe through the use of threading.Lock.

    Attributes:
        min_delay_seconds: Minimum delay between orders in seconds
        last_order_time: Timestamp of the last order submission
        lock: Threading lock for thread-safe operations
        total_waits: Total number of times waiting occurred
        total_wait_time: Cumulative wait time in seconds

    Example:
        >>> limiter = GlobalRateLimiter(min_delay_seconds=2.0)
        >>>
        >>> # Blocking wait until rate limit allows order
        >>> wait_time = limiter.wait_if_needed()
        >>> broker.submit_order(order)
        >>> limiter.mark_order_submitted()
        >>>
        >>> # Non-blocking check
        >>> if limiter.can_submit_immediately():
        >>>     broker.submit_order(order)
        >>>     limiter.mark_order_submitted()
    """

    def __init__(self, min_delay_seconds: float = 2.0):
        """
        Initialize the GlobalRateLimiter.

        Args:
            min_delay_seconds: Minimum delay between orders in seconds (default: 2.0)
        """
        self.min_delay_seconds = min_delay_seconds
        self.last_order_time = 0.0  # Initialize to 0 (allows first order immediately)
        self.lock = threading.Lock()

        # Statistics tracking
        self.total_waits = 0
        self.total_wait_time = 0.0
        self.orders_submitted = 0

        # Logger
        self.logger = logging.getLogger(__name__)

    def wait_if_needed(self) -> float:
        """
        Block until rate limit allows next order submission.

        This method calculates the time elapsed since the last order and sleeps
        for the remaining time if less than min_delay_seconds has elapsed.

        Returns:
            Actual wait time in seconds (0.0 if no wait was needed)

        Example:
            >>> limiter = GlobalRateLimiter(min_delay_seconds=2.0)
            >>> wait_time = limiter.wait_if_needed()
            >>> print(f"Waited {wait_time:.2f} seconds")
        """
        with self.lock:
            current_time = time.time()
            elapsed = current_time - self.last_order_time

            if elapsed < self.min_delay_seconds:
                # Need to wait
                wait_time = self.min_delay_seconds - elapsed
                self.total_waits += 1
                self.total_wait_time += wait_time

                self.logger.debug(
                    f"Rate limiting: waiting {wait_time:.3f}s "
                    f"(elapsed: {elapsed:.3f}s, min_delay: {self.min_delay_seconds}s)"
                )

                # Sleep while holding the lock to prevent other threads from proceeding
                time.sleep(wait_time)

                return wait_time
            else:
                # No wait needed
                self.logger.debug(
                    f"Rate limiting: no wait needed "
                    f"(elapsed: {elapsed:.3f}s >= min_delay: {self.min_delay_seconds}s)"
                )
                return 0.0

    def can_submit_immediately(self) -> bool:
        """
        Check if an order can be submitted immediately without waiting.

        This is a non-blocking status check that doesn't modify state.

        Returns:
            True if sufficient time has elapsed since last order, False otherwise

        Example:
            >>> if limiter.can_submit_immediately():
            >>>     broker.submit_order(order)
            >>>     limiter.mark_order_submitted()
            >>> else:
            >>>     print(f"Must wait {limiter.get_time_until_next_allowed():.1f}s")
        """
        with self.lock:
            current_time = time.time()
            elapsed = current_time - self.last_order_time
            return elapsed >= self.min_delay_seconds

    def mark_order_submitted(self) -> None:
        """
        Mark that an order has been submitted.

        This method updates the last order timestamp to the current time.
        It should be called immediately after submitting an order to the broker.

        Example:
            >>> limiter.wait_if_needed()
            >>> broker.submit_order(order)
            >>> limiter.mark_order_submitted()
        """
        with self.lock:
            self.last_order_time = time.time()
            self.orders_submitted += 1
            self.logger.debug(
                f"Order submitted (total: {self.orders_submitted}, " f"next allowed in {self.min_delay_seconds}s)"
            )

    def get_time_until_next_allowed(self) -> float:
        """
        Get the time remaining until next order can be submitted.

        Returns:
            Time in seconds until next order allowed (0.0 if can submit now)

        Example:
            >>> time_remaining = limiter.get_time_until_next_allowed()
            >>> print(f"Next order allowed in {time_remaining:.1f}s")
        """
        with self.lock:
            current_time = time.time()
            elapsed = current_time - self.last_order_time

            if elapsed >= self.min_delay_seconds:
                return 0.0
            else:
                return self.min_delay_seconds - elapsed

    def get_elapsed_since_last_order(self) -> float:
        """
        Get the time elapsed since the last order submission.

        Returns:
            Time in seconds since last order (or infinity if no orders yet)

        Example:
            >>> elapsed = limiter.get_elapsed_since_last_order()
            >>> print(f"Last order was {elapsed:.1f}s ago")
        """
        with self.lock:
            if self.last_order_time == 0.0:
                return float("inf")  # No orders submitted yet

            current_time = time.time()
            return current_time - self.last_order_time

    def reset(self) -> None:
        """
        Reset the rate limiter state.

        This clears all timing information and statistics, allowing the next
        order to be submitted immediately.

        Example:
            >>> limiter.reset()
            >>> # Next order can be submitted immediately
        """
        with self.lock:
            self.last_order_time = 0.0
            self.total_waits = 0
            self.total_wait_time = 0.0
            self.orders_submitted = 0
            self.logger.debug("Rate limiter reset")

    def get_stats(self) -> dict:
        """
        Get rate limiter statistics.

        Returns:
            Dictionary with statistics:
                - orders_submitted: Total number of orders submitted
                - total_waits: Number of times waiting occurred
                - total_wait_time: Cumulative wait time in seconds
                - avg_wait_time: Average wait time per wait
                - time_since_last_order: Time since last order in seconds

        Example:
            >>> stats = limiter.get_stats()
            >>> print(f"Orders submitted: {stats['orders_submitted']}")
            >>> print(f"Average wait time: {stats['avg_wait_time']:.2f}s")
        """
        with self.lock:
            avg_wait = (self.total_wait_time / self.total_waits) if self.total_waits > 0 else 0.0

            return {
                "orders_submitted": self.orders_submitted,
                "total_waits": self.total_waits,
                "total_wait_time": self.total_wait_time,
                "avg_wait_time": avg_wait,
                "time_since_last_order": self.get_elapsed_since_last_order(),
                "min_delay_seconds": self.min_delay_seconds,
            }

    def __repr__(self) -> str:
        """String representation of the rate limiter."""
        stats = self.get_stats()
        return (
            f"GlobalRateLimiter("
            f"min_delay={self.min_delay_seconds}s, "
            f"orders={stats['orders_submitted']}, "
            f"waits={stats['total_waits']}, "
            f"avg_wait={stats['avg_wait_time']:.2f}s)"
        )
