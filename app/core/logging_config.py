import os
import sys
import logging
from loguru import logger
from dotenv import load_dotenv
from app.core.otel import get_trace_id

load_dotenv()

# LOG_LEVEL을 대문자로 정규화하여 설정
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

class InterceptHandler(logging.Handler):
    """
    표준 logging 모듈의 로그를 loguru로 전달하는 핸들러
    """
    def emit(self, record):
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        frame, depth = logging.currentframe(), 2
        while frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back
            depth += 1

        logger.opt(depth=depth, exception=record.exc_info).log(level, record.getMessage())

def init_logging():
    # 1. 표준 logging 설정을 loguru로 리다이렉션
    logging.basicConfig(handlers=[InterceptHandler()], level=0, force=True)
    
    # 특정 라이브러리의 로그 레벨 조정 (너무 상세한 로그 방지)
    for logger_name in ["uvicorn", "uvicorn.access", "fastapi", "httpx"]:
        logging.getLogger(logger_name).handlers = [InterceptHandler()]
        logging.getLogger(logger_name).propagate = False

    # 2. loguru 설정
    logger.remove()  # 기본 핸들러 제거
    
    # Trace ID를 로그에 포함시키기 위한 패처(Patcher)
    logger.configure(patcher=lambda record: record["extra"].update(trace_id=get_trace_id() or "0" * 32))
    
    log_format = "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{extra[trace_id]}</cyan> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>"
    
    # 콘솔 출력
    logger.add(sys.stdout, level=LOG_LEVEL, format=log_format, enqueue=True)
    
    # 파일 출력
    if os.environ.get("DOCKERIZED", "0") == "1":
        log_path = "/app/logs/app.log"
    elif os.name == "nt":
        log_path = ".\\logs\\app.log"
    else:
        log_path = "./logs/app.log"

    logger.add(log_path, level=LOG_LEVEL, format=log_format, rotation="10 MB", retention="10 days", encoding="utf-8", enqueue=True)
    
    logger.info(f"Logging initialized with level: {LOG_LEVEL}")