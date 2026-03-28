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
    logger.info("정적 로그인 시도 중...")
    async with semaphore:
        try:
            async with BaseCrawler(
                    LOGIN_INFO["login_url"], LOGIN_INFO["username"], LOGIN_INFO["password"]
            ) as crawler:
                success, cookies = await crawler.login()

                status = "success" if success else "fail"
                logger.info(f"로그인 결과: {status}")

                return json.dumps({
                    "status": status,
                    "message": "Login successful" if success else "Login failed",
                    "cookies": cookies
                })
        except Exception as e:
            logger.exception(f"로그인 도중 예외 발생: {e}")
            return json.dumps({"status": "error", "message": str(e)})


@mcp.tool()
@requires_groupware_login
async def get_team_attendance(
        ot_date: str,
        dept_name: str,
        **kwargs
) -> str:
    """팀 멤버들의 근태 기록을 조회합니다.
    (이 함수는 별도의 파라미터 입력이 필요하지 않으며, 시스템 설정값을 사용합니다.)
    """
    logger.info("팀 근태 기록 조회 시작")
    logger.debug(f"get_team_attendance 호출됨 - 날짜: {ot_date}, 부서: {dept_name}")

    async with semaphore:
        async with AttendanceCrawler(
                LOGIN_INFO["login_url"], LOGIN_INFO["username"], LOGIN_INFO["password"], kwargs.get('cookies')
        ) as crawler:
            try:
                target_date = ot_date if ot_date else date.today().isoformat()
                result = await crawler.fetch_attendance(
                    groupware_domain=LOGIN_INFO["login_url"],
                    ot_date=target_date,
                    dept_name=dept_name
                )
            except ValueError as e:
                # 부서명이 없는 경우에 대한 에러 응답 처리
                return json.dumps({"status": "fail", "message": str(e)}, ensure_ascii=False)
            logger.success(f"팀 근태 데이터 수집 완료 (팀 수: {len(result)})")
            return json.dumps(result, ensure_ascii=False)


@mcp.tool()
@requires_groupware_login
async def get_meeting_room_status(
        room_name: str,
        **kwargs
) -> str:
    """회의실 예약 현황을 조회합니다."""
    logger.info(f"회의실 조회 요청: {room_name}")
    async with semaphore:
        async with MeetingRoomCrawler(
                LOGIN_INFO["login_url"], LOGIN_INFO["username"], LOGIN_INFO["password"], kwargs.get('cookies')
        ) as crawler:
            result = await crawler.fetch_reservations(LOGIN_INFO["login_url"], room_name)
            logger.info(f"'{room_name}' 예약 현황 조회 성공")
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
    logger.info(f"OT 계산 시작 (데이터 개수: {len(attendance_data)})")
    calculator = OvertimeCalculator()
    result_dict = calculator.calculate_overtime(attendance_data)
    logger.success("OT 계산 완료")
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

# @mcp.tool()
# @requires_groupware_login
# async def request_overtime_approval(
#         request_data: dict, # LLM이 보낸 JSON 데이터를 dict로 받음
#         **kwargs
# ) -> str:
#     # Pydantic 모델로 파싱 및 검증 (Alias, Validator 작동)
#     data = OvertimeRequestModel(**request_data)
#     """근태 기록을 기반으로 잔업/특근 신청서를 자동 작성하고 임시저장/상신합니다."""
#     async with semaphore:
#         # 1. 사용자 정보 및 날짜 폴백 처리
#         actual_user = data.target_user_name if data.target_user_name else "문병찬"
#         actual_dept = data.dept_name if data.dept_name else "DX사업부"
#         actual_date = data.ot_date if data.ot_date else date.today().isoformat()
#         logger.debug(f"fetch_attendance 호출됨 - 날짜: {data.ot_date}, 부서: {data.dept_name}")
#         try:
#             async with ApprovalCrawler(
#                     LOGIN_INFO["login_url"], LOGIN_INFO["username"], LOGIN_INFO["password"], kwargs.get('cookies')
#             ) as crawler:
#                 result = await crawler.process_overtime_request(
#                     target_user_name=actual_user,
#                     dept_name=actual_dept,
#                     doc_type=data.doc_type,
#                     ot_date=actual_date,
#                     memo=data.memo
#                 )
#
#                 # 3. 폼 세팅 성공 시 최종 액션(상신/저장) 수행
#                 if result.get("status") == "success":
#                     # [추가] 브라우저 Confirm/Alert 창 자동 수락 설정
#                     # "저장하시겠습니까?" 또는 "상신하시겠습니까?" 창이 뜨면 자동으로 '확인' 클릭
#                     crawler.page.on("dialog", lambda dialog: asyncio.create_task(dialog.accept()))
#                     # 부모 페이지의 SendFlowData 함수 호출
#                     await crawler.page.evaluate(f"SendFlowData('{data.action_type}')")
#                     # 3. 네트워크 유휴 상태 및 페이지 전환 대기
#                     # 상신 후에는 목록 페이지 등으로 이동하므로 기다려줘야 안전합니다.
#                     try:
#                         await crawler.page.wait_for_load_state("load", timeout=10000)
#                     except Exception as e:
#                         logger.warning(f"페이지 전환 대기 중 타임아웃 발생: {e}")
#
#                     # 4. 리다이렉트 URL 검증 로직 적용
#                     final_url = crawler.page.url
#                     # 보통 상신 후에는 목록(Form_List)이나 보관함으로 이동합니다.
#                     if "Form_List" in final_url or "Doc_View" in final_url or "Main" in final_url:
#                         logger.info(f"성공적으로 리다이렉트 되었습니다. 현재 URL: {final_url}")
#                         action_name = "결재상신" if data.action_type == "F" else "임시저장"
#                         result["message"] = f"{actual_user}님의 OT 신청 {action_name} 완료 및 페이지 이동 확인"
#                     else:
#                         # URL이 그대로라면 상신 실패(유효성 검사 걸림 등)일 확률이 높습니다.
#                         logger.error(f"페이지가 이동하지 않았습니다. 상신 실패 의심. 현재 URL: {final_url}")
#                         result["status"] = "fail"
#                         result["message"] = "페이지 이동이 확인되지 않았습니다. 그룹웨어의 알림 메시지를 확인해주세요."
#
#                 return json.dumps(result, ensure_ascii=False)
#         except Exception as e:
#             logger.exception("OT 신청 중 치명적 오류 발생")
#             return json.dumps({"status": "error", "message": str(e)})


@mcp.tool()
@requires_groupware_login
async def request_overtime_approval(
        request_data: dict,
        **kwargs
) -> str:
    """
    perform_login은 먼저 수행하지 않아도 됩니다.
    잔업/특근 신청서를 자동 작성하고 임시저장/상신합니다.
    """

    # 1. Pydantic 모델 파싱 (Alias 및 Validator 적용)
    try:
        data = OvertimeRequestModel(**request_data)
    except Exception as e:
        logger.error(f"데이터 파싱 에러: {e}")
        return json.dumps({"status": "error", "message": f"입력 데이터 형식이 잘못되었습니다: {e}"})

    async with semaphore:
        # 2. 기본값 폴백 (Model에서 설정되지 않은 경우 대비)
        actual_user = data.target_user_name or "문병찬"
        actual_dept = data.dept_name or "DX사업부"
        actual_date = data.ot_date or date.today().isoformat()

        logger.info(f"OT 신청 시작 - 대상: {data.target_user_name}, 부서: {data.dept_name}, 날짜: {data.ot_date}")

        try:
            async with ApprovalCrawler(
                    LOGIN_INFO["login_url"], LOGIN_INFO["username"], LOGIN_INFO["password"], kwargs.get('cookies')
            ) as crawler:
                # 3. 폼 작성 및 결재선 설정 (이 내부에서 set_approval_line 등이 실행됨)
                # process_overtime_request가 내부에서 실패하면 이미 status='fail'인 result 반환
                result = await crawler.process_overtime_request(
                    target_user_name=actual_user,
                    dept_name=actual_dept,
                    doc_type=data.doc_type,
                    ot_date=actual_date,
                    memo=data.memo
                )

                # 4. 폼 세팅 성공 시 최종 액션(상신/저장) 수행
                if result.get("status") == "success":
                    action_name = "결재상신" if data.action_type == "F" else "임시저장"

                    # Dialog 핸들러: 레이스 컨디션 방지를 위해 비동기 래퍼 사용
                    async def handle_dialog(dialog):
                        try:
                            logger.info(f"브라우저 대화상자 감지 ({dialog.message}) -> 승인")
                            await dialog.accept()
                        except:
                            pass  # 이미 페이지가 닫혔거나 이동 중인 경우 무시

                    crawler.page.on("dialog", lambda d: asyncio.create_task(handle_dialog(d)))

                    # 최종 버튼 클릭 (SendFlowData 호출)
                    logger.info(f"최종 {action_name} 호출: SendFlowData('{data.action_type}')")
                    await crawler.page.evaluate(f"SendFlowData('{data.action_type}')")

                    # 5. 페이지 전환 및 리다이렉트 대기
                    try:
                        # 상신 후 목록으로 튕겨나가는 것을 기다림
                        await crawler.page.wait_for_load_state("networkidle", timeout=10000)
                    except Exception as e:
                        logger.warning(f"페이지 전환 대기 중 타임아웃(계속 진행): {e}")

                    # 6. 최종 URL 검증
                    final_url = crawler.page.url
                    # 'Doc_Write'가 여전히 URL에 있다면 상신 실패(필수값 누락 등) 가능성이 높음
                    if any(kw in final_url for kw in ["Form_List", "Doc_View", "Main"]):
                        logger.success(f"{action_name} 성공 확인. URL: {final_url}")
                        result["message"] = f"{actual_user}님의 OT 신청 {action_name} 완료"
                    elif "Doc_Write" in final_url:
                        logger.error(f"{action_name} 후에도 작성 페이지에 머물러 있음. 실패 의심.")
                        result.update({"status": "fail", "message": "상신 후 페이지가 이동하지 않았습니다. 필수 항목을 확인해주세요."})
                    else:
                        # 알 수 없는 페이지로 이동했더라도 일단 긍정적으로 판단 (이미 닫혔을 수 있음)
                        result["message"] = f"{actual_user}님의 OT 신청 {action_name} 처리됨 (확인 필요)"

                return json.dumps(result, ensure_ascii=False)

        except Exception as e:
            logger.exception("OT 신청 도구 실행 중 치명적 오류 발생")
            return json.dumps({"status": "error", "message": str(e)})