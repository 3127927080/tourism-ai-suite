"""
Redis 异步客户端单例，仅用于 Pub/Sub 发布/订阅实时事件。
"""

import redis.asyncio as redis

from atguigu.common.config import get_settings

redis_client = redis.from_url(
    get_settings().redis_url,
    decode_responses=True
)
