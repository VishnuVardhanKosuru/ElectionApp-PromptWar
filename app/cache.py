"""
In-memory cache for Google Civic Information API responses.

Uses ``cachetools.TTLCache`` (time-to-live + LRU eviction) wrapped in a
thread-safe lock so concurrent async workers don't corrupt the cache state.

Cache keys are SHA-256 hashes of the (endpoint, params) tuple so they are
safe for use as dictionary keys and avoid leaking raw addresses in memory.

Usage::

    from app.cache import get_cache
    cache = get_cache()

    key = cache.make_key("elections", {})
    if (cached := cache.get(key)) is not None:
        return cached
    result = await client.get_elections()
    cache.set(key, result)
    return result
"""

import hashlib
import json
import logging
import threading
from typing import Any, Optional

from cachetools import TTLCache

logger = logging.getLogger(__name__)

# Module-level singleton – initialised by ``init_cache`` at startup.
_cache_instance: Optional["CivicResponseCache"] = None
_cache_lock = threading.Lock()


class CivicResponseCache:
    """Thread-safe TTL cache for raw Civic API JSON responses.

    Attributes:
        _cache: Underlying ``cachetools.TTLCache`` instance.
        _lock: ``threading.Lock`` protecting concurrent access.
        _hits: Cumulative cache hit counter.
        _misses: Cumulative cache miss counter.
    """

    def __init__(self, maxsize: int = 512, ttl: int = 300) -> None:
        """Initialise the cache.

        Args:
            maxsize: Maximum number of items to store before LRU eviction.
            ttl: Time-to-live in seconds for each cache entry.
        """
        self._cache: TTLCache = TTLCache(maxsize=maxsize, ttl=ttl)
        self._lock = threading.Lock()
        self._hits: int = 0
        self._misses: int = 0
        logger.info(
            "CivicResponseCache initialised.",
            extra={"maxsize": maxsize, "ttl_seconds": ttl},
        )

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    @staticmethod
    def make_key(endpoint: str, params: dict[str, Any]) -> str:
        """Derive a stable, opaque cache key from an endpoint + params pair.

        Args:
            endpoint: API endpoint path string (e.g. ``"elections"``).
            params: Query parameter dict used for the request.

        Returns:
            A hex-encoded SHA-256 digest string.
        """
        raw = json.dumps(
            {"endpoint": endpoint, "params": params}, sort_keys=True
        )
        return hashlib.sha256(raw.encode()).hexdigest()

    def get(self, key: str) -> Optional[dict[str, Any]]:
        """Retrieve a cached value.

        Args:
            key: Cache key produced by :meth:`make_key`.

        Returns:
            Cached dict or ``None`` on a cache miss / expired entry.
        """
        with self._lock:
            value = self._cache.get(key)
            if value is not None:
                self._hits += 1
                logger.debug("Cache HIT.", extra={"key_prefix": key[:12]})
                return value
            self._misses += 1
            logger.debug("Cache MISS.", extra={"key_prefix": key[:12]})
            return None

    def set(self, key: str, value: dict[str, Any]) -> None:
        """Store a value in the cache.

        Args:
            key: Cache key produced by :meth:`make_key`.
            value: Raw Civic API response dict to store.
        """
        with self._lock:
            self._cache[key] = value
        logger.debug("Cache SET.", extra={"key_prefix": key[:12]})

    def stats(self) -> dict[str, Any]:
        """Return current cache statistics.

        Returns:
            Dict with ``hits``, ``misses``, ``size``, and ``maxsize`` keys.
        """
        with self._lock:
            return {
                "hits": self._hits,
                "misses": self._misses,
                "size": len(self._cache),
                "maxsize": self._cache.maxsize,
            }

    def clear(self) -> None:
        """Evict all cached entries (useful in tests)."""
        with self._lock:
            self._cache.clear()
        logger.info("Cache cleared.")


def init_cache(maxsize: int = 512, ttl: int = 300) -> None:
    """Initialise the module-level cache singleton.

    Must be called once during application startup (e.g. inside the FastAPI
    lifespan handler) before the first request is processed.

    Args:
        maxsize: Maximum cache size (items).
        ttl: Time-to-live in seconds.
    """
    global _cache_instance  # noqa: PLW0603
    with _cache_lock:
        _cache_instance = CivicResponseCache(maxsize=maxsize, ttl=ttl)


def get_cache() -> CivicResponseCache:
    """Return the module-level cache singleton.

    Returns:
        The initialised ``CivicResponseCache`` instance.

    Raises:
        RuntimeError: If :func:`init_cache` has not been called yet.
    """
    if _cache_instance is None:
        raise RuntimeError(
            "Cache has not been initialised. "
            "Call app.cache.init_cache() during application startup."
        )
    return _cache_instance
