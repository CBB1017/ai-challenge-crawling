import asyncio
from threading import Thread
from contextlib import asynccontextmanager

import uvicorn
from loguru import logger
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from app.core.config import LoggingMiddleware
from app.core.server import mcp
from app.crawler.base import BaseCrawler, cdp_manager
from app.session.session_manager import save_session
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

@asynccontextmanager
async def lifespan(app: FastAPI):
    # FastAPI 시작 시 CDP 연결 초기화
    logger.info("🚀 Pre-initializing CDP for FastAPI...")
    asyncio.create_task(cdp_manager.start())
    yield

# 1. FastAPI 먼저 선언 (로그인용)
app = FastAPI(title="Groupware Auth Proxy", lifespan=lifespan)
# FastAPI 서버 설정
config = uvicorn.Config(app, host="0.0.0.0", port=8000, log_level="info")
server = uvicorn.Server(config)
# 미들웨어 추가
app.add_middleware(LoggingMiddleware)

# 요청/응답 모델 정의
class LoginRequest(BaseModel):
    userId: str
    password: str

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request, exc):
    logger.error(f"Validation Error: {exc.errors()}")
    return JSONResponse(
        status_code=422,
        content={"detail": exc.errors(), "body": exc.body},
    )

@app.post("/api/login")
async def login_endpoint(request: LoginRequest):
    """그룹웨어 로그인을 수행하고 유저 정보와 쿠키를 반환합니다."""
    logger.info(f"정적 로그인 시도 중...")
    try:
        async with BaseCrawler(user_id=request.userId, password=request.password) as crawler:
            success, cookies, data = await crawler.login()

            if not success:
                error_message = (data or {}).get("error") or "로그인 시 오류가 발생했습니다."
                return {
                    "status": "fail",
                    "message": error_message,
                    "user": None
                }

            await save_session(data.get("userId"), cookies)

            # 성공 시 데이터 구조화
            return {
                "status": "success",
                "message": "Login successful",
                "cookies": cookies,  # 리스트 형태의 쿠키
                "user": {
                    "nameAndPosition": data.get("username"), # "문병찬 대리"
                    "dept": data.get("dept"),          # "DX 2Team"
                    "userId": data.get("userId")         # "bc.mun"
                }
            }

    except Exception as e:
        logger.exception(f"로그인 도중 예외 발생: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/status")
async def status():
    return {"status": "FastAPI is running"}

def run_mcp():
    # MCP는 별도 스레드에서 차단(blocking) 방식으로 실행
    # 실행 직전에 비동기로 CDP 초기화 시도
    async def _start():
        logger.info("🚀 Pre-initializing CDP for MCP...")
        await cdp_manager.start()
        
        # mcp.run()은 내부에서 anyio.run()을 호출하여 새 루프를 만들려고 시도함 (에러 원인)
        # 대신 내부의 비동기 실행 메서드를 직접 호출하여 현재 루프를 공유합니다.
        try:
            # FastMCP의 내부 비동기 실행 메서드 호출
            await mcp.run_streamable_http_async()
        except AttributeError:
            # 혹시나 메서드명이 다를 경우를 대비한 fallback
            logger.warning("mcp.run_streamable_http_async() not found, falling back to mcp.run()")
            # 이 경우 루프 충돌을 피하기 위해 run()을 호출하기 전에 루프를 종료하거나 다른 방식이 필요
            mcp.run(transport="streamable-http")
    
    asyncio.run(_start())

if __name__ == "__main__":
    # 1. MCP 서버를 데몬 스레드로 시작
    mcp_thread = Thread(target=run_mcp, daemon=True)
    mcp_thread.start()

    # 2. 메인 스레드에서 FastAPI(Uvicorn) 실행
    # uvicorn.run은 호출 시 루프를 점유하며 서버를 유지합니다.
    uvicorn.run(app, host="0.0.0.0", port=8081)