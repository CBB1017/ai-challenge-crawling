import functools
import json

from loguru import logger

from app.core.config import LOGIN_INFO
from app.session.session_manager import get_session


def requires_groupware_login(func):
    """프론트엔드에서 발급받은 유효한 세션(쿠키)이 없으면 즉시 에러를 반환하는 데코레이터"""

    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        # TODO: 실제 환경에서는 LOGIN_INFO 대신 request 컨텍스트나 헤더에서 유저 식별자를 가져오는 것이 좋습니다.
        username = LOGIN_INFO.get("username")

        if not username:
            return json.dumps({"status": "fail", "message": "사용자 식별자(username)가 누락되었습니다."}, ensure_ascii=False)

        # 1. 프론트엔드가 (로그인 시) 저장해둔 세션 확인
        cached_cookies = get_session(username)

        # 2. [핵심] 세션이 없으면 자동 로그인 시도 없이 '즉시 실패(Fail-Fast)' 반환
        if not cached_cookies:
            logger.warning(f"[{username}] 유효한 세션이 없습니다. 프론트엔드 로그인 리다이렉트 필요.")
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