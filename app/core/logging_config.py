import os
import sys
from loguru import logger
from dotenv import load_dotenv

load_dotenv()

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

def init_logging():
    logger.remove()  # 기본 핸들러 제거 (중복 로그 방지)
    logger.add(sys.stdout, level=LOG_LEVEL, format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>", enqueue=True)
    if os.environ.get("DOCKERIZED", "0") == "1":
        log_path = "/app/logs/app.log"
    elif os.name == "nt":
        log_path = ".\\logs\\app.log"
    else:
        log_path = "./logs/app.log"  # 리눅스 로컬 개발일 수도 있음

    logger.add(log_path, level=LOG_LEVEL, rotation="10 MB", retention="10 days", encoding="utf-8", enqueue=True)