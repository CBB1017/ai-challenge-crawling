# server.py 수정
from functools import partial
from threading import Thread

import anyio
import uvicorn
from loguru import logger
from fastapi import FastAPI, HTTPException
from mcp.server import FastMCP
from pydantic import BaseModel

from app.core.server import semaphore, mcp
from app.core.utils import get_email_from_jwt
from app.crawler.base import BaseCrawler
from app.session.session_manager import save_session
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


# 1. FastAPI 먼저 선언 (로그인용)
app = FastAPI(title="Groupware Auth Proxy")
# FastAPI 서버 설정
config = uvicorn.Config(app, host="0.0.0.0", port=8000, log_level="info")
server = uvicorn.Server(config)

# 요청/응답 모델 정의
class LoginRequest(BaseModel):
    username: str
    password: str

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request, exc):
    logger.error(f"Validation Error: {exc.errors()}") # 여기서 에러 원인이 찍힙니다!
    return JSONResponse(
        status_code=422,
        content={"detail": exc.errors(), "body": exc.body},
    )

@app.post("/api/login")
async def login_endpoint(request: LoginRequest):
    """그룹웨어 로그인을 수행하고 유저 정보와 쿠키를 반환합니다."""
    logger.info(f"정적 로그인 시도 중... User: {request.username} password: {request.password}")
    async with semaphore:
        try:
            async with BaseCrawler(username=request.username, password=request.password) as crawler:
                success, cookies, access_token, user_info = await crawler.login()

                if not success:
                    return {
                        "status": "fail",
                        "message": "Login failed",
                        "user": None
                    }

                email = await get_email_from_jwt(access_token)
                save_session(email, cookies)
                # 성공 시 데이터 구조화
                return {
                    "status": "success",
                    "token": access_token,
                    "message": "Login successful",
                    "cookies": cookies,  # 리스트 형태의 쿠키
                    "user": {
                        "nameAndPosition": user_info.get("username"), # "문병찬 대리"
                        "dept": user_info.get("dept")          # "DX 2Team"
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
    mcp.run(transport="streamable-http")

if __name__ == "__main__":
    # 1. MCP 서버를 데몬 스레드로 시작
    mcp_thread = Thread(target=run_mcp, daemon=True)
    mcp_thread.start()

    # 2. 메인 스레드에서 FastAPI(Uvicorn) 실행
    # uvicorn.run은 호출 시 루프를 점유하며 서버를 유지합니다.
    uvicorn.run(app, host="0.0.0.0", port=8081)