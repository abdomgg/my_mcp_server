# -*- coding: utf-8 -*-
"""Distributed rate limiter.

Uses Redis (sliding-window via sorted sets) when available, with an
automatic in-memory token-bucket fallback so single-worker / no-Redis
deployments still work. No session affinity required.
"""
import logging
import threading
import time

from odoo import models

_logger = logging.getLogger(__name__)

# Process-local fallback store: {bucket_key: [(ts), ...]}
_MEM_LOCK = threading.Lock()
_MEM_WINDOWS = {}


def _get_redis(env):
    """Return a redis client or None. Config param mcp_server_pro.redis_url."""
    url = env["ir.config_parameter"].sudo().get_param(
        "mcp_server_pro.redis_url")
    if not url:
        return None
    try:
        import redis  # noqa
    except ImportError:
        _logger.debug("MCP: redis lib not installed, using in-memory limiter")
        return None
    try:
        client = redis.Redis.from_url(url, socket_timeout=0.25,
                                      socket_connect_timeout=0.25)
        client.ping()
        return client
    except Exception as e:
        _logger.warning("MCP: redis unavailable (%s), falling back", e)
        return None


class McpRateLimiter(models.AbstractModel):
    _name = "mcp.rate.limiter"
    _description = "MCP Rate Limiter"

    def check(self, key, per_sec, per_min):
        """Return (allowed: bool, retry_after: float).

        Enforces both windows. Returns the worst-case retry hint.
        """
        client = _get_redis(self.env)
        if client is not None:
            try:
                return self._check_redis(client, key, per_sec, per_min)
            except Exception as e:
                _logger.warning("MCP: redis limiter error (%s), falling back", e)
        return self._check_memory(key, per_sec, per_min)

    # ---- Redis sliding window -------------------------------------------
    def _check_redis(self, client, key, per_sec, per_min):
        now = time.time()
        pipe = client.pipeline()
        for window, limit in ((1.0, per_sec), (60.0, per_min)):
            if not limit:
                continue
            zkey = "mcp:rl:%s:%s" % (key, int(window))
            cutoff = now - window
            pipe.zremrangebyscore(zkey, 0, cutoff)
            pipe.zadd(zkey, {("%f-%s" % (now, id(self))): now})
            pipe.zcard(zkey)
            pipe.expire(zkey, int(window) + 1)
        results = pipe.execute()
        # results layout per window: [zrem, zadd, zcard, expire]
        idx = 0
        for window, limit in ((1.0, per_sec), (60.0, per_min)):
            if not limit:
                continue
            count = results[idx + 2]
            idx += 4
            if count > limit:
                return False, window
        return True, 0.0

    # ---- In-memory token window -----------------------------------------
    def _check_memory(self, key, per_sec, per_min):
        now = time.time()
        with _MEM_LOCK:
            for window, limit in ((1.0, per_sec), (60.0, per_min)):
                if not limit:
                    continue
                bucket = "%s:%s" % (key, window)
                stamps = _MEM_WINDOWS.setdefault(bucket, [])
                cutoff = now - window
                # Drop expired
                stamps[:] = [t for t in stamps if t > cutoff]
                if len(stamps) >= limit:
                    return False, window
                stamps.append(now)
        return True, 0.0
