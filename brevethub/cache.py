"""Caching module for Team Asha Randonneuring."""
from flask_caching import Cache

# Cache timeout in seconds - change this value to adjust cache duration everywhere
CACHE_TIMEOUT = 300  # 5 minutes

# Initialize cache instance (will be configured in app.py)
cache = Cache()

def init_cache(app):
    """Initialize caching with app configuration."""
    cache.init_app(app, config={
        'CACHE_TYPE': 'SimpleCache',  # In-memory cache for Vercel serverless
        'CACHE_DEFAULT_TIMEOUT': CACHE_TIMEOUT,
    })
    return cache

def make_cache_key(*args, **kwargs):
    """Generate cache key from function arguments."""
    key_parts = []
    for arg in args:
        if arg is not None:
            key_parts.append(str(arg))
    for k, v in sorted(kwargs.items()):
        if v is not None:
            key_parts.append(f"{k}:{v}")
    return "_".join(key_parts)

def clear_cache_on_write():
    """Clear all caches when data is modified (signups, ride updates, etc.)."""
    cache.clear()
