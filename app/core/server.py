import os
import asyncio
import json
import holidays

from dotenv import load_dotenv
from playwright.async_api import async_playwright
from loguru import logger
from typing import Optional, List, Dict, Any
from mcp.server.fastmcp import FastMCP
from fastapi import FastAPI

from datetime import datetime, timedelta, date
from collections import defaultdict

from app.core.scheduler import is_weekend, is_holiday, annotate_member_status, all_checked_in, should_reset_today, \
    calculate_dynamic_schedule_times
from app.crawler.attendance import AttendanceCrawler
from app.crawler.meeting import MeetingRoomCrawler
from app.crawler.member import MemberCrawler
from app.crawler.overtime import OvertimeCalculator

load_dotenv()
# --- 1. BaseCrawler 개선 (Context Manager 적용) ---
class BaseCrawler:
    def __init__(self, login_url: str, username: str, password: str, cookies: list = None):
        self.login_url = login_url
        self.username = username
        self.password = password
        self.cookies = cookies or []  # 외부에서 전달받은 세션 쿠키

        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None

    async def __aenter__(self):
        self.playwright = await async_playwright().start()
        cdp_endpoint = os.environ.get("CDP_ENDPOINT", f"ws://localhost:3001?token={os.environ.get('TOKEN', '')}")

        for attempt in range(3):
            try:
                self.browser = await self.playwright.chromium.connect_over_cdp(cdp_endpoint, timeout=10000)
                break
            except Exception as e:
                if attempt == 2:
                    logger.error(f"CDP 연결 최종 실패: {e}")
                    await self.__aexit__(None, None, None)
                    raise
                await asyncio.sleep(1)

        self.context = await self.browser.new_context()

        # 💡 핵심: 전달받은 쿠키가 있다면 컨텍스트에 주입 (로그인 상태 복원)
        if self.cookies:
            await self.context.add_cookies(self.cookies)
            logger.info("기존 세션 쿠키를 주입했습니다.")

        self.page = await self.context.new_page()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        # 자원 해제 타임아웃 적용
        async def _cleanup():
            if self.page: await self.page.close()
            if self.context: await self.context.close()
            if self.browser: await self.browser.close()
            if self.playwright: await self.playwright.stop()

        try:
            await asyncio.wait_for(_cleanup(), timeout=5.0)
        except asyncio.TimeoutError:
            logger.warning("[WARNING] 브라우저 자원 정리 타임아웃")

    async def wait_for_frame(self, name, timeout=10):
        interval = 0.2
        for _ in range(int(timeout / interval)):
            frame = self.page.frame(name=name)
            if frame: return frame
            await asyncio.sleep(interval)
        return None

    async def login(self, frame_name="mainFrame"):
        try:
            await self.page.goto(self.login_url)
            frame = await self.wait_for_frame(frame_name)
            if not frame:
                logger.error("[ERROR] 프레임을 찾을 수 없습니다.")
                return False, []

            await frame.fill('input[name="UserID"]', self.username)
            await frame.fill('input[name="UserPass"]', self.password)
            await frame.click('button[type="button"].ibtn')

            try:
                await frame.wait_for_selector('img[src*="btn_logout.gif"]', timeout=5000)
                logger.info("[INFO] 로그인 성공")

                # 로그인 성공 후 세션 쿠키 추출
                cookies = await self.context.cookies()
                return True, cookies

            except Exception:
                logger.error("[ERROR] 로그인 실패 (로그아웃 버튼 없음)")
                return False, []
        except Exception as e:
            logger.error(f"[ERROR] 로그인 실패: {e}")
            return False, []


# --- 2. MCP 서버 및 동시성 제어 설정 ---
MAX_CONCURRENT_TASKS = 5  # 최대 병렬 실행 갯수 제한
semaphore = asyncio.Semaphore(MAX_CONCURRENT_TASKS)

mcp = FastMCP("crawler-server")

@mcp.tool()
async def perform_login(login_url: str, username: str, password: str) -> str:
    """대상 시스템에 로그인하고 세션 쿠키를 JSON 형태로 반환합니다."""
    async with semaphore:
        async with BaseCrawler(login_url, username, password) as crawler:
            success, cookies = await crawler.login()

            if success:
                return json.dumps({
                    "status": "success",
                    "message": "Login successful",
                    "cookies": cookies
                })
            else:
                return json.dumps({
                    "status": "fail",
                    "message": "Login failed",
                    "cookies": []
                })

@mcp.tool()
async def get_team_attendance(
        login_url: str,
        username: str,
        password: str,
        groupware_domain: str,
        cookies: Optional[List[Dict[str, Any]]] = None
) -> str:
    """
    조직도 HTML을 분석하여 팀 멤버들의 근태 기록을 조회합니다.
    기존 세션(쿠키)이 있다면 재사용하여 속도를 높입니다.
    """
    async with semaphore:
        async with AttendanceCrawler(login_url, username, password, cookies) as crawler:
            result = await crawler.fetch_attendance(groupware_domain)

            # Spring이 파싱할 수 있도록 JSON 문자열로 직렬화하여 반환
            # (한글 깨짐 방지를 위해 ensure_ascii=False 사용)
            return json.dumps(result, ensure_ascii=False)

@mcp.tool()
async def get_meeting_room_status(
    login_url: str,
    username: str,
    password: str,
    groupware_domain: str,
    room_name: str = None,
    cookies: Optional[List[Dict[str, Any]]] = None
) -> str:
    """
    그룹웨어의 회의실 예약 현황 테이블을 크롤링하여 JSON으로 반환합니다.
    """
    async with semaphore:
        async with MeetingRoomCrawler(login_url, username, password, cookies) as crawler:
            result = await crawler.fetch_reservations(groupware_domain, room_name)
            return json.dumps(result, ensure_ascii=False)

@mcp.tool()
async def get_team_members(
    login_url: str,
    username: str,
    password: str,
    groupware_domain: str,
    cookies: Optional[List[Dict[str, Any]]] = None
) -> str:
    """
    조직도(팀 및 멤버정보)와 이메일 정보를 병합하여 JSON으로 반환합니다.
    """
    async with semaphore:
        async with MemberCrawler(login_url, username, password, cookies) as crawler:
            result = await crawler.fetch_members(groupware_domain)
            return json.dumps(result, ensure_ascii=False)

@mcp.tool()
def calculate_overtime_data(attendance_data: list[dict]) -> str:
    """
    크롤링된 근태 데이터를 기반으로 초과 근무(OT) 및 부족 근무 상쇄 결과를 계산합니다.
    """
    calculator = OvertimeCalculator()
    result_dict = calculator.calculate_overtime(attendance_data)

    return json.dumps(result_dict, ensure_ascii=False)


@mcp.tool()
async def process_attendance_and_get_schedules(login_url: str, username: str, password: str) -> str:
    """근태 크롤링을 수행하고 상태 주석, 스케줄링 필요 정보를 반환합니다."""
    today = date.today()
    mute = is_weekend(today) or is_holiday(today)

    # 1. 크롤링 실행 (기존 BaseCrawler 로직 활용)
    crawler = AttendanceCrawler(login_url, username, password)
    data = await crawler.run_attendance()

    if not data:
        return json.dumps({"status": "fail", "message": "데이터 없음"})

    # 2. 상태 주석 달기
    for team, members in data.items():
        data[team] = annotate_member_status(members, datetime.now(), mute_not_checked_in=mute)

    # 3. 플래그 및 동적 시간 계산
    is_all_checked_in = all_checked_in(data)
    is_should_reset = should_reset_today(data)
    next_schedules = calculate_dynamic_schedule_times(data)

    result = {
        "status": "success",
        "data": data,
        "is_holiday_or_weekend": mute,
        "all_checked_in": is_all_checked_in,
        "should_reset_today": is_should_reset,
        "dynamic_schedule_times": next_schedules  # ["2026-03-27T18:40:00", ...]
    }

    return json.dumps(result, ensure_ascii=False)

# 3. FastAPI 연동
app = FastAPI(title="MCP Crawler Server")

# Spring Webflux 연동에 최적화된 Streamable HTTP 앱으로 마운트 (SSE도 자동 지원)
app.mount("/mcp", mcp.streamable_http_app())

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)