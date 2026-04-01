import functools
import json

from loguru import logger

from app.core.config import LOGIN_INFO
from app.session.session_manager import get_session


def requires_cookies(func):
    """넘겨받은 파라미터에서 user_id를 추출해 세션을 검증하고 쿠키를 주입하는 데코레이터"""

    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        context = None
        # args 중 metadata 가진 객체 찾기
        for arg in args:
            if hasattr(arg, "metadata"):
                context = arg
                break

        if context is None:
            context = kwargs.get("context")

        if context is None:
            return json.dumps({
                "status": "error",
                "message": "MCP context가 전달되지 않았습니다."
            }, ensure_ascii=False)

        metadata = getattr(context, "metadata", {}) or {}
        user = metadata.get("user", {})

        userId = user.get("id")
        dept = user.get("dept")
        name = user.get("name")

        if not userId:
            return json.dumps({"status": "fail", "message": "metadata.user.id 누락"}, ensure_ascii=False)

        # 1. 프론트엔드가 (로그인 시) 저장해둔 세션 확인
        cached_cookies = get_session(userId)

        # 2. 세션이 없으면 자동 로그인 시도 없이 '즉시 실패(Fail-Fast)' 반환
        if not cached_cookies:
            logger.warning(f"[{userId}] 유효한 세션이 없습니다. 프론트엔드 로그인 리다이렉트 필요.")
            return json.dumps({
                "status": "error",
                "code": "SESSION_EXPIRED",
                "message": "세션이 만료되었거나 존재하지 않습니다. 다시 로그인해주세요."
            }, ensure_ascii=False)

        # 3. 확보된 쿠키를 kwargs에 주입하여 원래 함수 실행
        kwargs['cookies'] = cached_cookies
        # 첫 번째 인자가 OvertimeRequestModel 객체인 경우 해당 객체에 직접 주입
        if args and hasattr(args[0], 'cookies'):
            args[0].cookies = cached_cookies

        return await func(*args, **kwargs)

    return wrapper