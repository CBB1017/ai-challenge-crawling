import asyncio
import os
from contextlib import asynccontextmanager

import uvicorn
from loguru import logger
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from app.core.config import LoggingMiddleware
from app.core.otel import setup_otel
from app.core.server import mcp
from app.crawler.base import BaseCrawler, cdp_manager
from app.session.session_manager import save_session
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    FastAPI의 시작과 종료 시점에 비동기 자원을 관리합니다.
    모든 서비스(CDP, MCP)는 여기서 동일한 이벤트 루프를 공유하며 시작됩니다.
    """
    # 1. CDP 및 Playwright 초기화
    logger.info("🚀 Pre-initializing CDP and Playwright...")
    await cdp_manager.start()

    # 2. MCP 서버를 백그라운드 태스크로 실행
    # MCP도 동일한 루프를 사용하므로 락(Lock)이나 세마포어 충돌이 없습니다.
    logger.info("🚀 Starting MCP Server as background task...")
    mcp_task = None
    try:
        # FastMCP의 비동기 실행 메서드 호출 시도
        if hasattr(mcp, "run_streamable_http_async"):
            mcp_task = asyncio.create_task(mcp.run_streamable_http_async())
        else:
            logger.warning("mcp.run_streamable_http_async() not found. Running MCP in default mode...")
            # 비동기 실행이 불가능한 경우에 대비 (일반적으로는 위 메서드가 존재함)
            # mcp.run()이 블로킹이면 여기서 문제가 될 수 있으므로 주의 필요
            mcp_task = asyncio.create_task(asyncio.to_thread(mcp.run, transport="streamable-http"))
    except Exception as e:
        logger.error(f"❌ MCP 서버 시작 실패: {e}")

    yield

    # 종료 시 처리
    if mcp_task:
        mcp_task.cancel()
        logger.info("🛑 MCP Server task cancelled.")

# FastAPI 앱 설정
app = FastAPI(title="Groupware Auth Proxy", lifespan=lifespan)
setup_otel(app)
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
    logger.info(f"정적 로그인 시도 중 (User: {request.userId})...")
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

if __name__ == "__main__":
    # 환경 변수에서 포트 읽기
    port = int(os.environ.get("PORT", 8081))
    
    # 메인 스레드에서 Uvicorn 실행
    # uvicorn.run은 현재 루프를 시작하고 lifespan을 실행합니다.
    logger.info(f"🌐 Starting FastAPI server on port {port}")
    uvicorn.run(app, host="0.0.0.0", port=port)
