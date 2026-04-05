"""
services/rate_limiter.py
─────────────────────────────────────────────────────────────────────────────
Production-grade rate limiter using Redis.

Drop-in replacement for the in-memory _RateLimiter in app.py.
Works correctly across multiple gunicorn workers, processes, and restarts.

Setup:
  pip install redis

Environment variable:
  REDIS_URL=redis://https://api.curabook.com:6379/0        # local
  REDIS_URL=redis://:password@host:6379/0  # with auth
  REDIS_URL=rediss://...                   # TLS (Upstash, Railway, Render)

If REDIS_URL is not set, falls back to in-memory limiter automatically
(safe for single-process local dev, NOT for multi-worker production).

Usage in app.py — replace the _RateLimiter class instantiation with:
  from services.rate_limiter import get_rate_limiter
  _limiter = get_rate_limiter()
"""

import os
import time
import logging
from collections import defaultdict
from threading import Lock

logger = logging.getLogger("phi.rate_limiter")


# ── Redis sliding-window limiter ──────────────────────────────────────────────

class RedisRateLimiter:
    """
    Sliding-window rate limiter backed by Redis.
    Thread-safe, process-safe, restart-safe.

    Algorithm: sorted set per key where each member is a timestamp.
    Atomically trims old entries and counts current window with a Lua script.
    """

    _LUA_SCRIPT = """
    local key     = KEYS[1]
    local now     = tonumber(ARGV[1])
    local window  = tonumber(ARGV[2])
    local limit   = tonumber(ARGV[3])
    local cutoff  = now - window

    -- Remove timestamps outside the window
    redis.call('ZREMRANGEBYSCORE', key, 0, cutoff)

    -- Count remaining
    local count = redis.call('ZCARD', key)

    if count >= limit then
        return 0
    end

    -- Add current timestamp (score = timestamp, member = timestamp:random)
    redis.call('ZADD', key, now, now .. ':' .. math.random(1000000))

    -- Set expiry so keys clean themselves up
    redis.call('EXPIRE', key, window + 10)

    return 1
    """

    def __init__(self, redis_url: str):
        import redis as redis_lib
        self._client = redis_lib.from_url(
            redis_url,
            decode_responses = True,
            socket_timeout   = 1.0,     # fail fast — never block a request
            socket_connect_timeout = 1.0,
        )
        self._script = self._client.register_script(self._LUA_SCRIPT)
        # Test connection
        self._client.ping()
        logger.info(f"[RATE] Redis rate limiter connected: {redis_url[:30]}…")

    def is_allowed(self, key: str, limit: int, window_seconds: int = 60) -> bool:
        try:
            result = self._script(
                keys = [f"rl:{key}"],
                args = [int(time.time() * 1000), window_seconds * 1000, limit],
            )
            return bool(result)
        except Exception as e:
            # Redis failure → fail open (allow the request)
            # Better to serve the request than to block everyone on Redis outage
            logger.warning(f"[RATE] Redis error (failing open): {e}")
            return True

    def cleanup(self):
        pass  # Redis handles TTL-based cleanup automatically


# ── In-memory fallback (single process only) ─────────────────────────────────

class InMemoryRateLimiter:
    """
    Sliding-window in-memory rate limiter.
    ONLY suitable for single-process deployments.
    Prints a warning if WORKER_COUNT > 1.
    """

    def __init__(self):
        self._windows: dict[str, list[float]] = defaultdict(list)
        self._lock = Lock()

    def is_allowed(self, key: str, limit: int, window_seconds: int = 60) -> bool:
        now    = time.time()
        cutoff = now - window_seconds
        with self._lock:
            calls = [t for t in self._windows[key] if t > cutoff]
            if len(calls) >= limit:
                return False
            calls.append(now)
            self._windows[key] = calls
            return True

    def cleanup(self):
        now = time.time()
        with self._lock:
            stale = [k for k, v in self._windows.items()
                     if not v or now - max(v) > 300]
            for k in stale:
                del self._windows[k]


# ── Factory function ──────────────────────────────────────────────────────────

def get_rate_limiter():
    """
    Returns a RedisRateLimiter if REDIS_URL is set and Redis is reachable.
    Falls back to InMemoryRateLimiter with a clear warning if not.
    """
    redis_url = os.getenv("REDIS_URL", "")

    if redis_url:
        try:
            limiter = RedisRateLimiter(redis_url)
            print("✅  Redis rate limiter active (multi-worker safe)")
            return limiter
        except Exception as e:
            print(f"⚠️  Redis rate limiter FAILED to connect: {e}")
            print("   Falling back to in-memory rate limiter.")
            print("   This is NOT safe for multi-worker production.")

    worker_count = int(os.getenv("WORKER_COUNT", "1"))
    if worker_count > 1:
        print(
            f"⚠️  WARNING: {worker_count} workers + in-memory rate limiter.\n"
            "   Effective rate limit is multiplied by worker count.\n"
            "   Set REDIS_URL in .env to fix this."
        )
    else:
        print("ℹ️  In-memory rate limiter active (single-process mode)")

    return InMemoryRateLimiter()