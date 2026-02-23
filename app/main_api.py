from fastapi import FastAPI
from contextlib import asynccontextmanager
from app.core.scheduler import scheduler, init_scheduler
from app.api.router import router
from app.core.logging_config import init_logging
from fastapi import Request
from loguru import logger

import time
import asyncio
import sys

if sys.platform.startswith("win"):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

init_logging()

logger.info("서비스 시작!")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_scheduler()
    scheduler.start()
    logger.info("Scheduler started.")
    yield
    scheduler.shutdown()
    logger.info("Scheduler stopped.")


app = FastAPI(lifespan=lifespan)
app.include_router(router)


@app.get("/")
async def root():
    return {"message": "FastAPI + APScheduler + Lifespan!"}


@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = time.perf_counter()
    logger.info(f"요청: {request.method} {request.url}")
    response = await call_next(request)
    duration = (time.perf_counter() - start) * 1000  # ms 단위
    logger.info(
        f"응답: status={response.status_code} "
        f"[{request.method} {request.url.path}] "
        f"걸린 시간: {duration:.2f}ms"
    )
    return response