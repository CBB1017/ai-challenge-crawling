import asyncio
import json
from datetime import datetime, date
from typing import Any

from dotenv import load_dotenv
from loguru import logger
from mcp.server.fastmcp import FastMCP

from app.core.config import LOGIN_INFO
from app.core.scheduler import is_weekend, is_holiday, annotate_member_status, all_checked_in, should_reset_today, \
    calculate_dynamic_schedule_times
from app.crawler.approval import ApprovalCrawler
from app.crawler.attendance import AttendanceCrawler
from app.crawler.base import BaseCrawler
from app.crawler.meeting import MeetingRoomCrawler
from app.crawler.member import MemberCrawler
from app.crawler.overtime import OvertimeCalculator
from app.models.models import OvertimeRequestModel
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


@mcp.tool()
@requires_groupware_login
async def request_overtime_approval(
    target_user_name: str = "",
    dept_name: str = "",
    doc_type: str = "OT",
    ot_date: str = "",
    memo: str = ".",
    action_type: str = "T",
    **kwargs
) -> str:
    """
        근태 기록을 기반으로 잔업/특근 신청서를 자동 작성하고 임시저장/상신합니다.

        Args:
            target_user_name: OT 대상자 이름 (미입력 시 본인)
            dept_name: 결재라인 부서명 (예: DX사업부)
            doc_type: 결재 양식 종류 (기본: OT)
            ot_date: OT 일자 (YYYY-MM-DD, 미입력 시 오늘)
            memo: 연장근로 사유 (기본: .)
            action_type: T(임시저장), F(결재상신)
        """
    async with semaphore:
        cookies = kwargs.get('cookies')
        actual_user_name = target_user_name or "문병찬"
        actual_dept_name = dept_name or  "DX사업부"
        async with ApprovalCrawler(cookies=cookies) as crawler:
            # 1. 근태 검증 및 폼 세팅
            result = await crawler.process_overtime_request(
                target_user_name=actual_user_name,
                dept_name=actual_dept_name,
                doc_type=doc_type,
                ot_date=ot_date,
                memo=memo
            )

            # 3. 폼 세팅 성공 시 최종 액션(상신/저장) 수행
            if result.get("status") == "success":
                # [추가] 브라우저 Confirm/Alert 창 자동 수락 설정
                # "저장하시겠습니까?" 또는 "상신하시겠습니까?" 창이 뜨면 자동으로 '확인' 클릭
                crawler.page.on("dialog", lambda dialog: asyncio.create_task(dialog.accept()))
                # 부모 페이지의 SendFlowData 함수 호출
                await crawler.page.evaluate(f"SendFlowData('{action_type}')")
                # 3. 네트워크 유휴 상태 및 페이지 전환 대기
                # 상신 후에는 목록 페이지 등으로 이동하므로 기다려줘야 안전합니다.
                try:
                    await crawler.page.wait_for_load_state("load", timeout=10000)
                except Exception as e:
                    logger.warning(f"페이지 전환 대기 중 타임아웃 발생: {e}")

                # 4. 리다이렉트 URL 검증 로직 적용
                final_url = crawler.page.url
                # 보통 상신 후에는 목록(Form_List)이나 보관함으로 이동합니다.
                if "Form_List" in final_url or "Doc_View" in final_url or "Main" in final_url:
                    logger.info(f"성공적으로 리다이렉트 되었습니다. 현재 URL: {final_url}")
                    action_name = "결재상신" if action_type == "F" else "임시저장"
                    result["message"] = f"{actual_user_name}님의 OT 신청 {action_name} 완료 및 페이지 이동 확인"
                else:
                    # URL이 그대로라면 상신 실패(유효성 검사 걸림 등)일 확률이 높습니다.
                    logger.error(f"페이지가 이동하지 않았습니다. 상신 실패 의심. 현재 URL: {final_url}")
                    result["status"] = "fail"
                    result["message"] = "페이지 이동이 확인되지 않았습니다. 그룹웨어의 알림 메시지를 확인해주세요."

            return json.dumps(result, ensure_ascii=False)