import asyncio
import contextvars
import json
from datetime import date
from typing import Optional

from dotenv import load_dotenv
from loguru import logger
from mcp.server.fastmcp import FastMCP, Context

from app.core.utils import clean_user_name
from app.crawler.approval import ApprovalCrawler
from app.crawler.attendance import AttendanceCrawler
from app.crawler.email import EmailCrawler
from app.crawler.meeting import MeetingRoomCrawler
from app.crawler.overtime import OvertimeCalculator, get_list_for_submission, get_summary_for_report
from app.models.models import OvertimeRequestModel, LeaveRequestModel
from app.session.session_manage_decorator import requires_cookies

load_dotenv()

# 현재 비동기 요청(Request) 흐름 내에서만 유지
current_username = contextvars.ContextVar("current_username", default=None)
mcp = FastMCP("crawler-server")


@mcp.tool()
@requires_cookies
async def get_team_attendance(
        ot_date: Optional[str] = None,
        ctx: Context = None,
        cookies: list = None
) -> str:
    """
        팀 멤버들의 근태 기록을 조회합니다.
        결재 상신(request_overtime_approval)과는 연관이 없습니다.
        [주의] ot_date는 사용자가 명시적으로 날짜를 언급한 경우에만 YYYY-MM-DD 형식으로 입력하고, 언급이 없다면 절대 유추하지 말고 비워두세요.
    """
    logger.info("팀 근태 기록 조회 시작")
    # 1. Meta에서 안전하게 user_id 추출
    try:
        meta = getattr(ctx.request_context, 'meta', {}) or {}

        user_id = getattr(meta, "userId", None) if meta else None
        user_name = getattr(meta, "userName", None) if meta else None
        dept_name = getattr(meta, "userDept", None) if meta else None
        if not user_id or not user_name:
            raise ValueError("userId 또는 userName이 meta 정보에 없습니다.")
    except Exception as e:
        logger.error(f"데이터 파싱 에러(LLM 파라미터 누락): {str(e)}")
        # LLM에게 어떤 필드가 누락되었는지 피드백을 주어 스스로 수정하게 유도
        return json.dumps({
            "status": "error",
            "message": "필수 파라미터가 누락되었거나 형식이 틀렸습니다. 시스템 컨텍스트에서 로그인 유저의 부서(userDept)와 이름(user_id)을 확인하여 다시 호출해주세요.",
            "details": str(e)
        }, ensure_ascii=False)
    logger.debug(f"get_team_attendance 호출됨 - 날짜: {ot_date}, 부서: {dept_name}")

    async with AttendanceCrawler(cookies, user_id) as crawler:
        try:
            target_date = ot_date if ot_date else date.today().isoformat()
            result = await crawler.fetch_attendance(
                ot_date=target_date,
                dept_name=dept_name
            )
        except ValueError as e:
            # 부서명이 없는 경우에 대한 에러 응답 처리
            return json.dumps({"status": "fail", "message": str(e)}, ensure_ascii=False)
        logger.success(f"팀 근태 데이터 수집 완료 (팀 수: {len(result)})")
        return json.dumps(result, ensure_ascii=False)


@mcp.tool()
@requires_cookies
async def get_meeting_room_status(
        room_name: str,
        ctx: Context = None,
        cookies: list = None
) -> str:
    """회의실 예약 현황을 조회합니다."""
    logger.info(f"회의실 조회 요청: {room_name}")
    async with MeetingRoomCrawler(cookies) as crawler:
        result = await crawler.fetch_reservations(room_name)
        logger.info(f"'{room_name}' 예약 현황 조회 성공")
        return json.dumps(result, ensure_ascii=False)


# @mcp.tool()
# @requires_cookies
# async def get_team_members(
#         ctx: Context = None,
#         cookies: list = None
# ) -> str:
#     """조직도(팀 및 멤버정보)와 이메일 정보를 병합하여 JSON으로 반환합니다.
#      (이 함수는 별도의 파라미터 입력이 필요하지 않으며, 시스템 설정값을 사용합니다.)
#     """
#     async with MemberCrawler(cookies) as crawler:
#         result = await crawler.fetch_members(LOGIN_INFO["domain"])
#         return json.dumps(result, ensure_ascii=False)


# @mcp.tool()
# @requires_cookies
# async def calculate_overtime_data(
#         attendance_data: list[dict],
#         ctx: Context = None,
#         cookies: list = None
# ) -> str:
#     """
#     크롤링된 근태 데이터를 기반으로 초과 근무(OT) 및 부족 근무 상쇄 결과를 계산합니다.
#     """
#     logger.info(f"OT 계산 시작 (데이터 개수: {len(attendance_data)})")
#     calculator = OvertimeCalculator()
#     result_dict = calculator.calculate_overtime(attendance_data)
#     logger.success("OT 계산 완료")
#     return json.dumps(result_dict, ensure_ascii=False)


# @mcp.tool()
# @requires_cookies
# async def process_attendance_and_get_schedules(
#         ctx: Context = None,
#         cookies: dict = None
# ) -> str:
#     """
#     근태 크롤링 후 스케줄링 필요 정보를 반환합니다.
#     (이 함수는 별도의 파라미터 입력이 필요하지 않으며, 시스템 설정값을 사용합니다.)
#     """
#     today = date.today()
#     mute = is_weekend(today) or is_holiday(today)
#     # 1. 크롤링 실행
#     async with AttendanceCrawler(cookies) as crawler:
#         data = await crawler.fetch_attendance(ot_date, dept_name)
#
#     if not data:
#         return json.dumps({"status": "fail", "message": "데이터 없음"})
#
#     # 2. 상태 주석 달기
#     for team, members in data.items():
#         data[team] = annotate_member_status(members, datetime.now(), mute_not_checked_in=mute)
#
#     # 3. 플래그 및 동적 시간 계산 후 리턴
#     result = {
#         "status": "success",
#         "data": data,
#         "is_holiday_or_weekend": mute,
#         "all_checked_in": all_checked_in(data),
#         "should_reset_today": should_reset_today(data),
#         "dynamic_schedule_times": calculate_dynamic_schedule_times(data)
#     }
#
#     return json.dumps(result, ensure_ascii=False)

@mcp.tool()
@requires_cookies
async def overtime_calculate_monthly(
        target_year: str,
        target_month: str,
        ctx: Context = None,
        cookies: list = None
) -> str:
    """
    월별 OT(초과근무=야근) 총 시간을 계산하는 크롤링 도구입니다.

    [사용 시기]
    - 사용자가 "잔업", "특근", "주말 근무", "OT", "초과근무", "야근" 등의 단어와 함께 "계산","계산해줘" 요청을 할 때 호출합니다.
    """
    # 1. Pydantic 모델 파싱 (Alias 및 Validator 적용)
    try:
        meta = getattr(ctx.request_context, 'meta', {}) or {}

        user_id = getattr(meta, "userId", None) if meta else None
        user_name = getattr(meta, "userName", None) if meta else None
        dept_name = getattr(meta, "userDept", None) if meta else None
        if not user_id or not user_name:
            raise ValueError("userId 또는 userName이 meta 정보에 없습니다.")
    except Exception as e:
        logger.error(f"데이터 파싱 에러(LLM 파라미터 누락): {str(e)}")
        # LLM에게 어떤 필드가 누락되었는지 피드백을 주어 스스로 수정하게 유도
        return json.dumps({
            "status": "error",
            "message": "필수 파라미터가 누락되었거나 형식이 틀렸습니다. 시스템 컨텍스트에서 로그인 유저의 부서(userDept)와 이름(user_id)을 확인하여 다시 호출해주세요.",
            "details": str(e)
        }, ensure_ascii=False)

    logger.info(
        f"월별 OT 시간 계산 시작 - 대상: {user_name}, 부서: {dept_name}, 날짜: {target_year}-{target_month}")
    try:
        # 근태 크롤러를 열어서 데이터만 딱 가져오고 바로 닫음 (락 해제)
        async with AttendanceCrawler(cookies, user_id) as att_crawler:
            soup = await att_crawler.get_attendance_personal_monthly(
                year=target_year,
                month=target_month
            )
        if not soup:
            print("근태 데이터를 가져올 수 없습니다.")
            return json.dumps({"status": "fail", "message": f"{target_month}월 근태 데이터를 가져올 수 없습니다."})

        # 2. 파싱 및 초과근무 계산
        logger.info(f"[{target_month}월] 근태 테이블 파싱 및 계산기 가동")
        parsed_data = att_crawler.parse_attendance_table_dynamic(soup)
        logger.info(f"{parsed_data} 근태 테이블 파싱 및 계산기 가동")
        calculator = OvertimeCalculator()
        attendance_data = parsed_data[0] if isinstance(parsed_data, tuple) else parsed_data

        analyzed_data = calculator.analyze_attendance(attendance_data, exclude_approved=False)

        report_data = get_summary_for_report(analyzed_data)
        # 1. 파싱 및 계산 코어 가동 (결재된 내역 제외)
        # 2. 보고서 전용 데이터 추출

        # 3. JSON으로 변환하여 LLM에게 전달 (LLM이 알아서 요약해서 답변)
        return json.dumps({
            "status": "success",
            "data": report_data
        }, ensure_ascii=False)
    except Exception as e:
        logger.exception("월별 OT 시간 계산 도구 실행 중 오류")
        return json.dumps({"status": "error", "message": str(e)}, ensure_ascii=False)


@mcp.tool()
@requires_cookies
async def request_overtime_approval(
        request_data: dict,  # dict가 아닌 Pydantic 모델 지정
        ctx: Context = None,
        cookies: list = None  # 🚀 데코레이터가 주입해주는 값을 받을 자리!
) -> str:
    """
    그룹웨어에서 잔업(OT) 또는 특근 신청서를 자동으로 작성하고 임시저장하거나 결재를 상신합니다.

    [사용 시기]
    - 사용자가 "잔업 신청해줘", "특근 올려줘", "주말 근무 상신해", "OT 올려줘", "초과근무 올려줘", "야근 올려줘" 등의 요청을 할 때 호출합니다.
    - 일반 휴가, 연차, 출장 신청에는 이 툴을 사용하지 마세요.

    [주의 사항]
    - perform_login 툴을 먼저 호출할 필요가 없습니다. (내부에서 처리됨)
    - action_type은 반드시 결재상신이면 'F', 임시저장이면 'T'로 매핑해야 합니다.
    - ot_date는 반드시 'YYYY-MM-DD' 포맷이어야 합니다. 클라이언트가 다른 형식으로 전달했으면 이 포맷으로 변경 후 시도하세요.
    """
    # 1. Pydantic 모델 파싱 (Alias 및 Validator 적용)
    try:
        data = OvertimeRequestModel(**request_data)
        meta = getattr(ctx.request_context, 'meta', {}) or {}

        user_id = getattr(meta, "userId", None) if meta else None
        user_name = getattr(meta, "userName", None) if meta else None
        dept_name = getattr(meta, "userDept", None) if meta else None
        raw_user_name = clean_user_name(user_name)
        if not user_id or not user_name:
            raise ValueError("userId 또는 userName이 meta 정보에 없습니다.")
    except Exception as e:
        logger.error(f"데이터 파싱 에러(LLM 파라미터 누락): {str(e)}")
        # LLM에게 어떤 필드가 누락되었는지 피드백을 주어 스스로 수정하게 유도
        return json.dumps({
            "status": "error",
            "message": "필수 파라미터가 누락되었거나 형식이 틀렸습니다. 시스템 컨텍스트에서 로그인 유저의 부서(userDept)와 이름(user_id)을 확인하여 다시 호출해주세요.",
            "details": str(e)
        }, ensure_ascii=False)

    logger.info(
        f"OT 신청 시작 - 대상: {user_name}, 부서: {dept_name}, 유형: {data.request_type}, 날짜: {data.ot_date},{data.target_year}-{data.target_month}, 액션: {data.action_type}")
    try:
        ot_data_list = []  # 월 단위 데이터를 담을 변수

        # ==========================================
        # 1. [데이터 획득 페이즈] AttendanceCrawler 단독 실행
        # ==========================================
        if data.request_type == "monthly":
            # 근태 크롤러를 열어서 데이터만 딱 가져오고 바로 닫음 (락 해제)
            async with AttendanceCrawler(cookies, user_id) as att_crawler:
                soup = await att_crawler.get_attendance_personal_monthly(
                    year=data.target_year,
                    month=data.target_month
                )
            if not soup:
                print("근태 데이터를 가져올 수 없습니다.")
                return json.dumps({"status": "fail", "message": f"{data.target_month}월 근태 데이터를 가져올 수 없습니다."})

            # 2. 파싱 및 초과근무 계산
            logger.info(f"[{data.target_month}월] 근태 테이블 파싱 및 계산기 가동")
            parsed_data = att_crawler.parse_attendance_table_dynamic(soup)

            # 반환값이 튜플(attendance_data, ot_summary)인지 단일 리스트인지에 따른 방어 처리
            attendance_data = parsed_data[0] if isinstance(parsed_data, tuple) else parsed_data
            # 1. 파싱 및 계산 코어 가동 (결재된 내역 제외)
            analyzed_data = OvertimeCalculator().analyze_attendance(attendance_data, exclude_approved=True)
            # 2. 결재 폼 전용 데이터 추출
            ot_data_list = get_list_for_submission(analyzed_data, memo=data.memo)

            # 3. 스킵 분기
            if not ot_data_list:
                skip_msg = f"{data.target_year}년 {data.target_month}월에 상신할 유효 초과근무 내역이 없습니다."
                return json.dumps({"status": "success", "message": skip_msg}, ensure_ascii=False)

            logger.info(f"총 {len(ot_data_list)}건의 초과근무 폼 세팅을 진행합니다.")
        # ==========================================
        # 2. [결재 상신 페이즈] ApprovalCrawler 단독 실행
        # ==========================================
        async with ApprovalCrawler(cookies, user_id) as crawler:
            # [분기 1] 월단위 처리 (위에서 가져온 ot_data_list 주입)
            if data.request_type == "monthly":
                result = await crawler.process_monthly_overtime_request(
                    target_user_name=raw_user_name,  # 문병찬
                    dept_name=dept_name,
                    ot_data_list=ot_data_list,
                    doc_type=data.doc_type,
                    memo=data.memo
                )

            # [분기 2] 단일 날짜 처리
            else:
                result = await crawler.process_oneday_overtime_request(
                    target_user_name=user_name,  # 문병찬대리
                    dept_name=dept_name,
                    doc_type=data.doc_type,
                    ot_date=data.ot_date,
                    memo=data.memo
                )

            # ==========================================
            # 3. [공통] 상신/저장 액션 처리 (ApprovalCrawler 락 유지 상태)
            # ==========================================
            if result.get("status") == "success":
                action_name = "결재상신" if data.action_type == "F" else "임시저장"

                # 대화상자 메시지 캡처
                dialog_messages = []

                async def handle_dialog(dialog):
                    try:
                        msg = dialog.message
                        dialog_messages.append(msg)
                        logger.info(f"브라우저 대화상자 감지 ({msg}) -> 승인")
                        await dialog.accept()
                    except:
                        pass

                crawler.page.on("dialog", lambda d: asyncio.create_task(handle_dialog(d)))

                logger.info(f"최종 {action_name} 호출: SendFlowData('{data.action_type}')")
                await crawler.page.evaluate(f"SendFlowData('{data.action_type}')")

                # 최종 목적지 URL 도달 확인 함수
                async def check_final_destination(target_pattern):
                    # 1. 현재 페이지에서 먼저 대기 시도
                    try:
                        await crawler.page.wait_for_url(target_pattern, timeout=5000)
                        return True
                    except Exception:
                        pass

                    # 2. 현재 페이지가 닫혔거나 이동이 없는 경우, 컨텍스트 내 모든 페이지 뒤지기
                    for _ in range(10): # 최대 5초 대기
                        # 에러성 대화상자 확인
                        error_dialogs = [m for m in dialog_messages if "저장하시겠습니까" not in m and "상신하시겠습니까" not in m]
                        if error_dialogs:
                            logger.warning(f"에러 대화상자 감지로 인한 대기 중단: {error_dialogs[-1]}")
                            return False

                        for p in crawler.context.pages:
                            try:
                                if not p.is_closed() and any(pat in p.url for pat in ["Doc_List", "DocBox_List"]):
                                    # 목적지 패턴이 포함된 페이지 발견 시 성공으로 간주
                                    if "Gubun=T" in p.url or ("Sign=F" in p.url and "isTemp=N" in p.url):
                                        logger.info(f"목적지 페이지 발견: {p.url}")
                                        crawler.page = p # 페이지 갱신
                                        return True
                            except:
                                continue
                        await asyncio.sleep(0.5)
                    return False

                if data.action_type == "F":
                    is_success = await check_final_destination("**/Flow/Doc_List*")
                else:
                    is_success = await check_final_destination("**/Flow/DocBox_List?Gubun=T*")

                if is_success:
                    action_name = "결재상신" if data.action_type == "F" else "임시저장"
                    logger.success(f"{action_name} 성공 확인. URL: {crawler.page.url}")
                    result["message"] = f"{user_name}님의 OT 신청 {action_name} 완료"
                else:
                    # 실패 시 대화상자 메시지가 있다면 해당 메시지를 결과에 포함
                    error_msg = dialog_messages[-1] if dialog_messages else "페이지 이동 실패"
                    logger.error(f"{action_name} 실패. 사유: {error_msg}")
                    result.update({
                        "status": "fail",
                        "message": f"{action_name} 중 오류가 발생했습니다: {error_msg}",
                        "details": error_msg
                    })

            return json.dumps(result, ensure_ascii=False)

    except Exception as e:
        logger.exception("OT 신청 도구 실행 중 오류")
        return json.dumps({"status": "error", "message": str(e)}, ensure_ascii=False)


@mcp.tool()
@requires_cookies
async def request_for_leave(
        request_data: dict,
        ctx: Context = None,
        cookies: list = None
) -> str:
    """
    그룹웨어 휴가 신청 도구.
    그룹웨어에서 휴가 신청서를 자동으로 작성하고 임시저장하거나 결재를 상신합니다.

    [사용 시기]
    - 사용자가 하나 또는 여러 개의 휴가를 신청해달라고 요청할 때 호출합니다.
    - 예: "내일 연차 신청해줘", "월요일 반반차(오전 10시)랑 수요일 보상휴가(반일) 올려줘", "23일, 24일 연차 쓸게"
    - 잔업, 특근, 야근, OT 신청에는 절대 사용하지 마세요.

    [주의 사항]
    - action_type은 반드시 결재상신이면 'F', 임시저장이면 'T'로 매핑하세요.
    - leave_data_list 안에 각각의 휴가 내역을 담아야 합니다.
    - 반차는 half_day_type("오전"|"오후"), 반반차는 start_time("HH:MM")을 반드시 파악해서 넣으세요.
    [매핑 가이드]
    - "오후반차" 요청 시 -> leave_type: "반차", half_day_type: "오후"로 분리하여 입력.
    - "오전보상휴가" 요청 시 -> leave_type: "보상휴가(반일)", half_day_type: "오전"으로 분리.
    """
    try:
        # 리스트가 포함된 전체 모델 파싱
        data = LeaveRequestModel(**request_data)
        meta = getattr(ctx.request_context, 'meta', {}) or {}

        user_id = getattr(meta, "userId", None) if meta else None
        user_name = getattr(meta, "userName", None) if meta else None
        dept_name = getattr(meta, "userDept", None) if meta else None

        if not user_id or not user_name:
            raise ValueError("userId 또는 userName이 meta 정보에 없습니다.")

    except Exception as e:
        logger.error(f"휴가 데이터 파싱 에러: {str(e)}")
        return json.dumps({
            "status": "error",
            "message": "필수 파라미터 누락 또는 데이터 형식이 틀렸습니다.",
            "details": str(e)
        }, ensure_ascii=False)

    logger.info(f"휴가 신청 시작 - 대상: {user_name}, 총 {len(data.leave_data_list)}건, 리스트: {data.leave_data_list}")

    if not data.leave_data_list:
        if data.skipped_dates:
            skipped_str = ", ".join(data.skipped_dates)
            logger.warning(f"[{user_name}] 주말/공휴일 제외로 신청 내역 없음: {skipped_str}")
            return json.dumps({
                "status": "fail",
                "message": f"요청하신 날짜({skipped_str})는 주말 또는 공휴일이어서 휴가를 신청할 수 없습니다. 평일로 다시 요청해주세요.",
            }, ensure_ascii=False)

        logger.warning(f"[{user_name}] 휴가 신청 내역이 없음 (leave_data_list is empty)")
        return json.dumps({
            "status": "fail",
            "message": "신청할 휴가 내역을 찾을 수 없습니다. '몇 월 며칠에 어떤 휴가(연차, 오후반차 등)'를 쓰실지 정확하게 말씀해주세요.",
        }, ensure_ascii=False)

    max_retries = 2
    for attempt in range(max_retries):
        try:
            async with ApprovalCrawler(cookies, user_id) as crawler:

                result = await crawler.process_leave_request(
                    dept_name=dept_name,
                    leave_data_list=data.leave_data_list  # 리스트 형태의 데이터
                )

                if result.get("status") == "success":
                    action_name = "결재상신" if data.action_type == "F" else "임시저장"

                    # 대화상자 메시지를 저장할 리스트
                    dialog_messages = []

                    async def handle_dialog(dialog):
                        try:
                            msg = dialog.message
                            dialog_messages.append(msg)
                            logger.info(f"브라우저 대화상자 감지 ({msg}) -> 승인")
                            await dialog.accept()
                        except:
                            pass

                    crawler.page.on("dialog", lambda d: asyncio.create_task(handle_dialog(d)))

                    logger.info(f"최종 {action_name} 호출: SendFlowData('{data.action_type}')")
                    await crawler.page.evaluate(f"SendFlowData('{data.action_type}')")

                    # 최종 목적지 URL 도달 확인 함수 (휴가)
                    async def check_final_destination_leave(target_pattern):
                        try:
                            await crawler.page.wait_for_url(target_pattern, timeout=5000)
                            return True
                        except:
                            pass

                        for _ in range(15):  # 최대 7.5초 대기
                            # 만약 에러성 대화상자가 떴다면 즉시 중단하고 실패 처리하기 위해 메시지 확인
                            # "저장하시겠습니까" 이외의 메시지가 있다면 에러일 확률이 높음
                            error_dialogs = [m for m in dialog_messages if "저장하시겠습니까" not in m and "상신하시겠습니까" not in m]
                            if error_dialogs:
                                logger.warning(f"에러 대화상자 감지로 인한 대기 중단: {error_dialogs[-1]}")
                                return False

                            for p in crawler.context.pages:
                                try:
                                    if not p.is_closed() and any(pat in p.url for pat in ["Doc_List", "DocBox_List"]):
                                        if "Gubun=T" in p.url or ("Sign=F" in p.url and "isTemp=N" in p.url):
                                            logger.info(f"목적지 페이지 발견(휴가): {p.url}")
                                            crawler.page = p
                                            return True
                                except:
                                    continue
                            await asyncio.sleep(0.5)
                        return False

                    if data.action_type == "F":
                        is_success = await check_final_destination_leave("**/Flow/Doc_List*")
                    else:
                        is_success = await check_final_destination_leave("**/Flow/DocBox_List?Gubun=T*")

                    if is_success:
                        action_name = "결재상신" if data.action_type == "F" else "임시저장"
                        logger.success(f"{action_name} 성공 확인. URL: {crawler.page.url}")
                        result["message"] = f"{user_name}님의 휴가 신청({len(data.leave_data_list)}건) {action_name} 완료"
                    else:
                        # 실패 시 대화상자 메시지가 있다면 해당 메시지를 결과에 포함
                        error_msg = dialog_messages[-1] if dialog_messages else "페이지 이동 실패"
                        logger.error(f"{action_name} 실패. 사유: {error_msg}")
                        result.update({
                            "status": "fail", 
                            "message": f"{action_name} 중 오류가 발생했습니다: {error_msg}",
                            "details": error_msg
                        })
                return json.dumps(result, ensure_ascii=False)

        except Exception as e:
            if ("closed" in str(e).lower() or "disconnected" in str(e).lower()) and attempt < max_retries - 1:
                logger.warning(f"⚠️ 브라우저 연결 끊김 감지 (재시도 {attempt + 1}/{max_retries}): {e}")
                await asyncio.sleep(2)
                continue
            
            logger.exception("휴가 신청 도구 실행 중 오류")
            return json.dumps({"status": "error", "message": str(e)}, ensure_ascii=False)

@mcp.tool()
@requires_cookies
async def get_recent_emails(
        ctx: Context = None,
        cookies: list = None
) -> str:
    """
    최근 받은 이메일 목록을 조회하고 요약합니다.
    안읽은 메일 여부, 보낸 사람, 제목, 수신 시간을 포함합니다.
    """
    logger.info("최신 이메일 목록 조회 요청")

    try:
        meta = getattr(ctx.request_context, 'meta', {}) or {}
        user_id = getattr(meta, "userId", None)

        async with EmailCrawler(cookies, user_id) as crawler:
            emails = await crawler.fetch_recent_emails()

            if not emails:
                return json.dumps({"status": "success", "message": "최근 받은 메일이 없습니다."}, ensure_ascii=False)

            # 요약 정보 생성
            total_count = len(emails)
            unread_count = sum(1 for e in emails if e["is_unread"])

            result = {
                "status": "success",
                "summary": f"총 {total_count}개의 메일이 있으며, 그 중 {unread_count}개가 읽지 않은 메일입니다.",
                "emails": emails
            }

            logger.success(f"이메일 {total_count}건 조회 완료")
            return json.dumps(result, ensure_ascii=False)

    except Exception as e:
        logger.exception("이메일 조회 중 오류 발생")
        return json.dumps({"status": "error", "message": str(e)}, ensure_ascii=False)