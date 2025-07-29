"""Redis utility for caching"""

import json
import logging
import os
from typing import Any, Optional

import redis

from gefapi.config import SETTINGS

logger = logging.getLogger(__name__)


class RedisCache:
    """Redis cache utility for storing and retrieving cached data"""

    def __init__(self):
        self._client = None
        self._initialize_client()

    def _initialize_client(self):
        """Initialize Redis client"""
        try:
            # Get Redis URL from environment or config
            redis_url = (
                os.getenv("REDIS_URL")
                or SETTINGS.get("REDIS_URL")
                or (
                    f"redis://{os.getenv('REDIS_PORT_6379_TCP_ADDR', 'localhost')}:"
                    f"{os.getenv('REDIS_PORT_6379_TCP_PORT', '6379')}"
                )
            )
            self._client = redis.from_url(
                redis_url,
                decode_responses=True,
                socket_connect_timeout=5,
                socket_timeout=5,
                retry_on_timeout=True,
            )

            # Test connection
            self._client.ping()
            logger.info("Redis cache client initialized successfully")

        except Exception as e:
            logger.error(f"Failed to initialize Redis cache client: {e}")
            self._client = None

    @property
    def client(self) -> Optional[redis.Redis]:
        """Get Redis client, reinitialize if needed"""
        if self._client is None:
            self._initialize_client()
        return self._client

    def is_available(self) -> bool:
        """Check if Redis is available"""
        try:
            if self.client:
                self.client.ping()
                return True
        except Exception as e:
            logger.warning(f"Redis not available: {e}")
        return False

    def set(self, key: str, value: Any, ttl: int = 300) -> bool:
        """
        Set a value in Redis cache

        Args:
            key: Cache key
            value: Value to cache (will be JSON serialized)
            ttl: Time to live in seconds (default: 5 minutes)

        Returns:
            bool: True if successful, False otherwise
        """
        try:
            if not self.client:
                return False

            # Serialize value to JSON
            json_value = json.dumps(value, default=str)

            # Set with TTL
            self.client.setex(key, ttl, json_value)
            logger.debug(f"Cached value for key '{key}' with TTL {ttl}s")
            return True

        except Exception as e:
            logger.error(f"Failed to cache value for key '{key}': {e}")
            return False

    def get(self, key: str) -> Optional[Any]:
        """
        Get a value from Redis cache

        Args:
            key: Cache key

        Returns:
            Cached value or None if not found/error
        """
        try:
            if not self.client:
                return None

            json_value = self.client.get(key)
            if json_value is None:
                return None

            # Deserialize from JSON
            value = json.loads(json_value)
            logger.debug(f"Retrieved cached value for key '{key}'")
            return value

        except Exception as e:
            logger.error(f"Failed to retrieve cached value for key '{key}': {e}")
            return None

    def delete(self, key: str) -> bool:
        """
        Delete a value from Redis cache

        Args:
            key: Cache key

        Returns:
            bool: True if successful, False otherwise
        """
        try:
            if not self.client:
                return False

            result = self.client.delete(key)
            logger.debug(f"Deleted cache key '{key}', result: {result}")
            return result > 0

        except Exception as e:
            logger.error(f"Failed to delete cache key '{key}': {e}")
            return False

    def exists(self, key: str) -> bool:
        """
        Check if a key exists in Redis cache

        Args:
            key: Cache key

        Returns:
            bool: True if key exists, False otherwise
        """
        try:
            if not self.client:
                return False

            return bool(self.client.exists(key))

        except Exception as e:
            logger.error(f"Failed to check existence of cache key '{key}': {e}")
            return False

    def get_ttl(self, key: str) -> int:
        """
        Get remaining TTL for a key

        Args:
            key: Cache key

        Returns:
            int: TTL in seconds, -1 if key doesn't exist, -2 if no TTL set
        """
        try:
            if not self.client:
                return -1

            return self.client.ttl(key)

        except Exception as e:
            logger.error(f"Failed to get TTL for cache key '{key}': {e}")
            return -1


# Global Redis cache instance
redis_cache = RedisCache()


def get_redis_cache() -> RedisCache:
    """Get the global Redis cache instance"""
    return redis_cache
