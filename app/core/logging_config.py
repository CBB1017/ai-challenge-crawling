import os
import sys
from loguru import logger
from dotenv import load_dotenv
from app.core.otel import get_trace_id

load_dotenv()

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

def init_logging():
    logger.remove()  # 기본 핸들러 제거 (중복 로그 방지)
    
    # Trace ID를 로그에 포함시키기 위한 패처(Patcher)
    logger.configure(patcher=lambda record: record["extra"].update(trace_id=get_trace_id() or "0" * 32))
    
    log_format = "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{extra[trace_id]}</cyan> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>"
    
    logger.add(sys.stdout, level=LOG_LEVEL, format=log_format, enqueue=True)
    if os.environ.get("DOCKERIZED", "0") == "1":
        log_path = "/app/logs/app.log"
    elif os.name == "nt":
        log_path = ".\\logs\\app.log"
    else:
        log_path = "./logs/app.log"  # 리눅스 로컬 개발일 수도 있음

    logger.add(log_path, level=LOG_LEVEL, format=log_format, rotation="10 MB", retention="10 days", encoding="utf-8", enqueue=True)