import json
from loguru import logger
from redis.asyncio import Redis

# Redis 클라이언트 연결 (환경에 맞게 host, port, db, password 등을 수정하세요)
try:
    redis_client = Redis(host='localhost', port=6379, db=0, password='redispass')
    # 연결 테스트 (서버 구동 시점에 에러를 빨리 잡기 위함)
    redis_client.ping()
    logger.info("Redis 연결 성공!")
except Exception as e:
    logger.error(f"Redis 연결 실패: {str(e)}")

# 🚀 async def 로 변경
async def get_session(username: str):
    """Redis에서 저장된 세션(쿠키)을 가져옵니다."""
    try:
        cookies_bytes = await redis_client.get(f"session:{username}")
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
    try:
        await redis_client.setex(
            name=f"session:{username}",
            time=expire_seconds,
            value=json.dumps(cookies, ensure_ascii=False)
        )
        logger.info(f"[{username}] Redis에 세션 저장 완료")
    except Exception as e:
        logger.error(f"[{username}] Redis 저장 에러: {str(e)}")