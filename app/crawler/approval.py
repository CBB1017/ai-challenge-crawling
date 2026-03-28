import os
from datetime import datetime
from loguru import logger
from app.crawler.attendance import AttendanceCrawler
from app.crawler.base import BaseCrawler


class ApprovalCrawler(BaseCrawler):
    def __init__(self, login_url: str, username: str, password: str, cookies: list = None):
        super().__init__(login_url, username, password, cookies)

    async def process_overtime_request(
            self,
            target_user_name: str,
            dept_name: str,
            doc_type: str = "OT",
            ot_date: str = None,
            memo: str = "."
    ):
        groupware_domain = os.environ["GROUPWARE_DOMAIN"]
        """1. 페이지 진입 전, 근태 데이터 사전 검증 (Fail-Fast)"""
        if not self.cookies:
            success, new_cookies = await self.login()
            if not success: return {"status": "fail", "message": "로그인 실패"}
            self.cookies = new_cookies

        # 근태 크롤러로 해당 유저 데이터 조회
        async with AttendanceCrawler(self.login_url, self.username, self.password, self.cookies) as att_crawler:
            att_result = await att_crawler.fetch_attendance(groupware_domain)

        if att_result.get("status") != "success":
            return {"status": "fail", "message": "근태 조회 실패"}

        # 타겟 유저 찾기
        target_member = next(
            (m for members in att_result["data"].values() for m in members if m.get("memberName") == target_user_name),
            None
        )

        if not target_member:
            logger.warning(f"[{target_user_name}] 근태 기록을 찾을 수 없습니다.")
            return {"status": "fail", "message": "근태 기록 없음"}

        # 2. OT 수행 여부 유효성 검사
        plan_in_out = target_member.get("planInOut", "09:00~18:00")
        actual_out = target_member.get("actualOut", "")  # ex: "21:30" 또는 ""

        if not actual_out:
            logger.info(f"[{target_user_name}] 퇴근 기록이 없습니다. 상신 스킵.")
            return {"status": "fail", "message": "퇴근 전(OT 미수행)"}

        try:
            # 시작 시간 계산 (25시 이상 표기 지원이므로 % 24 제거)
            plan_out_str = plan_in_out.split("~")[1].strip()
            plan_out_h, plan_out_m = map(int, plan_out_str.split(":"))
            ot_start_h = plan_out_h + 1
            ot_start_m = plan_out_m

            # 실제 퇴근 시간 30분 단위 내림 처리
            actual_out_h, actual_out_m = map(int, actual_out.split(":"))
            rounded_out_m = 30 if actual_out_m >= 30 else 0
            rounded_out_h = actual_out_h

            # 분 단위 환산 비교 (25시도 그대로 선형 계산됨)
            start_total_mins = ot_start_h * 60 + ot_start_m
            end_total_mins = rounded_out_h * 60 + rounded_out_m

            # OT 인정 시간이 0분 이하인 경우 스킵 (예: 19:00 시작인데 19:25 퇴근 -> 내림 시 19:00)
            if end_total_mins <= start_total_mins:
                logger.info(
                    f"[{target_user_name}] 실제 퇴근({actual_out}) -> 인정 OT({rounded_out_h:02d}:{rounded_out_m:02d}) 미달. 상신 스킵.")
                return {"status": "fail", "message": "OT 조건(30분) 미달"}

            # 최종 폼에 입력할 OT 종료 시간
            final_ot_end_hm = f"{rounded_out_h:02d}:{rounded_out_m:02d}"


        except Exception as e:
            logger.error(f"시간 파싱 오류: {e}")
            return {"status": "fail", "message": "시간 계산 오류"}

        logger.info(f"[{target_user_name}] OT 검증 통과. {ot_start_h:02d}:{ot_start_m:02d} ~ {final_ot_end_hm} 상신 진행.")

        """3. 검증 통과 시 페이지 이동 및 폼 작성"""
        approval_url = f"{groupware_domain}/Flow/Doc_Write?ActionGubun=APPEND&BoxNo=2&DocKind=2&RtnURL=Form_List?gubun=Doc&FormNo=147757&FormType=PH&FormName=3%2E%EC%9E%94%EC%97%85%2F%ED%8A%B9%EA%B7%BC%28OT%29%EC%8B%A0%EC%B2%AD%EC%84%9C"
        await self.page.goto(approval_url)

        await self.set_approval_line(dept_name, doc_type)

        # 이미 계산된 시작/종료 시간을 전달하여 중복 조회 방지
        await self.fill_overtime_form(
            target_user_name=target_user_name,
            ot_start_hm=f"{ot_start_h:02d}:{ot_start_m:02d}",
            ot_end_hm=actual_out,  # 실제 퇴근 시간을 OT 종료시간으로 자동 세팅
            ot_date=ot_date,
            memo=memo
        )
        # 테스트 시 임시저장으로 동작 확인
        await self.submit_request_via_js("T")

        return {"status": "success", "message": "OT 폼 세팅 완료", "cookies": self.cookies}

    async def set_approval_line(self, dept_name: str, doc_type: str):
        """결재라인 팝업을 열고 동적으로 결재선을 선택 후 적용합니다."""
        # 영역 열기 체크
        if await self.page.locator("#trline_on").is_hidden():
            await self.page.locator("#trline_off img").click()

        # 팝업 대기 및 진입
        async with self.page.expect_popup() as popup_info:
            await self.page.locator("#trline_on button:has-text('결재라인불러오기')").click()

        popup = await popup_info.value
        await popup.wait_for_load_state()

        # 결재선 매핑 및 클릭
        line_mapping = {"OT": "근태 관련", "휴가": "근태 관련", "지출결의": "재무 관련"}
        target_text = f"{dept_name}_{line_mapping.get(doc_type, '기타')}"

        await popup.locator("tr", has_text=target_text).click()
        await popup.locator("button:has-text('결재라인 사용')").click()

        # 팝업이 닫힐 때까지 대기 (안정성 확보)
        await popup.wait_for_event("close")

    async def fill_overtime_form(self, target_user_name: str, ot_start_hm: str, ot_end_hm: str, ot_date: str = None,
                                 memo: str = "."):
        """이미 계산된 시간을 받아 폼만 채우도록 역할 분리"""
        if ot_date is None:
            ot_date = datetime.now().strftime("%Y-%m-%d")

        str_start_h, str_start_m = ot_start_hm.split(":")
        str_end_h, str_end_m = ot_end_hm.split(":")

        frame = self.page.frame_locator("#AspFile")

        await frame.locator('input[name="otDate"]').evaluate(
            f'(el) => {{ el.value = "{ot_date}"; el.dispatchEvent(new Event("change")); }}'
        )
        await frame.locator('select[name="startH"]').select_option(str_start_h)
        await frame.locator('select[name="startM"]').select_option(str_start_m)

        # 간혹 실제 퇴근시간이 분 단위로 안 떨어질 수 있으므로, select 박스에 있는 근사치(00, 15, 30, 45)로
        # 내림/올림 처리하는 로직이 필요할 수도 있습니다. (필요시 추가)
        await frame.locator('select[name="endH"]').select_option(str_end_h)
        await frame.locator('select[name="endM"]').select_option(str_end_m)

        await frame.locator('select[name="otWorkGubun"]').select_option('10')
        await frame.locator('select[name="LunchYN"]').select_option('O')
        await frame.locator('select[name="DinnerYN"]').select_option('O')
        await frame.locator('textarea[name="memo"]').fill(memo)

    async def submit_request_via_js(self, action_type: str = "T"):
        """JS 함수를 직접 실행하여 상신/임시저장 처리"""
        """
            action_type: 
            "F" = 결재상신
            "T" = 임시저장 (테스트)
        """
        # "F"(상신) 또는 "T"(임시저장)
        await self.page.evaluate(f"SendFlowData('{action_type}')")

        logger.info(f"동작({action_type})이 서버로 전송되었습니다.")
        await self.page.wait_for_load_state("networkidle")