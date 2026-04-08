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
            # 2. 회의실 예약 페이지 이동
            url = f"{LOGIN_INFO["domain"]}/RsvObjMgr/RsvObj_List?cmbCateNo=1#RsvObjMgrLeftBoxShare0"
            await self.page.goto(url)

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

        header_row = table.find("tr")
        headers = [td.get_text(strip=True) for td in header_row.find_all("td")]
        day_columns = headers[4:]

        result = []
        for row in table.find_all("tr")[1:]:
            cols = row.find_all("td")
            if not cols or len(cols) < 5:
                continue

            category = cols[0].get_text(strip=True)
            room_kor, room_detail_txt = MeetingRoomCrawler._parse_room_kor_and_detail(cols[1])
            manager = cols[2].get_text(strip=True)
            apply_type = cols[3].get_text(strip=True)

            days = [
                MeetingRoomCrawler._parse_day_cell(td, day_columns[idx] if idx < len(day_columns) else f"day{idx + 1}")
                for idx, td in enumerate(cols[4:])
            ]

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
