import asyncio
import sys
import time
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi import Request
from loguru import logger

from app.api.router import router
from app.core.logging_config import init_logging

if sys.platform.startswith("win"):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

init_logging()

logger.info("서비스 시작!")


app = FastAPI()
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