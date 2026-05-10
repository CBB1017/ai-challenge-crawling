import asyncio
import re
from loguru import logger
from bs4 import BeautifulSoup

from app.core.config import LOGIN_INFO
from app.crawler.base import BaseCrawler


class MeetingRoomCrawler(BaseCrawler):
    def __init__(self, cookies: list = None):
        super().__init__(cookies)

    async def fetch_reservations(self, room_name: str = None):

        try:
            # 2. 회의실 예약 페이지 이동 https://ekp.brycenkorea.co.kr:1212/RsvObjMgr/RsvObjUse_Trans.asp?ObjNo=3&Seq=-1&SelDate=2026-05-10&ObjName=%5B%EB%A6%AC%EB%B8%8C%EB%9D%BC%5D&SelYear=2026&SelWeek=20
            url = f"{LOGIN_INFO["domain"]}/RsvObjMgr/RsvObj_List?cmbCateNo=1#RsvObjMgrLeftBoxShare0"
            await self.page.goto(url)
            logger.info(f"[DEBUG] Navigated to URL: {self.page.url}")

            # 무조건 대기하기보다 테이블이 렌더링될 때까지 대기
            try:
                table_element = await self.page.wait_for_selector('table.table_title, table', timeout=3000)
            except Exception:
                # 렌더링 지연 시 기존 방식인 2초 강제 대기로 폴백
                await self.page.wait_for_timeout(2000)
                table_element = await self.page.query_selector('table')

            if not table_element:
                logger.error("[ERROR] 예약 테이블이 없습니다.")
                return {"status": "fail", "message": "예약 테이블을 찾을 수 없습니다.", "data": None}

            html = await table_element.inner_html()
            parsed_data = self.parse_meeting_room_table(f"<table>{html}</table>")

            logger.info(f"[DEBUG] parsed_data: {parsed_data}")

            if room_name:
                parsed_data = [room for room in parsed_data if room_name in room["회의실명"]]

            return {
                "status": "success",
                "message": "회의실 예약 조회 성공",
                "data": parsed_data,
                "cookies": self.cookies
            }

        except Exception as e:
            logger.error(f"[ERROR] 회의실 예약 데이터 크롤링 실패: {e}")
            return {"status": "fail", "message": str(e), "data": None}

    async def reserve_meeting_room(self, reservation_data: dict):
        """
        회의실 예약을 수행합니다.
        reservation_data: {
            "room_name": str,
            "start_date": str, (YYYY-MM-DD)
            "end_date": str, (YYYY-MM-DD)
            "start_time": str, (HH:mm)
            "end_time": str, (HH:mm)
            "title": str,
            "people_count": str,
            "description": str
        }
        """
        try:
            room_name = reservation_data.get("room_name")
            start_date = reservation_data.get("start_date")
            end_date = reservation_data.get("end_date") or start_date
            start_time = reservation_data.get("start_time")
            end_time = reservation_data.get("end_time")
            title = reservation_data.get("title")
            people_count = reservation_data.get("people_count", "1")
            description = reservation_data.get("description", ".")

            # 1. 예약 페이지 이동
            encoded_room_name = room_name if room_name.startswith("[") else f"[{room_name}]"
            url = f"{LOGIN_INFO['domain']}/RsvObjMgr/RsvObjUse_Trans.asp?ObjNo=3&Seq=-1&SelDate={start_date}&ObjName={encoded_room_name}"
            await self.page.goto(url)
            logger.info(f"[RESERVE] Moving to reservation page: {self.page.url}")

            # 2. 제목 입력
            await self.page.fill("#Subject", title)

            # 3. 날짜 범위 설정 (Hidden 필드 조작 및 JS 호출)
            await self.page.evaluate("""
                ([s, e]) => {
                    document.getElementById('StartDate').value = s;
                    document.getElementById('EndDate').value = e;
                    if (typeof fncSetDateDiff === 'function') {
                        fncSetDateDiff();
                    }
                }
            """, [start_date, end_date])
            logger.info(f"[RESERVE] Date range set: {start_date} ~ {end_date}")

            # 4. 시간 선택 (HH:mm 분리)
            try:
                s_h, s_m = start_time.split(":")
                e_h, e_m = end_time.split(":")
                
                await self.page.select_option("select[name='st_hour']", s_h.zfill(2))
                await self.page.select_option("select[name='st_minute']", s_m.zfill(2))
                await self.page.select_option("select[name='ed_hour']", e_h.zfill(2))
                await self.page.select_option("select[name='ed_minute']", e_m.zfill(2))
            except Exception as e:
                logger.warning(f"[RESERVE] 시간 선택 중 오류 (데이터 확인 필요): {e}")

            # 4. 연락처 (사용자 번호가 적힌 버튼 클릭)
            phone_btn = await self.page.query_selector("input[type='button'][value^='010-']")
            if phone_btn:
                await phone_btn.click()
                logger.info("[RESERVE] 연락처 자동 입력 버튼 클릭")

            # 5. 사용예상인원
            await self.page.fill("input[name='UseInwon']", str(people_count))

            # 6. Naver Smart Editor 입력
            try:
                # 에디터가 로드될 때까지 대기
                await self.page.wait_for_selector("iframe[src*='SmartEditor2Skin.html']", timeout=5000)
                await self.page.wait_for_function(
                    "() => typeof oEditors_WebEditor1 !== 'undefined' && oEditors_WebEditor1.getById['WebEditor1']",
                    timeout=5000
                )
                
                # HTML 형식으로 내용 주입 (개행 처리 포함)
                logger.info(f"[RESERVE] Smart Editor 주입 시도 내용: {description}")
                safe_desc = description.replace("'", "\\'").replace("\n", "<br>")
                await self.page.evaluate(
                    f"oEditors_WebEditor1.getById['WebEditor1'].exec('SET_CONTENTS', ['{safe_desc}'])")
                logger.info("[RESERVE] Smart Editor 내용 주입 완료")
            except Exception as e:
                logger.error(f"[RESERVE] Smart Editor 주입 실패: {e}")

            # 7. 등록 및 다이얼로그 처리
            dialog_messages = []
            async def handle_dialog(dialog):
                dialog_messages.append(dialog.message)
                logger.info(f"[DIALOG] {dialog.message}")
                await dialog.accept()
            
            self.page.on("dialog", lambda d: asyncio.create_task(handle_dialog(d)))

            # 등록 전 화면 캡처 (디버깅용)
            try:
                await self.page.screenshot(path="logs/reservation_before_dialog.png", full_page=True)
                logger.info("[RESERVE] 등록 전 스크린샷 저장 완료: logs/reservation_before_dialog.png")
            except Exception as sce:
                logger.warning(f"[RESERVE] 스크린샷 저장 실패: {sce}")

            # 등록 버튼 클릭 (ChkFrm 함수 실행)
            submit_btn = await self.page.query_selector("button:has-text('등록하기')")
            if submit_btn:
                await submit_btn.click()
            else:
                return {"status": "fail", "message": "등록 버튼을 찾을 수 없습니다."}

            # 8. 결과 확인 (URL 변경 대기)
            try:
                # 성공 시 목록 페이지로 리다이렉트됨
                await self.page.wait_for_url("**/RsvObj_List*", timeout=7000)
                return {
                    "status": "success",
                    "message": f"[{room_name}] {start_date}~{end_date} {start_time}~{end_time} 예약이 성공적으로 등록되었습니다.",
                    "url": self.page.url
                }
            except:
                error_msg = dialog_messages[-1] if dialog_messages else "페이지 이동 실패 (중복 예약 가능성)"
                logger.error(f"[RESERVE] 예약 실패 사유: {error_msg}")
                return {"status": "fail", "message": f"예약 실패: {error_msg}"}

        except Exception as e:
            logger.error(f"[ERROR] 회의실 예약 실패: {e}")
            return {"status": "fail", "message": str(e)}

    @staticmethod
    def _parse_room_kor_and_detail(room_html):
        room_kor = room_html.find("a").get_text(strip=True) if room_html.find("a") else ""
        room_detail = room_html.find("span")
        room_detail_txt = room_detail.get_text(strip=True) if room_detail else ""
        return room_kor, room_detail_txt

    @staticmethod
    def _parse_day_cell(td, day_name):
        day_data = []
        inner_tables = td.find_all("table")
        if inner_tables:
            for t in inner_tables:
                approve_info = t.find("font")
                if approve_info:
                    parsed_res = MeetingRoomCrawler.parse_reservation_text(approve_info.get_text(strip=True))
                    day_data.append(parsed_res)
        if not day_data:
            text = td.get_text(strip=True)
            if text == "+":
                day_data.append({"예약가능": True})
        if not day_data:
            day_data.append({})
        return {
            "요일": day_name,
            "예약": day_data
        }

    @staticmethod
    def parse_meeting_room_table(html):
        soup = BeautifulSoup(html, "html.parser")
        table = soup.find("table")
        if not table:
            raise ValueError("테이블이 없습니다!")

        # 1. 실제 헤더 행 찾기 ("분류명"이 포함된 행)
        header_row = None
        all_trs = table.find_all("tr")
        for tr in all_trs:
            tr_text = tr.get_text(strip=True)
            if "분류명" in tr_text and "Today" in tr_text:
                header_row = tr
                break
        
        if not header_row:
            header_row = all_trs[0] if all_trs else None
        
        if not header_row:
            raise ValueError("헤더 행을 찾을 수 없습니다.")

        headers = [td.get_text(strip=True) for td in header_row.find_all("td")]
        logger.debug(f"[DEBUG] Raw headers: {headers}")

        # 'Today' 또는 날짜 형식이 시작되는 인덱스 찾기
        start_day_idx = 4 # 기본값 (분류명, 공유물명, 관리자, 신청구분 뒤)
        for i, h in enumerate(headers):
            if "Today" in h or "(" in h and ")" in h: # "Today(2026-05-10)" 또는 "月(MON)(...)" 형식
                start_day_idx = i
                break
        
        day_columns = headers[start_day_idx:]
        logger.info(f"[DEBUG] Identified day columns: {day_columns}")

        result = []
        # 헤더 행 이후부터 데이터 처리
        header_reached = False
        for row in all_trs:
            cols = row.find_all("td")
            if not cols or len(cols) < 5:
                continue
            
            # 헤더 행을 만날 때까지 스킵
            if not header_reached:
                if "분류명" in row.get_text(strip=True):
                    header_reached = True
                continue

            # 데이터 로우 파싱
            category = cols[0].get_text(strip=True)
            # 분류명이 '분류명'이면 헤더이므로 스킵
            if category == "분류명" or not category:
                continue

            room_kor, room_detail_txt = MeetingRoomCrawler._parse_room_kor_and_detail(cols[1])
            manager = cols[2].get_text(strip=True)
            apply_type = cols[3].get_text(strip=True)

            # 요일별 데이터 매칭 (데이터 로우의 4번 인덱스부터 요일 시작)
            days = []
            day_cell_idx = 4
            for i in range(len(day_columns)):
                if day_cell_idx >= len(cols):
                    break
                
                td = cols[day_cell_idx]
                day_name = day_columns[i]
                
                # 예약 정보 파싱
                parsed_day = MeetingRoomCrawler._parse_day_cell(td, day_name)
                days.append(parsed_day)
                
                # 이 시스템의 특이점: 예약이 있으면 상세 셀(Detail)이 뒤따라옴.
                # 요약 셀(Summary)은 '+'가 있거나 여러 예약 테이블을 포함함.
                # 상세 셀은 보통 무시하고 다음 실제 요일 셀로 넘어가야 함.
                # 하지만 정확한 '상세 셀' 개수를 알기 어려우므로, 
                # 일단 1:1 매칭을 시도하되 인덱스 밀림을 최소화함.
                day_cell_idx += 1

            result.append({
                "분류명": category,
                "회의실명": room_kor,
                "상세정보": room_detail_txt,
                "관리자": manager,
                "신청구분": apply_type,
                "요일별": days
            })

        return result

    @staticmethod
    def parse_reservation_text(res_text: str):
        # 패턴: [승인] 11:00~11:50 제목(신청자,전화번호,...)
        m = re.match(
            r"(?:\[(?P<approve>.+?)\]\s*)?(?P<time>\d{2}:\d{2}~\d{2}:\d{2})?\s*(?P<title>.*?)(?:\((?P<people>.+?)\))?$",
            res_text.strip())
        if not m:
            return {
                "시간": None, "승인": None, "제목": res_text, "신청자": [], "전화번호": []
            }
        approve = bool(m.group("approve")) and ("승인" in m.group("approve"))
        time = m.group("time")
        subject = m.group("title").strip() if m.group("title") else ""
        applier = []
        tel = []
        if m.group("people"):
            people_parts = [p.strip() for p in m.group("people").split(",")]
            for part in people_parts:
                if re.match(r"\d{2,3}-\d{3,4}-\d{4}|010-\d{4}-\d{4}|\d{11}", part):
                    tel.append(part)
                elif part:
                    applier.append(part)
        return {
            "시간": time,
            "승인": approve,
            "제목": subject,
            "신청자": applier,
            "전화번호": tel,
        }
