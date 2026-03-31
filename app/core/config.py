import os
from dotenv import load_dotenv
import time
import json
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from loguru import logger

load_dotenv()

LOGIN_INFO = {
    "domain": os.environ.get("GROUPWARE_DOMAIN"),
    "username": os.environ.get("LOGIN_ID"),
    "password": os.environ.get("LOGIN_PW"),
}



class LoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # 1. Request 로깅 (Body 복구 포함)
        start_time = time.time()
        req_body_bytes = await request.body()
        request._body = req_body_bytes  # API 핸들러에서 다시 읽을 수 있게 저장

        try:
            req_json = json.loads(req_body_bytes) if req_body_bytes else {}
        except:
            req_json = req_body_bytes.decode() if req_body_bytes else ""

        logger.info(f"REQ: {request.method} {request.url.path} | Body: {req_json}")

        # 2. 다음 프로세스 진행
        response = await call_next(request)

        # 3. Response Body 추출 (에러 해결 핵심)
        res_body_bytes = b""

        # IDE 경고를 피하기 위해 getattr 사용 또는 타입 체크
        if hasattr(response, "body_iterator"):
            async for chunk in response.body_iterator:
                res_body_bytes += chunk
        else:
            # 일반 Response 객체일 경우 직접 접근
            res_body_bytes = getattr(response, "body", b"")

        # 로그 기록
        try:
            res_content = json.loads(res_body_bytes)
        except:
            res_content = res_body_bytes.decode()

        process_time = (time.time() - start_time) * 1000
        logger.info(f"RES: {response.status_code} | {process_time:.2f}ms | Body: {res_content}")

        # 4. 새로운 응답 객체 반환 (스트림을 이미 소모했으므로 필수)
        return Response(
            content=res_body_bytes,
            status_code=response.status_code,
            headers=dict(response.headers),
            media_type=response.media_type
        )