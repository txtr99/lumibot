"""
SharedDataManager - Centralized Data Caching for Multi-Strategy Trading

This module provides a shared data manager that fetches market data once and distributes
it to all strategies, achieving 95%+ reduction in API calls through intelligent caching.

Key Features:
- Fetch data once for all strategies trading the same symbol
- 60-second TTL cache invalidation
- Thread-safe access with proper locking
- Support for both Pandas and Polars DataFrames
- Cache key format: {symbol}_{length}_{timestep}

Performance Target:
- Cache hit rate: >95%
- API call reduction: 95%+ (1 call vs. 30 calls per iteration)

Author: LumiBot Multi-Strategy Team
Date: 2025-11-18
"""

import logging
import threading
import time
from typing import Dict, List

from lumibot.entities import Asset


class SharedDataManager:
    """
    Manages shared market data for multiple strategies to minimize API calls.

    This class implements a TTL-based cache that fetches data once per unique
    symbol and distributes it to all strategies, preventing redundant API calls.

    Thread-Safety:
        All public methods are thread-safe through the use of threading.Lock.

    Attributes:
        data_source: DataSource instance for fetching market data
        cache_ttl_seconds: Time-to-live for cached data (default: 60 seconds)
        cache: Dictionary storing cached data by cache key
        last_fetch: Dictionary storing last fetch timestamps by cache key
        lock: Threading lock for thread-safe operations

    Example:
        >>> from lumibot.data_sources import DataSource
        >>> data_source = DataSource()
        >>> manager = SharedDataManager(data_source, cache_ttl_seconds=60)
        >>>
        >>> # Fetch data for multiple strategies
        >>> symbols = ['ES', 'NQ', 'YM']
        >>> manager.fetch_for_all_strategies(symbols, length=100, timestep='1M')
        >>>
        >>> # Each strategy retrieves cached data (no additional API calls)
        >>> es_data = manager.get_cached_data('ES', 100, '1M')
        >>> nq_data = manager.get_cached_data('NQ', 100, '1M')
    """

    def __init__(self, data_source, cache_ttl_seconds: int = 60):
        """
        Initialize the SharedDataManager.

        Args:
            data_source: DataSource instance for fetching market data
            cache_ttl_seconds: Time-to-live for cached data in seconds (default: 60)
        """
        self.data_source = data_source
        self.cache_ttl_seconds = cache_ttl_seconds

        # Cache storage: {cache_key: data}
        self.cache: Dict[str, any] = {}

        # Last fetch timestamps: {cache_key: timestamp}
        self.last_fetch: Dict[str, float] = {}

        # Thread safety lock
        self.lock = threading.Lock()

        # Statistics tracking
        self.cache_hits = 0
        self.cache_misses = 0
        self.total_fetches = 0

        # Logger
        self.logger = logging.getLogger(__name__)

    def _create_cache_key(self, symbol: str, length: int, timestep: str) -> str:
        """
        Create a cache key from symbol, length, and timestep.

        Args:
            symbol: Asset symbol (e.g., 'ES', 'NQ')
            length: Number of bars to fetch
            timestep: Timestep string (e.g., '1M', '5M', '1H')

        Returns:
            Cache key string in format: {symbol}_{length}_{timestep}
        """
        return f"{symbol}_{length}_{timestep}"

    def _is_cache_valid(self, cache_key: str) -> bool:
        """
        Check if cached data is still valid based on TTL.

        Args:
            cache_key: Cache key to check

        Returns:
            True if cache is valid (not expired), False otherwise
        """
        if cache_key not in self.last_fetch:
            return False

        age = time.time() - self.last_fetch[cache_key]
        return age < self.cache_ttl_seconds

    def fetch_for_all_strategies(
        self, symbols: List[str], length: int, timestep: str, asset_type: str = Asset.AssetType.CONT_FUTURE
    ) -> None:
        """
        Fetch data once for all unique symbols, caching for all strategies.

        This method fetches market data for each unique symbol and caches it.
        Subsequent calls within the TTL window will use cached data instead
        of making additional API calls.

        Args:
            symbols: List of symbol strings to fetch data for
            length: Number of bars to fetch for each symbol
            timestep: Timestep string (e.g., '1M', '5M', '1H')
            asset_type: Asset type (default: CONT_FUTURE)

        Example:
            >>> manager.fetch_for_all_strategies(['ES', 'NQ', 'ES'], 100, '1M')
            >>> # Only fetches data for ES and NQ (deduplicates ES)
            >>> # Subsequent calls within 60 seconds use cache
        """
        with self.lock:
            # Deduplicate symbols
            unique_symbols = list(set(symbols))

            for symbol in unique_symbols:
                cache_key = self._create_cache_key(symbol, length, timestep)

                # Check if cache is valid
                if self._is_cache_valid(cache_key):
                    self.cache_hits += 1
                    self.logger.debug(
                        f"Cache hit for {cache_key} (age: {self.get_cache_age(symbol, length, timestep):.1f}s)"
                    )
                    continue

                # Cache miss - fetch from API
                self.cache_misses += 1
                self.logger.debug(f"Cache miss for {cache_key} - fetching from API")

                try:
                    # Create asset and fetch data
                    asset = Asset(symbol, asset_type=asset_type)
                    data = self.data_source.get_historical_prices(asset, length, timestep)

                    # Store in cache
                    self.cache[cache_key] = data
                    self.last_fetch[cache_key] = time.time()
                    self.total_fetches += 1

                    self.logger.debug(f"Cached data for {cache_key}")

                except Exception as e:
                    self.logger.error(f"Failed to fetch data for {symbol}: {e}")
                    # Don't cache failures
                    continue

    def get_cached_data(self, symbol: str, length: int, timestep: str):
        """
        Retrieve cached data for a specific symbol.

        This method is called by individual strategies to access cached data.
        It returns None if data is not cached or has expired.

        Args:
            symbol: Asset symbol
            length: Number of bars
            timestep: Timestep string

        Returns:
            Cached Bars object, or None if not cached or expired

        Example:
            >>> data = manager.get_cached_data('ES', 100, '1M')
            >>> if data is not None:
            >>>     # Use cached data
            >>>     close_prices = data.df['close']
        """
        with self.lock:
            cache_key = self._create_cache_key(symbol, length, timestep)

            if not self._is_cache_valid(cache_key):
                return None

            return self.cache.get(cache_key)

    def invalidate_cache(self, symbol: str = None, length: int = None, timestep: str = None) -> None:
        """
        Invalidate cached data for a specific symbol or all data.

        Args:
            symbol: Symbol to invalidate (if None, invalidates all)
            length: Length to invalidate (only if symbol specified)
            timestep: Timestep to invalidate (only if symbol specified)

        Example:
            >>> # Invalidate specific cache entry
            >>> manager.invalidate_cache('ES', 100, '1M')
            >>>
            >>> # Invalidate all ES cache entries
            >>> manager.invalidate_cache('ES')
            >>>
            >>> # Invalidate entire cache
            >>> manager.invalidate_cache()
        """
        with self.lock:
            if symbol is None:
                # Invalidate entire cache
                self.cache.clear()
                self.last_fetch.clear()
                self.logger.debug("Invalidated entire cache")
            elif length is not None and timestep is not None:
                # Invalidate specific entry
                cache_key = self._create_cache_key(symbol, length, timestep)
                if cache_key in self.cache:
                    del self.cache[cache_key]
                    del self.last_fetch[cache_key]
                    self.logger.debug(f"Invalidated cache for {cache_key}")
            else:
                # Invalidate all entries for symbol
                keys_to_delete = [k for k in self.cache.keys() if k.startswith(f"{symbol}_")]
                for key in keys_to_delete:
                    del self.cache[key]
                    del self.last_fetch[key]
                self.logger.debug(f"Invalidated {len(keys_to_delete)} cache entries for {symbol}")

    def get_cache_age(self, symbol: str, length: int, timestep: str) -> float:
        """
        Get the age of cached data in seconds.

        Args:
            symbol: Asset symbol
            length: Number of bars
            timestep: Timestep string

        Returns:
            Age in seconds, or -1 if not cached
        """
        with self.lock:
            cache_key = self._create_cache_key(symbol, length, timestep)

            if cache_key not in self.last_fetch:
                return -1.0

            return time.time() - self.last_fetch[cache_key]

    def get_cache_stats(self) -> dict:
        """
        Get cache performance statistics.

        Returns:
            Dictionary with cache statistics:
                - total_fetches: Total number of API fetches
                - cache_hits: Number of cache hits
                - cache_misses: Number of cache misses
                - hit_rate: Cache hit rate as percentage
                - cached_symbols: Number of unique cache entries

        Example:
            >>> stats = manager.get_cache_stats()
            >>> print(f"Cache hit rate: {stats['hit_rate']:.1f}%")
        """
        with self.lock:
            total_requests = self.cache_hits + self.cache_misses
            hit_rate = (self.cache_hits / total_requests * 100) if total_requests > 0 else 0.0

            return {
                "total_fetches": self.total_fetches,
                "cache_hits": self.cache_hits,
                "cache_misses": self.cache_misses,
                "hit_rate": hit_rate,
                "cached_symbols": len(self.cache),
            }

    def reset_stats(self) -> None:
        """Reset cache statistics counters."""
        with self.lock:
            self.cache_hits = 0
            self.cache_misses = 0
            self.total_fetches = 0
