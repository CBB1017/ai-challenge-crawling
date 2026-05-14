import asyncio
import re
from loguru import logger
from bs4 import BeautifulSoup

from app.core.config import LOGIN_INFO
from app.crawler.base import BaseCrawler


class MeetingRoomCrawler(BaseCrawler):
    # 회의실 명칭과 내부 ObjNo 매핑 (HTML 실제 명칭 반영)
    ROOM_MAPPING = {
        "에리스": 1,
        "캐프리콘": 2,
        "리브라": 3,
        "제미나이": 4,
        "미라이": 15,
        "스콜피오": 2,
        "리오①": 6,
        "리오②": 7,
        "리오③": 8,
        "리오④": 9,
        "파이시스①": 10,
        "파이시스②": 11,
        "파이시스③": 12,
        "파이시스④": 13,
        "SANTAFE(231호5640)": 14,
        "이클립스": 16
    }

    def __init__(self, cookies: list = None):
        super().__init__(cookies)

    def _resolve_room_info(self, input_name: str):
        """입력된 회의실 이름을 정규화하고 정식 명칭과 ObjNo를 반환합니다."""
        if not input_name:
            return None, None
            
        # 1. 기본 전처리: 공백 제거 및 대문자화
        target_name = input_name.strip().upper().replace(" ", "")
        
        # 2. 숫자(1-4)를 원문자(①-④)로 변환하는 로직 추가
        digit_to_circle = {
            "1": "①", "2": "②", "3": "③", "4": "④"
        }
        for digit, circle in digit_to_circle.items():
            if digit in target_name:
                target_name = target_name.replace(digit, circle)
        
        # 3. SANTAFE 특수 처리
        if "산타페" in target_name or "SANTAFE" in target_name:
            return "SANTAFE(231호5640)", 14

        # 4. 전체 매핑 순회하며 매칭 확인
        for formal_name, obj_no in self.ROOM_MAPPING.items():
            normalized_formal = formal_name.upper().replace(" ", "")
            if target_name == normalized_formal:
                return formal_name, obj_no
        
        return None, None

    def _is_overlapping(self, start_time: str, end_time: str, existing_reservations: list):
        """
        시간 겹침 및 10분 간격 여부를 확인합니다.
        규정: 이전 예약 종료 시간과 다음 예약 시작 시간 사이에 최소 10분의 간격이 있어야 합니다.
        예: 12:00 ~ 13:00 예약이 있으면 다음 예약은 13:10부터 가능합니다.
        """
        def to_minutes(t_str):
            try:
                # '14:00' 또는 '오전 10:00' 등의 형식 대응
                t_str = t_str.replace("오전", "").replace("오후", "").strip()
                h, m = map(int, t_str.split(':'))
                return h * 60 + m
            except:
                return 0

        new_s = to_minutes(start_time)
        new_e = to_minutes(end_time)

        if new_s >= new_e:
            return True, "시작 시간이 종료 시간보다 늦거나 같습니다."

        for res in existing_reservations:
            # res format: "10:00 - 11:00 [제목]" 또는 "10:00~10:50 AI사업부"
            match = re.search(r'(\d{1,2}:\d{2})\s*[-~]\s*(\d{1,2}:\d{2})', res)
            if match:
                ex_s = to_minutes(match.group(1))
                ex_e = to_minutes(match.group(2))

                # 10분 간격 규정 적용:
                # (새 시작 < 기존 종료 + 10) AND (기존 시작 < 새 종료 + 10)
                if new_s < ex_e + 10 and ex_s < new_e + 10:
                    return True, f"기존 예약({match.group(1)} - {match.group(2)})과 10분 이상의 간격이 필요합니다."
        
        return False, None

    async def fetch_reservations(self, room_name: str = None):
        try:
            # 1. 예약 페이지 이동
            url = f"{LOGIN_INFO['domain']}/RsvObjMgr/RsvObj_List"
            await self.page.goto(url)
            logger.info(f"[DEBUG] Navigated to URL: {self.page.url}")

            # 2. 테이블 렌더링 대기
            try:
                await self.page.wait_for_selector('td.table_title', timeout=5000)
            except Exception:
                await self.page.wait_for_timeout(2000)

            # 전체 페이지 content를 가져와 BeautifulSoup으로 한 번에 처리
            content = await self.page.content()
            parsed_data = self.parse_meeting_room_table(content)

            if room_name and room_name != "전체":
                resolved_name, _ = self._resolve_room_info(room_name)
                search_term = resolved_name if resolved_name else room_name
                parsed_data = [room for room in parsed_data if search_term in room["공유물명"]]

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
        """
        try:
            input_room_name = reservation_data.get("room_name")
            formal_room_name, obj_no = self._resolve_room_info(input_room_name)
            
            if not formal_room_name:
                valid_rooms = ", ".join(self.ROOM_MAPPING.keys())
                return {
                    "status": "fail", 
                    "message": f"'{input_room_name}'은(는) 유효한 회의실 이름이 아닙니다. 정확한 이름을 입력해주세요. (예: {valid_rooms})"
                }

            start_date = reservation_data.get("start_date")
            end_date = reservation_data.get("end_date") or start_date
            start_time = reservation_data.get("start_time")
            end_time = reservation_data.get("end_time")
            title = reservation_data.get("title")
            people_count = str(reservation_data.get("people_count", "1"))
            description = reservation_data.get("description", ".")

            # 0. 시간 형식 및 10분 단위 검증
            try:
                s_h, s_m = start_time.split(":")
                e_h, e_m = end_time.split(":")
                if int(s_m) % 10 != 0 or int(e_m) % 10 != 0:
                    return {
                        "status": "fail",
                        "message": f"회의실 예약은 10분 단위로만 가능합니다. (입력값: {start_time} - {end_time})"
                    }
            except Exception:
                return {"status": "fail", "message": "시간 형식이 올바르지 않습니다. (예: 14:00)"}

            # 1. 중복 예약 사전 체크 (선택 사항이나 권장됨)
            status_res = await self.fetch_reservations(formal_room_name)
            if status_res["status"] == "success" and status_res["data"]:
                room_data = status_res["data"][0]
                # 해당 날짜의 예약 목록 찾기
                for date_key, reservations in room_data.get("예약현황", {}).items():
                    if start_date in date_key:
                        is_over, reason = self._is_overlapping(start_time, end_time, reservations)
                        if is_over:
                            logger.warning(f"[RESERVE] 중복 예약 감지: {reason}")
                            return {"status": "fail", "message": f"해당 시간에 이미 예약이 있습니다. ({reason})"}

            # 2. 예약 페이지 이동
            encoded_room_name = f"[{formal_room_name}]"
            url = f"{LOGIN_INFO['domain']}/RsvObjMgr/RsvObjUse_Trans.asp?ObjNo={obj_no}&Seq=-1&SelDate={start_date}&ObjName={encoded_room_name}"
            await self.page.goto(url)
            logger.info(f"[RESERVE] Moving to reservation page: {self.page.url} (ObjNo: {obj_no})")

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
                    "message": f"[{formal_room_name}] {start_date}~{end_date} {start_time}~{end_time} 예약이 성공적으로 등록되었습니다.",
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
    def parse_meeting_room_table(html: str):
        soup = BeautifulSoup(html, "html.parser")

        # 1. 'table_title' 클래스를 가진 td가 포함된 tr을 찾아 헤더로 설정
        header_td = soup.find("td", class_="table_title")
        if not header_td:
            raise ValueError("예약 테이블 헤더를 찾을 수 없습니다.")

        header_row = header_td.find_parent("tr")
        target_table = header_row.find_parent("table")

        # 날짜 헤더 추출 (5번째 열부터 11번째 열까지)
        all_header_tds = header_row.find_all("td", recursive=False)
        day_headers = [td.get_text(separator=" ", strip=True) for td in all_header_tds[4:11]]

        result = []
        # 2. 헤더 다음 행부터 데이터 추출
        for tr in header_row.find_next_siblings("tr"):
            cols = tr.find_all("td", recursive=False)
            if len(cols) < 11:
                continue

            # 공유물명 추출: td 내부의 텍스트와 span 내의 상세 설명을 합치거나 핵심만 추출
            # a 태그가 없을 경우를 대비해 td 전체 텍스트에서 불필요한 [사용일지] 등 제거
            raw_room_info = cols[1].get_text(separator=" ", strip=True)
            room_name = raw_room_info.replace("[사용일지]", "").strip()

            days_data = {}
            # 3. 7일치 예약 데이터 (인덱스 4 ~ 10)
            for i in range(7):
                td_idx = 4 + i
                date_label = day_headers[i] if i < len(day_headers) else f"날짜_{i}"

                # 중첩된 테이블 내의 <font> 태그 텍스트만 추출
                reservations = []
                inner_fonts = cols[td_idx].find_all("font")
                for font in inner_fonts:
                    res_text = font.get_text(strip=True)
                    if res_text and res_text != "+":  # '+' 기호 등 불필요한 텍스트 제외
                        reservations.append(res_text)

                # 예약이 없으면 빈 리스트 대신 None이나 생략하여 LLM 토큰 절약 가능
                if reservations:
                    days_data[date_label] = reservations

            if days_data:  # 예약 정보가 하나라도 있는 방만 추가하거나, 구조 유지를 위해 포함
                result.append({
                    "공유물명": room_name,
                    "예약현황": days_data
                })

        return result