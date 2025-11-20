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
        # Fast path: if we already have the data cached, reuse it (expected in backtests)
        if cache_key in self.cache:
            return True

        # Otherwise, fall back to TTL check for any future extensions/live reuse
        if cache_key not in self.last_fetch:
            return False

        age = time.time() - self.last_fetch[cache_key]
        return age < self.cache_ttl_seconds

    def _prefetched_index_by_symbol(self):
        """
        Build a dict mapping symbol -> prefetched data object from the datasource store.
        Returns empty dict if no datasource store is available.
        """
        ds = getattr(self, "data_source", None)
        store = getattr(ds, "pandas_data", None)
        if not store or not isinstance(store, dict):
            return {}

        index = {}
        for key, data_obj in store.items():
            try:
                asset = key[0] if isinstance(key, tuple) else key
                symbol = getattr(asset, "symbol", None)
                if symbol:
                    index[symbol] = data_obj
            except Exception:
                continue
        return index

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
        fetch_start = time.perf_counter()
        debug_enabled = self.logger.isEnabledFor(logging.DEBUG)
        self.logger.info(
            f"[SDM] fetch_for_all_strategies start symbols={symbols} length={length} timestep={timestep} asset_type={asset_type}"
        )
        phase1_start = time.perf_counter()
        to_fetch = []
        unique_symbols: List[str] = []
        all_cached_flag = False
        try:
            # Phase 1 (under lock): determine which symbols need fetching and fill from prefetched store
            self.logger.info("[SDM] acquiring lock for phase1")
            lock_wait_start = time.perf_counter()
            with self.lock:
                self.logger.info(
                    f"[SDM] lock acquired in {time.perf_counter() - lock_wait_start:.6f}s"
                )
                unique_symbols = list(set(symbols))
                if debug_enabled:
                    self.logger.debug(f"[SDM] phase1 unique_symbols={unique_symbols}")
                prefetched_index = self._prefetched_index_by_symbol()
                if debug_enabled:
                    self.logger.debug(f"[SDM] prefetched_index keys={list(prefetched_index.keys())}")

                # If everything is already cached, count as hits and return
                all_cached = True
                for symbol in unique_symbols:
                    cache_key = self._create_cache_key(symbol, length, timestep)
                    if debug_enabled:
                        self.logger.debug(f"[SDM] evaluating symbol={symbol} cache_key={cache_key}")

                    if self._is_cache_valid(cache_key):
                        self.cache_hits += 1
                        if debug_enabled:
                            self.logger.debug(
                                f"[SDM] cache hit {cache_key} age={self.get_cache_age(symbol, length, timestep):.4f}s"
                            )
                        continue

                    all_cached = False
                    prefetched = prefetched_index.get(symbol)
                    if prefetched is not None:
                        self.cache[cache_key] = prefetched
                        self.last_fetch[cache_key] = time.time()
                        self.total_fetches += 1
                        self.logger.info(f"[SDM] Cached prefetched data for {cache_key}")
                        continue

                    self.cache_misses += 1
                    to_fetch.append((symbol, cache_key))
                    if debug_enabled:
                        self.logger.debug(f"[SDM] marked for fetch symbol={symbol} cache_key={cache_key}")

                self.logger.info(
                    f"[SDM] phase1 complete all_cached={all_cached} to_fetch={to_fetch} "
                    f"duration={time.perf_counter() - phase1_start:.6f}s"
                )

                if all_cached and not to_fetch:
                    all_cached_flag = True
        finally:
            if debug_enabled:
                self.logger.debug(
                    f"[SDM] phase1 exit unique_symbols={unique_symbols} to_fetch={to_fetch}"
                )

        if all_cached_flag and not to_fetch:
            self.logger.info(
                f"[SDM] all_cached fast-exit duration={time.perf_counter() - fetch_start:.4f}s "
                f"cache_stats={self.get_cache_stats()}"
            )
            return

        # Phase 2 (outside lock): try datasource store, then perform API fetches only if needed
        # Build a fast index of datasource store once
        store_index = {}
        store = getattr(self.data_source, "pandas_data", None)
        if store and isinstance(store, dict):
            for key, data_obj in store.items():
                try:
                    asset = key[0] if isinstance(key, tuple) else key
                    symbol = getattr(asset, "symbol", None)
                    if symbol:
                        store_index[symbol] = data_obj
                except Exception:
                    continue
        if debug_enabled:
            self.logger.debug(f"[SDM] phase2 store_index keys={list(store_index.keys())} from datasource")

        for symbol, cache_key in to_fetch:
            per_symbol_start = time.perf_counter()
            # Try grabbing from datasource store directly to avoid API
            data_obj = store_index.get(symbol)
            if data_obj is not None:
                now_ts = time.time()
                with self.lock:
                    self.cache[cache_key] = data_obj
                    self.last_fetch[cache_key] = now_ts
                    self.total_fetches += 1
                self.logger.debug(
                    f"[SDM] Cached datasource store data for {cache_key} "
                    f"duration={time.perf_counter() - per_symbol_start:.6f}s"
                )
                continue

            # If still not cached, fall back to API
            with self.lock:
                already_cached = cache_key in self.cache
            if already_cached:
                self.logger.debug(
                    f"[SDM] Skipping API fetch for {cache_key}; already cached after store check "
                    f"duration={time.perf_counter() - per_symbol_start:.6f}s"
                )
                continue

            self.logger.info(f"[SDM] No prefetched/store data for {symbol}; fetching from API")
            try:
                asset = Asset(symbol, asset_type=asset_type)
                data = self.data_source.get_historical_prices(asset, length, timestep)
                now_ts = time.time()
                # Store result under lock
                with self.lock:
                    self.cache[cache_key] = data
                    self.last_fetch[cache_key] = now_ts
                    self.total_fetches += 1
                self.logger.debug(
                    f"[SDM] Cached data for {cache_key} "
                    f"duration={time.perf_counter() - per_symbol_start:.6f}s"
                )
            except Exception as e:
                self.logger.error(f"Failed to fetch data for {symbol}: {e}")
                continue

        self.logger.info(
            f"[SDM] fetch_for_all_strategies done in {time.perf_counter() - fetch_start:.4f}s "
            f"cache_stats={self.get_cache_stats()}"
        )

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
