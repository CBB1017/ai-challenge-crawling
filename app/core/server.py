import asyncio
import json
from datetime import datetime, date

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

from app.core.config import LOGIN_INFO
from app.core.scheduler import is_weekend, is_holiday, annotate_member_status, all_checked_in, should_reset_today, \
    calculate_dynamic_schedule_times
from app.crawler.attendance import AttendanceCrawler
from app.crawler.base import BaseCrawler
from app.crawler.meeting import MeetingRoomCrawler
from app.crawler.member import MemberCrawler
from app.crawler.overtime import OvertimeCalculator
from app.session.session_manage_decorator import requires_groupware_login

load_dotenv()

# --- 2. MCP 서버 및 동시성 제어 설정 ---
MAX_CONCURRENT_TASKS = 5  # 최대 병렬 실행 갯수 제한
semaphore = asyncio.Semaphore(MAX_CONCURRENT_TASKS)

mcp = FastMCP("crawler-server")

@mcp.tool()
async def perform_login() -> str:
    """대상 시스템에 로그인하고 세션 쿠키를 JSON 형태로 반환합니다."""
    async with semaphore:
        async with BaseCrawler(
                LOGIN_INFO["login_url"], LOGIN_INFO["username"], LOGIN_INFO["password"]
        ) as crawler:
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
@requires_groupware_login
async def get_team_attendance(
        **kwargs
) -> str:
    """팀 멤버들의 근태 기록을 조회합니다.
    (이 함수는 별도의 파라미터 입력이 필요하지 않으며, 시스템 설정값을 사용합니다.)
    """

    async with semaphore:
        async with AttendanceCrawler(
                LOGIN_INFO["login_url"], LOGIN_INFO["username"], LOGIN_INFO["password"], kwargs.get('cookies')
        ) as crawler:
            result = await crawler.fetch_attendance(LOGIN_INFO["login_url"])
            return json.dumps(result, ensure_ascii=False)


@mcp.tool()
@requires_groupware_login
async def get_meeting_room_status(
        room_name: str,
        **kwargs
) -> str:
    """회의실 예약 현황을 조회합니다."""
    async with semaphore:
        async with MeetingRoomCrawler(
                LOGIN_INFO["login_url"], LOGIN_INFO["username"], LOGIN_INFO["password"], kwargs.get('cookies')
        ) as crawler:
            result = await crawler.fetch_reservations(LOGIN_INFO["login_url"], room_name)
            return json.dumps(result, ensure_ascii=False)


@mcp.tool()
@requires_groupware_login
async def get_team_members(
        **kwargs
) -> str:
    """조직도(팀 및 멤버정보)와 이메일 정보를 병합하여 JSON으로 반환합니다.
     (이 함수는 별도의 파라미터 입력이 필요하지 않으며, 시스템 설정값을 사용합니다.)
    """
    async with semaphore:
        async with MemberCrawler(
                LOGIN_INFO["login_url"], LOGIN_INFO["username"], LOGIN_INFO["password"], kwargs.get('cookies')
        ) as crawler:
            result = await crawler.fetch_members(LOGIN_INFO["login_url"])
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
@requires_groupware_login
async def process_attendance_and_get_schedules(
        **kwargs
) -> str:
    """
    근태 크롤링 후 스케줄링 필요 정보를 반환합니다.
    (이 함수는 별도의 파라미터 입력이 필요하지 않으며, 시스템 설정값을 사용합니다.)
    """
    today = date.today()
    mute = is_weekend(today) or is_holiday(today)
    # 1. 크롤링 실행
    async with AttendanceCrawler(
            LOGIN_INFO["login_url"], LOGIN_INFO["username"], LOGIN_INFO["password"], kwargs.get('cookies')
    ) as crawler:
        data = await crawler.fetch_attendance(LOGIN_INFO["login_url"])

    if not data:
        return json.dumps({"status": "fail", "message": "데이터 없음"})

    # 2. 상태 주석 달기
    for team, members in data.items():
        data[team] = annotate_member_status(members, datetime.now(), mute_not_checked_in=mute)

    # 3. 플래그 및 동적 시간 계산 후 리턴
    result = {
        "status": "success",
        "data": data,
        "is_holiday_or_weekend": mute,
        "all_checked_in": all_checked_in(data),
        "should_reset_today": should_reset_today(data),
        "dynamic_schedule_times": calculate_dynamic_schedule_times(data)
    }

    return json.dumps(result, ensure_ascii=False)