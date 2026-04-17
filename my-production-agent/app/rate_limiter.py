import time
import redis
from fastapi import HTTPException
from .config import settings

# Initialize Redis client globally or per request depending on architecture. We'll use global pool.
r = redis.from_url(settings.REDIS_URL, decode_responses=True)

def check_rate_limit(user_id: str):
    now = time.time()
    key = f"rate_limit:{user_id}"
    window = 60
    max_reqs = settings.RATE_LIMIT_PER_MINUTE

    # Remove older ones
    r.zremrangebyscore(key, 0, now - window)
    count = r.zcard(key)

    if count >= max_reqs:
        raise HTTPException(status_code=429, detail="Rate limit exceeded. Try again later.")

    r.zadd(key, {str(now): now})
    r.expire(key, window)
