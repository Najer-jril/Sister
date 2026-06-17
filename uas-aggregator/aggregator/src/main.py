"""FastAPI application entrypoint with lifespan: init DB pool, Redis client, and worker pool."""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from src.api.events import router as events_router
from src.api.health import router as health_router
from src.api.publish import router as publish_router
from src.api.stats import router as stats_router
from src.broker import close_redis, create_redis
from src.consumer.worker import start_workers, stop_workers
from src.database import close_pool, create_pool
from src.utils.logging import configure_logging

configure_logging()


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.db_pool = await create_pool()
    app.state.redis = await create_redis()
    await start_workers(app.state)
    yield
    await stop_workers(app.state)
    await close_redis(app.state.redis)
    await close_pool(app.state.db_pool)


app = FastAPI(
    title="UAS Pub-Sub Log Aggregator",
    version="2.0.0",
    lifespan=lifespan,
)

app.include_router(publish_router)
app.include_router(events_router)
app.include_router(stats_router)
app.include_router(health_router)
