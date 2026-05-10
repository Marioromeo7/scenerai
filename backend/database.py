import logging

logger = logging.getLogger(__name__)

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from redis.asyncio import Redis
from config import settings

engine = create_async_engine(settings.database_url, pool_size=10, max_overflow=20, echo=False)
AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session


_redis: Redis | None = None


async def get_redis() -> Redis:
    global _redis
    if _redis is None:
        _redis = Redis.from_url(settings.redis_url, encoding="utf-8", decode_responses=True)
        logger.info("Redis connection established")
    return _redis


async def close_connections():
    global _redis
    if _redis:
        await _redis.aclose()
        logger.info("Redis connection closed")
    await engine.dispose()
    logger.info("Database engine disposed")
