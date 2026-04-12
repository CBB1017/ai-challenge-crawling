import asyncio
import json
from loguru import logger
from redis.asyncio import Redis

# 각 이벤트 루프별로 Redis 클라이언트를 저장할 딕셔너리
_redis_clients = {}


def get_redis_client() -> Redis:
    """
    현재 실행 중인 이벤트 루프에 바인딩된 Redis 객체를 반환합니다.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # 실행 중인 루프가 없다면 기본 연결 반환 (주로 동기 컨텍스트용)
        return Redis.from_url("redis://localhost:6379/0", password='redispass', decode_responses=True)

    # 현재 루프용 Redis 객체가 없다면 새로 생성해서 저장
    if loop not in _redis_clients:
        _redis_clients[loop] = Redis.from_url(
            "redis://localhost:6379/0",
            password='redispass',
            decode_responses=True
            # 필요에 따라 비밀번호 등 추가 설정
        )

    return _redis_clients[loop]

# 🚀 async def 로 변경
async def get_session(username: str):
    """Redis에서 저장된 세션(쿠키)을 가져옵니다."""
    client = get_redis_client()
    try:
        cookies_bytes = await client.get(f"session:{username}")
        if cookies_bytes:
            # json.loads는 bytes 타입을 자동으로 디코딩해서 딕셔너리로 만들어줍니다.
            return json.loads(cookies_bytes)
        return None
    except Exception as e:
        logger.error(f"[{username}] Redis 조회 에러: {str(e)}")
        return None

# 🚀 async def 로 변경
async def save_session(username: str, cookies: dict, expire_seconds: int = 86400):
    """쿠키를 JSON 문자열로 변환하여 Redis에 저장합니다."""
    client = get_redis_client()
    try:
        await client.setex(
            name=f"session:{username}",
            time=expire_seconds,
            value=json.dumps(cookies, ensure_ascii=False)
        )
        logger.info(f"[{username}] Redis에 세션 저장 완료")
    except Exception as e:
        logger.error(f"[{username}] Redis 저장 에러: {str(e)}")