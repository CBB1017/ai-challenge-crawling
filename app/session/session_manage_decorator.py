import functools
import json

from loguru import logger

from app.session.session_manager import get_session, save_session


def requires_cookies(func):
    """넘겨받은 파라미터에서 user_id를 추출해 세션을 검증하고 쿠키를 주입하는 데코레이터"""

    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        # 1. FastMCP Context 객체 찾기 (파라미터명이 ctx 또는 context일 경우 대비)
        mcp_ctx = kwargs.get("ctx") or kwargs.get("context")

        # 혹시 args로 넘어왔을 경우 방어적 탐색 (FastMCP Context는 request_context를 가짐)
        if mcp_ctx is None:
            for arg in args:
                if hasattr(arg, "request_context"):
                    mcp_ctx = arg
                    break

        if mcp_ctx is None:
            logger.warning("유효한 세션이 없습니다. MCP Context 객체를 찾지 못했습니다.")
            return json.dumps({
                "status": "error",
                "message": "MCP context가 전달되지 않았습니다."
            }, ensure_ascii=False)

        # 2. Spring AI가 toolContext()로 넘긴 meta 데이터 추출
        request_context = getattr(mcp_ctx, "request_context", None)
        meta = getattr(request_context, "meta", None)

        # 3. Meta 객체(Pydantic) 또는 dict 구조에서 안전하게 데이터 꺼내기
        if hasattr(meta, "model_extra") and meta.model_extra:
            # Pydantic V2 (MCP SDK 표준)의 extra 필드에서 추출
            meta_dict = meta.model_extra
        elif isinstance(meta, dict):
            meta_dict = meta
        else:
            meta_dict = getattr(meta, "__dict__", {})

        user_id = getattr(meta, "userId", meta_dict.get("userId"))
        dept = getattr(meta, "userDept", meta_dict.get("userDept"))
        name = getattr(meta, "userName", meta_dict.get("userName"))

        logger.info(f"[Context] 요청자 ID: {user_id}, 부서: {dept}, 이름: {name}")

        if not user_id:
            return json.dumps({"status": "fail", "message": "Context meta에 userId가 누락되었습니다."}, ensure_ascii=False)

        # 4. 프론트엔드가 (로그인 시) 저장해둔 세션 확인
        cached_cookies = await get_session(user_id)

        # 5. 세션이 없으면 자동 로그인 시도 없이 '즉시 실패(Fail-Fast)' 반환
        if not cached_cookies:
            logger.warning(f"[{user_id}] 유효한 세션이 없습니다. 프론트엔드 로그인 리다이렉트 필요.")
            return json.dumps({
                "status": "error",
                "code": "SESSION_EXPIRED",
                "message": "세션이 만료되었거나 존재하지 않습니다. 다시 로그인해주세요."
            }, ensure_ascii=False)

        # 6. 확보된 쿠키를 kwargs에 주입하여 원래 함수 실행
        kwargs['cookies'] = cached_cookies

        return await func(*args, **kwargs)

    return wrapper


def requires_system_session(func):
    """시스템 계정(LOGIN_INFO)의 세션을 사용하여 쿠키를 주입하는 데코레이터"""

    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        from app.core.config import LOGIN_INFO
        user_id = LOGIN_INFO["username"]

        # 1. Redis에서 세션 확인
        cached_cookies = await get_session(user_id)

        # 2. 세션이 없으면 자동 로그인 시도
        if not cached_cookies:
            logger.info(f"[{user_id}] 유효한 세션이 없습니다. 자동 로그인을 시도합니다.")
            from app.crawler.base import BaseCrawler
            
            # LOGIN_INFO의 계정 정보를 사용하여 로그인 시도
            async with BaseCrawler(user_id=user_id, password=LOGIN_INFO["password"]) as crawler:
                success, cookies, _ = await crawler.login()
                if success:
                    await save_session(user_id, cookies)
                    cached_cookies = cookies
                else:
                    logger.error(f"[{user_id}] 자동 로그인 실패")
                    return json.dumps({
                        "status": "error",
                        "code": "LOGIN_FAILED",
                        "message": "자동 로그인에 실패했습니다. 계정 정보를 확인해주세요."
                    }, ensure_ascii=False)

        # 3. 확보된 쿠키를 kwargs에 주입
        kwargs['cookies'] = cached_cookies

        return await func(*args, **kwargs)

    return wrapper