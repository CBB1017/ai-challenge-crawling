import asyncio
import os
from datetime import datetime
from loguru import logger
from app.crawler.attendance import AttendanceCrawler
from app.crawler.base import BaseCrawler

def normalize_ot_minute(minute_str: str) -> str:
    """분(minute)을 00 또는 30으로 정규화"""
    try:
        m = int(minute_str)
        if m >= 30:
            return "30"
        else:
            return "00"
    except (ValueError, TypeError):
        return "00"



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
            att_result = await att_crawler.fetch_attendance(
                groupware_domain,
                ot_date=ot_date,
                dept_name=dept_name
            )

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
        # 결재선 설정 시도
        line_success = await self.set_approval_line(dept_name, doc_type)
        if not line_success:
            return {"status": "fail", "message": "결재라인 설정 실패로 중단되었습니다."}

        # 이미 계산된 시작/종료 시간을 전달하여 중복 조회 방지
        await self.fill_overtime_form(
            target_user_name=target_user_name,
            ot_start_hm=f"{ot_start_h:02d}:{ot_start_m:02d}",
            ot_end_hm=actual_out,  # 실제 퇴근 시간을 OT 종료시간으로 자동 세팅
            ot_date=ot_date,
            memo=memo
        )

        return {"status": "success", "message": "OT 폼 세팅 완료", "cookies": self.cookies}

    async def set_approval_line(self, dept_name: str, doc_type: str) -> bool:
        import asyncio

        dept_map = {
            "DX": "DX사업부",
            "AI": "AI사업부",
            "이미징": "이미징솔루션그룹",
            "경영": "경영관리부"
        }

        target_dept = next(
            (v for k, v in dept_map.items() if k in dept_name.upper()),
            dept_name
        )

        suffix = "근태" if doc_type in ["OT", "휴가"] else "재무"
        search_keyword = f"{target_dept}_{suffix}"

        try:
            # 1. 결재라인 영역 펼치기
            await self.page.evaluate("""
            () => {
                const area = document.querySelector("#trline_on");
                if (area && getComputedStyle(area).display === 'none') {
                    const img = document.querySelector("img[onclick*='trline']");
                    if (img) img.click();
                }
            }
            """)

            await self.page.wait_for_function("""
            () => {
                const el = document.querySelector("#trline_on");
                return el && getComputedStyle(el).display !== 'none';
            }
            """, timeout=3000)

            # 2. popup 열기
            async with self.page.expect_popup(timeout=7000) as popup_info:
                await self.page.locator("button[onclick*='LineR2']").click()

            popup = await popup_info.value
            await popup.wait_for_load_state("domcontentloaded")
            await popup.wait_for_timeout(1000)

            # 3. popup frame 확인
            for f in popup.frames:
                logger.info(f"frame name={f.name}, url={f.url}")

            # 4. RightTop frame 확보 (url 우선)
            target_frame = next(
                (f for f in popup.frames if "Line_Top" in f.url),
                None
            )

            if not target_frame:
                target_frame = popup.frame(name="RightTop")

            if not target_frame:
                logger.error("상단 결재선 프레임을 찾지 못했습니다.")
                return False

            await target_frame.wait_for_selector("table", timeout=5000)

            # 5. 결재선 row 찾기 (text-is 대신 has-text)
            row = target_frame.locator(f"tr:has(td:has-text('{search_keyword}'))").first

            await row.wait_for(state="visible", timeout=5000)

            # 6. 우선 click 시도
            try:
                await row.click()
                logger.info(f"결재선 row 클릭 성공: {search_keyword}")
            except:
                onclick_script = await row.get_attribute("onclick")
                if onclick_script:
                    await target_frame.evaluate(
                        onclick_script.replace("javascript:", "")
                    )
                    logger.info(f"onclick fallback 실행: {search_keyword}")

            await popup.wait_for_load_state("networkidle")

            # 8. alert 자동 승인
            popup.on(
                "dialog",
                lambda d: asyncio.create_task(d.accept())
            )

            # 9. 결재라인 사용 버튼 클릭
            use_btn = popup.locator("button:has-text('결재라인 사용')")
            await use_btn.wait_for(state="visible", timeout=3000)

            # 팝업 닫힘 기다리지 말고 그냥 클릭만 던짐
            await use_btn.click()
            logger.info("클릭 완료, 1초 대기 후 강제 복구 시작")
            await asyncio.sleep(1.0)

            # 10. [핵심] 죽은 자식 버리고 살아있는 부모 찾기
            # self.page가 죽었을 확률이 높으니 context에서 직접 뒤집니다.
            found_page = None
            for p in self.context.pages:
                try:
                    # 닫히지 않았고, URL에 'Doc_Write'나 공통 키워드가 포함된 놈 탐색
                    if not p.is_closed() and ("Doc_Write" in p.url or "main" in p.url):
                        found_page = p
                        break
                except:
                    continue

            if not found_page:
                # 그래도 없으면 그냥 닫히지 않은 첫 번째 탭이라도 잡음
                alive_pages = [p for p in self.context.pages if not p.is_closed()]
                if alive_pages:
                    found_page = alive_pages[0]

            if found_page:
                self.page = found_page
                # 핵심: 페이지가 완전히 로드(networkidle)될 때까지 대기
                await self.page.wait_for_load_state("networkidle")
                await self.page.bring_to_front()
                logger.info("부모창 복구 및 로딩 완료")
                return True
            else:
                logger.error("살아있는 탭을 하나도 찾지 못했습니다.")
                return False

        except Exception as e:
            logger.error(f"결재라인 설정 실패: {e}")
            return False

    async def fill_overtime_form(
            self,
            target_user_name: str,
            ot_start_hm: str,
            ot_end_hm: str,
            ot_date: str = None,
            memo: str = "."
    ) -> None:
        if ot_date is None:
            ot_date = datetime.now().strftime("%Y-%m-%d")

        # 시간 분리 및 종료 분 보정
        str_start_h, str_start_m = ot_start_hm.split(":")
        str_end_h, str_end_m = ot_end_hm.split(":")
        normalized_m = normalize_ot_minute(str_end_m)

        try:
            await self.page.wait_for_load_state("domcontentloaded", timeout=5000)
        except:
            # 만약 페이지 객체가 죽었다면 다시 context에서 가져오기
            for p in self.context.pages:
                if not p.is_closed() and "Doc_Write" in p.url:
                    self.page = p
                    break

            # 1. iframe 대기 전에 메인 페이지가 살아있는지 확인
        await self.page.wait_for_selector("#AspFile", timeout=10000)
        frame = self.page.frame_locator("#AspFile")

        ot_date_input = frame.locator('input[name="otDate"]')

        # 요소가 나타날 때까지 대기 (FrameLocator에 wait_for_selector가 없으므로 이 방식 사용)
        await ot_date_input.wait_for(state="visible", timeout=5000)

        # 2. 데이터 입력 (evaluate 사용 시 이벤트 디스패치 포함)
        await ot_date_input.evaluate(
            f'(el, val) => {{ el.value = val; el.dispatchEvent(new Event("change")); }}',
            ot_date
        )

        # 3. Select 및 Input 필드 채우기
        # 루프나 연속적인 locator 호출 시 frame 객체를 재사용합니다.
        await frame.locator('select[name="startH"]').select_option(str_start_h)
        await frame.locator('select[name="startM"]').select_option(str_start_m)
        await frame.locator('select[name="endH"]').select_option(str_end_h)

        try:
            await frame.locator('select[name="endM"]').select_option(normalized_m)
        except Exception as e:
            logger.warning(f"종료 분({normalized_m}) 선택 실패 → '00' fallback: {e}")
            await frame.locator('select[name="endM"]').select_option("00")

        # 나머지 옵션 설정
        await frame.locator('select[name="otWorkGubun"]').select_option("10")
        await frame.locator('select[name="LunchYN"]').select_option("O")
        await frame.locator('select[name="DinnerYN"]').select_option("O")
        await frame.locator('textarea[name="memo"]').fill(memo)

        logger.success(f"OT 신청서 작성 완료: {ot_date} {ot_start_hm}~{ot_end_hm}")

    async def submit_request_via_js(self, action_type: str = "T"):
        """JS 함수를 직접 실행하여 상신/임시저장 처리"""
        """
            action_type: 
            "F" = 결재상신
            "T" = 임시저장 (테스트)
        """
        # "F"(상신) 또는 "T"(임시저장)
        self.page.on("dialog", lambda d: asyncio.create_task(d.accept()))

        await self.page.evaluate(f"SendFlowData('{action_type}')")

        logger.info(f"동작({action_type})이 서버로 전송되었습니다.")

        try:
            await self.page.wait_for_load_state("networkidle", timeout=5000)
        except:
            pass