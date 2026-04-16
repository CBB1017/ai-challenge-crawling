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
    def __init__(self, cookies: list = None, user_id: str = None):
        super().__init__(cookies, user_id)

    async def process_oneday_overtime_request(
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
            # 쿠키가 없으면 무조건 에러를 뱉고 뻗습니다.
            # 프론트엔드는 이 에러를 받아 사용자를 로그인 창으로 튕겨냅니다.
            logger.warning("유효한 세션(쿠키)이 없습니다. 프론트엔드 리다이렉트 필요.")
            return {"status": "fail", "message": "세션이 만료되었습니다. 다시 로그인해주세요.", "code": "SESSION_EXPIRED"}

        # 근태 크롤러로 해당 유저 데이터 조회
        async with AttendanceCrawler(self.cookies) as att_crawler:
            att_result = await att_crawler.fetch_attendance(ot_date,dept_name)

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
            ot_start_hm=f"{ot_start_h:02d}:{ot_start_m:02d}",
            ot_end_hm=actual_out,  # 실제 퇴근 시간을 OT 종료시간으로 자동 세팅
            ot_date=ot_date,
            memo=memo
        )

        return {"status": "success", "message": "OT 폼 세팅 완료", "cookies": self.cookies}

    async def set_approval_line(self, dept_name: str, doc_type: str, form_no: str) -> bool:
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

            await target_frame.wait_for_selector("table", timeout=10000)

            # 5. 결재선 row 찾기 (text-is 대신 has-text)
            row = target_frame.locator(f"tr:has(td:has-text('{search_keyword}'))").first

            await row.wait_for(state="visible", timeout=10000)

            # 6. click 시도
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
                return False

            await popup.wait_for_timeout(1000)
            # -----------------------------------------------------------
            # 7. admin_main.CallLine() 직접 호출 및 에러 추적
            # -----------------------------5t6------------------------------
            logger.info("자바스크립트 CallLine() 호출하여 데이터 스틸 시도...")

            # 팝업의 최상위 window에서 원본 JS와 똑같이 실행하며,
            # 실패할 경우 정확히 "왜" 실패했는지 에러 메시지를 반환합니다.
            debug_script = """
                () => {
                    try {
                        // 1. 객체 존재 여부 확인
                        if (!window.admin_main) {
                            return { status: "fail", msg: "window.admin_main 프레임을 찾을 수 없습니다." };
                        }

                        // 2. 함수 존재 여부 확인
                        if (typeof window.admin_main.CallLine !== 'function') {
                            return { status: "fail", msg: "CallLine이 함수가 아닙니다. 현재 타입: " + typeof window.admin_main.CallLine };
                        }

                        // 3. 원본과 동일하게 함수 실행!
                        const result = window.admin_main.CallLine();

                        // 4. 결과값 검증
                        if (!result || result.trim() === "") {
                            return { status: "empty", msg: "함수가 실행되었으나 빈 값을 반환했습니다. (결재선 row 클릭이 인식되지 않았을 확률 높음)" };
                        }

                        return { status: "success", data: result };

                    } catch (e) {
                        // 내부에서 에러가 터졌을 경우
                        return { status: "error", msg: "JS 내부 에러 발생: " + e.message };
                    }
                }
                """

            result_obj = await popup.evaluate(debug_script)

            if result_obj["status"] == "success":
                v_list = result_obj["data"]
                logger.success(f"데이터 스틸 완벽 성공: {v_list[:50]}...")
            else:
                # 여기서 찍히는 로그를 보면 원인을 100% 알 수 있습니다.
                logger.error(f"CallLine() 실패 상세 원인: {result_obj['msg']}")
                return False

            # 10. 팝업 안에서 부모를 건드리지 않고 조용히 닫기만 함!
            logger.info("데이터 확보 완료. 팝업을 안전하게 종료합니다.")
            await popup.close()

            # 11. 부모 창으로 안전하게 복귀
            await self.page.bring_to_front()
            logger.success("부모 창 복귀 성공. 셀프 서브밋을 준비합니다.")

            # 서브밋을 실행함과 동시에, 서버에서 Doc_Line_View 응답이 올 때까지 기다립니다.
            # 이렇게 하면 프레임이 깨지든 말든 DOM 에러(Target closed)가 발생하지 않습니다.
            async with self.page.expect_response(lambda r: "Doc_Line_View" in r.url, timeout=20000):
                await self.page.evaluate(f"""(val) => {{
                            const frm = document.d_form;
                            if (frm) {{
                                frm.SignList.value = val;
                                frm.target = 'DocWrite_Line';
                                frm.action = 'Doc_Line_View?FormNo={form_no}&NowBuseo=29&SignList=' + encodeURIComponent(val);
                                frm.submit();
                            }}
                        }}""", v_list)

            # 13. 프레임 렌더링 확인
            logger.info("셀프 서브밋 완료, 프레임 갱신 대기 중...")

            # iframe 내부를 뒤질 필요 없이, 부모 창의 SignList에 값이 잘 들어갔는지만 봅니다.
            final_signlist = await self.page.locator("input[name='SignList']").get_attribute("value")

            if final_signlist and len(final_signlist) > 10:
                logger.success("결재라인 폼 반영 완벽하게 완료되었습니다!")
                return True
            else:
                logger.error("서브밋은 되었으나 SignList 값이 비어있습니다.")
                return False
        except Exception as e:
            logger.error(f"결재라인 설정 실패: {e}")
            return False

    async def fill_overtime_form(
            self,
            ot_start_hm: str,
            ot_end_hm: str,
            ot_date: str = None,
            memo: str = "."
    ) -> None:
        logger.info("--------------fill_overtime_form----------------")

        if ot_date is None:
            ot_date = datetime.now().strftime("%Y-%m-%d")

        # 시간 분리 및 종료 분 보정
        str_start_h, str_start_m = ot_start_hm.split(":")
        str_end_h, str_end_m = ot_end_hm.split(":")
        normalized_m = normalize_ot_minute(str_end_m)

        try:
            await self.page.wait_for_load_state("domcontentloaded", timeout=10000)
        except:
            logger.error("--------------페이지 객체가 죽었다----------------")
            # 만약 페이지 객체가 죽었다면 다시 context에서 가져오기
            for p in self.context.pages:
                if not p.is_closed() and "Doc_Write" in p.url:
                    self.page = p
                    break

            # 1. iframe 대기 전에 메인 페이지가 살아있는지 확인
        await self.page.wait_for_selector("#AspFile", timeout=20000)
        frame = self.page.frame_locator("#AspFile")

        ot_date_input = frame.locator('input[name="otDate"]')

        # 요소가 나타날 때까지 대기 (FrameLocator에 wait_for_selector가 없으므로 이 방식 사용)
        await ot_date_input.wait_for(state="visible", timeout=10000)

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
            await self.page.wait_for_load_state("networkidle", timeout=10000)
        except:
            pass

    async def process_monthly_overtime_request(
            self,
            target_user_name: str,
            dept_name: str,
            ot_data_list: list,  # [{'date': '2023-10-01', 'start': '19:00', 'end': '21:30', 'reason': '사유'}, ...]
            doc_type: str = "OT",
            memo: str = "."
    ):
        """월 단위 OT 상신 메인 파이프라인"""
        groupware_domain = os.environ["GROUPWARE_DOMAIN"]

        if not self.cookies:
            logger.warning("유효한 세션(쿠키)이 없습니다.")
            return {"status": "fail", "message": "세션 만료"}

        # 1. 폼 페이지 진입
        approval_url = f"{groupware_domain}/Flow/Doc_Write?ActionGubun=APPEND&BoxNo=2&DocKind=2&RtnURL=Form_List?gubun=Doc&FormNo=147757&FormType=PH&FormName=3%2E%EC%9E%94%EC%97%85%2F%ED%8A%B9%EA%B7%BC%28OT%29%EC%8B%A0%EC%B2%AD%EC%84%9C"
        await self.page.goto(approval_url)

        # 2. 결재선 설정
        line_success = await self.set_approval_line(dept_name, doc_type)
        if not line_success:
            return {"status": "fail", "message": "결재라인 설정 실패"}

        # 3. 월 단위 반복 폼 작성
        await self.fill_monthly_overtime_form(target_user_name, ot_data_list, memo)

        return {"status": "success", "message": "월 단위 OT 폼 세팅 완료", "cookies": self.cookies}
    async def process_leave_request(
            self,
            dept_name: str,
            leave_data_list: list,  # [{'date': '2023-10-01', 'start': '19:00', 'end': '21:30', 'reason': '사유'}, ...]
    ):
        """휴가 상신 메인 파이프라인"""
        groupware_domain = os.environ["GROUPWARE_DOMAIN"]
        form_no = '147758'

        if not self.cookies:
            logger.warning("유효한 세션(쿠키)이 없습니다.")
            return {"status": "fail", "message": "세션 만료"}

        # 1. 폼 페이지 진입
        approval_url = f"{groupware_domain}/Flow/Doc_Write?ActionGubun=APPEND&BoxNo=2&DocKind=2&RtnURL=Form_List?gubun=Doc&FormNo={form_no}&FormType=PH&FormName=4%2E%ED%9C%B4%EA%B0%80%EC%8B%A0%EC%B2%AD%EC%84%9C"
        await self.page.goto(approval_url)

        # 2. 결재선 설정
        line_success = await self.set_approval_line(dept_name, "휴가", form_no)
        if not line_success:
            return {"status": "fail", "message": "결재라인 설정 실패"}

        # 3. 월 단위 반복 폼 작성
        form_result = await self.fill_leave_form(leave_data_list)

        # 실패 시 바로 에러 반환
        if form_result.get("status") == "fail":
            return form_result

        return {"status": "success", "message": "휴가 폼 세팅 완료", "cookies": self.cookies}

    async def fill_monthly_overtime_form(self, my_name: str, ot_data_list: list, memo: str = "."):
        """라인 추가를 반복하며 월단위 데이터를 입력하고 자동완성을 처리하는 로직"""
        logger.info("--------------fill_monthly_overtime_form 시작----------------")

        await self.page.wait_for_selector("#AspFile", timeout=20000)
        frame = self.page.frame_locator("#AspFile")

        for index, data in enumerate(ot_data_list):
            logger.info(f"[{index + 1}/{len(ot_data_list)}] {data.get('date')} 데이터 세팅 중...")

            # [1] 라인 추가 (첫 번째 데이터는 기본 줄이 있다고 가정, 2번째부터 라인추가 클릭)
            if index > 0:
                # '라인추가' 버튼 클릭
                add_btn = frame.locator('input[value="라인추가"]').last
                await add_btn.click()

                # 애니메이션/렌더링 딜레이 방지를 위해 잠시 대기
                await self.page.wait_for_timeout(500)

            # [2] 현재 작업할 행(Row) 특정
            # 폼 구조에 따라 특정 클래스가 없다면 가장 마지막에 추가된 입력 영역 테이블 행을 찾습니다.
            # (예: 라인 추가 버튼이 있는 tr의 바로 위 tr 등, 이 부분은 실제 html에 맞게 튜닝될 수 있습니다)
            # 여기서는 편의상 input[name="emp_name"]을 포함하는 tr들을 리스트업해서 index로 접근합니다.
            current_row = frame.locator('tr:has(input[name="emp_name"])').nth(index)

            # [3] 이름 입력 및 jQuery UI Autocomplete 처리
            name_input = current_row.locator('input[name="emp_name"]')
            # click 후 fill을 해야 자동완성 이벤트가 정상적으로 트리거되는 경우가 많음
            await name_input.click()
            await name_input.fill(my_name)

            # 자동완성 ul 태그 출현 대기 (form 밖 <body> 끝에 주로 붙음)
            # frame 내부에 렌더링되므로 frame.locator 사용
            autocomplete_ul = frame.locator('ul.ui-autocomplete')
            await autocomplete_ul.wait_for(state="visible", timeout=10000)

            # 드롭박스 내에서 내 이름이 포함된 div(wrapper) 찾아서 클릭
            target_item = autocomplete_ul.locator(f'div.ui-menu-item-wrapper:has-text("{my_name}")').first
            await target_item.click()

            # 선택 후 드롭다운 닫히는지 확인 대기 (안정성 확보)
            await autocomplete_ul.wait_for(state="hidden", timeout=3000)

            # [4] 시간 계산 및 콤보박스 세팅
            str_start_h, str_start_m = data['start'].split(":")
            str_end_h, str_end_m = data['end'].split(":")
            normalized_end_m = normalize_ot_minute(str_end_m)

            # current_row(현재 줄) 안에서만 select를 찾으므로, 다른 줄의 데이터가 변경될 위험이 없음
            await current_row.locator('select[name="startH"]').select_option(str_start_h)
            await current_row.locator('select[name="startM"]').select_option(str_start_m)
            await current_row.locator('select[name="endH"]').select_option(str_end_h)

            try:
                await current_row.locator('select[name="endM"]').select_option(normalized_end_m)
            except Exception as e:
                logger.warning(f"종료 분({normalized_end_m}) 선택 실패 → '00' fallback: {e}")
                await current_row.locator('select[name="endM"]').select_option("00")

            # 날짜 및 사유 등 추가 입력 (데이터 키값은 실제 attendance 파싱 결과에 맞게 맞춰주세요)
            if 'date' in data:
                await current_row.locator('input[name="otDate"]').fill(data['date'])
            if 'reason' in data:
                await current_row.locator('input[name="reason"]').fill(data['reason'])  # 사유 input name 확인 필요

            # 필요에 따라 식대/야식 등 옵션 세팅
            # await current_row.locator('select[name="otWorkGubun"]').select_option("10")

        # [5] 전체 공통 메모 입력 (루프 종료 후 마지막에 한 번)
        await frame.locator('textarea[name="memo"]').fill(memo)
        logger.success(f"월 단위 OT {len(ot_data_list)}건 입력 완벽하게 완료되었습니다.")

    async def fill_leave_form(self, leave_data_list: list):
        """
        휴가 데이터를 순회하며 라인을 추가하고, 동적 콤보박스 및 사유를 입력합니다.
        leave_data_list 안의 데이터는 Pydantic 객체(LeaveItemModel) 기준입니다.
        """
        logger.info("--------------fill_leave_form 시작----------------")

        await self.page.wait_for_selector("#AspFile", timeout=20000)
        frame = self.page.frame_locator("#AspFile")

        for index, item in enumerate(leave_data_list):
            logger.info(f"[{index + 1}/{len(leave_data_list)}] {item.start_date} ({item.leave_type}) 세팅 중...")

            # [1] 라인 추가 (2번째 휴가부터 '추가사용' 버튼 클릭)
            if index > 0:
                add_btn = frame.locator('button:has-text("추가사용"), input[value="추가사용"]').last
                await add_btn.click()
                await self.page.wait_for_timeout(500)

            # [2] 현재 작업할 행(Row) 특정
            # 화면에 "실제로 보이는" select 박스와 textarea만 순서대로 가져옵니다.
            current_select = frame.locator('select[name="holidayCode"]').nth(index)
            current_row = current_select.locator('xpath=./ancestor::tr')
            memo_area = frame.locator('textarea[name="memo"]').nth(index)
            logger.info(f"[{index}] 행(Row) 확보 완료")

            # 🚨 [3] 휴가 종류 세팅 (자바스크립트 다이렉트 추출 방식으로 속도/안정성 극대화)
            safe_leave_type = item.leave_type.replace(" ", "")
            base_name = "보상휴가" if "보상휴가" in safe_leave_type else safe_leave_type

            # JS를 DOM에 직접 쏴서 가장 정확한 value 값을 0.01초 만에 뽑아옵니다.
            option_val = await current_select.evaluate(f'''(select) => {{
                    const options = Array.from(select.options);
                    const target = options.find(opt => {{
                        if (opt.getAttribute("holiday-name") !== "{base_name}") return false;
                        if ("{safe_leave_type}".includes("종일") && !opt.text.includes("(종일)")) return false;
                        if ("{safe_leave_type}".includes("반일") && !opt.text.includes("(반일)")) return false;
                        return true;
                    }});
                    return target ? target.value : null;
                }}''')

            if option_val:
                await current_select.select_option(value=option_val)
            else:
                logger.error(f"'{safe_leave_type}' 옵션을 찾을 수 없습니다. (데이터 또는 권한 확인)")
                return {"status": "fail", "message": "옵션을 찾을 수 없습니다. (데이터 또는 권한 확인)"}

            # 동적 콤보박스 렌더링 대기
            await self.page.wait_for_timeout(500)

            # [4] 시작일/종료일 세팅 (readonly 우회 - 기존 코드 유지)
            start_input = current_row.locator('input[name="startHoliday"]')
            end_input = current_row.locator('input[name="endHoliday"]')
            await start_input.evaluate(
                f"(el) => {{ el.value = '{item.start_date}'; el.dispatchEvent(new Event('change', {{ bubbles: true }})); }}")
            await end_input.evaluate(
                f"(el) => {{ el.value = '{item.end_date}'; el.dispatchEvent(new Event('change', {{ bubbles: true }})); }}")

            # 🚨 [5] 휴가 종류별 동적 콤보박스 처리 (이 부분도 JS 추출 방식으로 안정화)
            if safe_leave_type in ["반차", "보상휴가(반일)"]:
                half_type = item.half_day_type if item.half_day_type else "오후"
                tz_select = current_row.locator('select[name="SelTimeZone"]')

                tz_val = await tz_select.evaluate(f'''(select) => {{
                        const target = Array.from(select.options).find(opt => opt.text.includes("{half_type}"));
                        return target ? target.value : null;
                    }}''')

                if tz_val:
                    await tz_select.select_option(value=tz_val)
                    logger.debug(f"반차 유형 세팅 완료: {half_type}")
                else:
                    logger.warning(f"오전/오후 콤보박스 값을 찾을 수 없습니다: {half_type}")
                    return {"status": "fail", "message": "오전/오후 콤보박스 값을 찾을 수 없습니다"}

            elif safe_leave_type == "반반차":
                s_h, s_m = item.start_time.split(":")
                e_h, e_m = item.end_time.split(":")

                time_selects = current_row.locator('td[td-name="TimeTerm"] select')
                try:
                    await time_selects.nth(0).select_option(label=s_h)
                    await time_selects.nth(1).select_option(label=s_m)
                    await time_selects.nth(2).select_option(label=e_h)
                    await time_selects.nth(3).select_option(label=e_m)
                    logger.debug(f"반반차 시간 세팅 완료: {s_h}:{s_m} ~ {e_h}:{e_m}")
                except Exception as e:
                    logger.error(f"반반차 시간 콤보박스 세팅 실패: {e}")
                    return {"status": "fail", "message": "반반차 시간 콤보박스 세팅 실패"}

            # [6] 사유(Memo) 입력
            await memo_area.click()
            await memo_area.fill(item.memo)

        logger.success(f"휴가 신청 폼 {len(leave_data_list)}건 입력 완벽하게 완료되었습니다.")
        return {"status": "success", "message": "폼 작성 완료"}