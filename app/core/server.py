import asyncio
import contextvars
import json
from datetime import datetime, date

from dotenv import load_dotenv
from loguru import logger
from mcp.server.fastmcp import FastMCP, Context
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
from app.session.session_manager import get_session

load_dotenv()

# --- 2. MCP 서버 및 동시성 제어 설정 ---
MAX_CONCURRENT_TASKS = 5  # 최대 병렬 실행 갯수 제한
semaphore = asyncio.Semaphore(MAX_CONCURRENT_TASKS)
# 현재 비동기 요청(Request) 흐름 내에서만 유지
current_username = contextvars.ContextVar("current_username", default=None)
mcp = FastMCP("crawler-server")

@mcp.tool()
async def perform_login(
    username: str,
    password: str,
) -> str:
    """대상 시스템에 로그인하고 세션 쿠키를 JSON 형태로 반환합니다."""
    logger.info("정적 로그인 시도 중...")
    async with semaphore:
        try:
            async with BaseCrawler(username=username, password=password) as crawler:
                success, cookies, user_info = await crawler.login()

                status = "success" if success else "fail"
                logger.info(f"로그인 결과: {status}")

                username = user_info.get("username")  # "문병찬"
                dept = user_info.get("dept")  # "DX 2Team"

                return json.dumps({
                    "status": status,
                    "message": "Login successful" if success else "Login failed",
                    "cookies": cookies,
                    "user": {"username": username, "dept": dept}
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
        async with AttendanceCrawler(kwargs.get('cookies')) as crawler:
            try:
                target_date = ot_date if ot_date else date.today().isoformat()
                result = await crawler.fetch_attendance(
                    groupware_domain=LOGIN_INFO["domain"],
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
                LOGIN_INFO["domain"], LOGIN_INFO["username"], LOGIN_INFO["password"], kwargs.get('cookies')
        ) as crawler:
            result = await crawler.fetch_reservations(LOGIN_INFO["domain"], room_name)
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
                LOGIN_INFO["domain"], LOGIN_INFO["username"], LOGIN_INFO["password"], kwargs.get('cookies')
        ) as crawler:
            result = await crawler.fetch_members(LOGIN_INFO["domain"])
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
            LOGIN_INFO["domain"], LOGIN_INFO["username"], LOGIN_INFO["password"], kwargs.get('cookies')
    ) as crawler:
        data = await crawler.fetch_attendance(LOGIN_INFO["domain"])

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
async def request_overtime_approval(
        request_data: dict,
        **kwargs
) -> str:
    """
    그룹웨어에서 잔업(OT) 또는 특근 신청서를 자동으로 작성하고 임시저장하거나 결재를 상신합니다.

    [사용 시기]
    - 사용자가 "잔업 신청해줘", "특근 올려줘", "주말 근무 상신해", "OT 올려줘", "초과근무 올려줘", "야근 올려줘" 등의 요청을 할 때 호출합니다.
    - 일반 휴가, 연차, 출장 신청에는 이 툴을 사용하지 마세요.

    [주의 사항]
    - perform_login 툴을 먼저 호출할 필요가 없습니다. (내부에서 처리됨)
    - target_user_name과 dept_name은 시스템 컨텍스트의 로그인 유저 정보를 사용하세요.
    - action_type은 반드시 결재상신이면 'F', 임시저장이면 'T'로 매핑해야 합니다.
    - ot_date는 반드시 'YYYY-MM-DD' 포맷이어야 합니다.
    """

    # ---------------------------------------------------------
    # 2. 세션 검증 (데코레이터가 하던 역할)
    # ---------------------------------------------------------
    cached_cookies = get_session(username)
    if not cached_cookies:
        return json.dumps({
            "status": "error",
            "code": "SESSION_EXPIRED",
            "message": "세션이 만료되었습니다. 다시 로그인해주세요."
        }, ensure_ascii=False)

    # 1. Pydantic 모델 파싱 (Alias 및 Validator 적용)
    try:
        data = OvertimeRequestModel(**request_data)
    except Exception as e:
        logger.error(f"데이터 파싱 에러(LLM 파라미터 누락): {str(e)}")
        # LLM에게 어떤 필드가 누락되었는지 피드백을 주어 스스로 수정하게 유도
        return json.dumps({
            "status": "error",
            "message": "필수 파라미터가 누락되었거나 형식이 틀렸습니다. 시스템 컨텍스트에서 로그인 유저의 부서(dept_name)와 이름(target_user_name)을 확인하여 다시 호출해주세요.",
            "details": str(e)
        }, ensure_ascii=False)
    async with semaphore:
        # 2. 하드코딩된 폴백 제거 -> Pydantic 검증을 통과한 순수 데이터만 사용
        actual_user = data.target_user_name
        actual_dept = data.dept_name
        actual_date = data.ot_date

        logger.info(f"OT 신청 시작 - 대상: {actual_user}, 부서: {actual_dept}, 날짜: {actual_date}, 액션: {data.action_type}")
        try:
            async with ApprovalCrawler(kwargs.get('cookies')) as crawler:
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

                    # 5. 페이지 전환 및 리다이렉트 대기 (액션별 분기 처리)
                    try:
                        if data.action_type == "F":
                            # [결재상신] 파라미터 순서 무관하게 필수 키워드 포함 여부 확인
                            await crawler.page.wait_for_function("""
                                () => {
                                    const url = window.location.href;
                                    return url.includes('/Flow/Doc_List') && 
                                           url.includes('Sign=F') && 
                                           url.includes('isTemp=N');
                                }
                            """, timeout=15000)
                        else:
                            # [임시저장] 임시보관함 목록으로 이동 대기
                            await crawler.page.wait_for_url("**/Flow/DocBox_List?Gubun=T*", timeout=15000)
                    except Exception as e:
                        logger.warning(f"페이지 전환 대기 중 타임아웃(계속 진행): {e}")

                    # 6. 최종 URL 검증
                    final_url = crawler.page.url

                    # 액션 타입에 따른 성공 URL 키워드 설정
                    is_success = False
                    if data.action_type == "F" and "Doc_List" in final_url and "Sign=F" in final_url:
                        is_success = True
                    elif data.action_type != "F" and "DocBox_List" in final_url and "Gubun=T" in final_url:
                        is_success = True

                    # 'Doc_Write'가 여전히 URL에 있다면 상신 실패(필수값 누락 등) 가능성이 높음
                    if is_success:
                        logger.success(f"{action_name} 성공 확인. URL: {final_url}")
                        result["message"] = f"{actual_user}님의 OT 신청 {action_name} 완료"
                    elif "Doc_Write" in final_url:
                        logger.error(f"{action_name} 후에도 작성 페이지에 머물러 있음. 실패 의심.")
                        result.update({"status": "fail", "message": "상신 후 페이지가 이동하지 않았습니다. 필수 항목을 확인해주세요."})

                return json.dumps(result, ensure_ascii=False)

        except Exception as e:
            logger.exception("OT 신청 도구 실행 중 치명적 오류 발생")
            return json.dumps({"status": "error", "message": str(e)})