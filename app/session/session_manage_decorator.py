import functools
import json

from app.core.config import LOGIN_INFO
from app.crawler.base import BaseCrawler
from app.session.session_manager import get_session, save_session


def requires_groupware_login(func):
    """캐싱된 세션이 없으면 로그인을 먼저 수행하는 데코레이터"""

    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        # 툴의 인자에서 정보를 추출 (kwargs 또는 args 순서에 따라)
        username = LOGIN_INFO["username"]
        password = LOGIN_INFO["password"]
        login_url = LOGIN_INFO["login_url"]

        if not username:
            return json.dumps({"status": "fail", "message": "Username is required"})

        # 1. 세션 확인
        cached_cookies = get_session(username)

        # 2. 세션이 없으면 로그인 시도
        if not cached_cookies:
            async with BaseCrawler(login_url, username, password) as crawler:
                success, new_cookies = await crawler.login()
                if success:
                    save_session(username, new_cookies)
                    cached_cookies = new_cookies
                else:
                    return json.dumps({"status": "fail", "message": "Groupware login failed"})

        # 3. 확보된 쿠키를 kwargs에 주입하여 원래 함수 실행
        kwargs['cookies'] = cached_cookies
        return await func(*args, **kwargs)

    return wrapper