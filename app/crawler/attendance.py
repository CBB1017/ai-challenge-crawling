import os
from bs4 import BeautifulSoup
import re
from loguru import logger

from app.core.server import BaseCrawler


class AttendanceCrawler(BaseCrawler):
    def __init__(self, login_url: str, username: str, password: str, cookies: list = None):
        super().__init__(login_url, username, password, cookies)

    async def fetch_attendance(self, groupware_domain: str):
        # 1. 쿠키가 없으면(최초 요청) 로그인을 수행
        if not self.cookies:
            success, new_cookies = await self.login()
            if not success:
                return {"status": "fail", "message": "로그인 실패", "data": None}
            # 로그인 성공 시 추출한 쿠키를 현재 객체에 저장
            self.cookies = new_cookies

        # 2. 근태 페이지 이동 및 HTML 추출
        soup = await self.get_attendance_table_html(groupware_domain)
        if not soup:
            return {"status": "fail", "message": "근태 데이터(테이블) 없음", "data": None}

        # 3. 데이터 파싱
        daily_results = self.parse_attendance_table_dynamic(soup)
        mapped_rows = self.map_attendance_keys(daily_results)
        team_grouped = self.group_by_team(mapped_rows)

        return {
            "status": "success",
            "message": "조회 성공",
            "data": team_grouped,
            "cookies": self.cookies  # 갱신/유지된 쿠키 반환
        }

    async def get_attendance_table_html(self, groupware_domain: str):
        try:
            attendance_url = f"{groupware_domain}/AttendR2/AttendRegist"

            # 페이지 이동 (쿠키가 유효하다면 로그인 화면을 거치지 않고 바로 진입됨)
            response = await self.page.goto(attendance_url)

            # 만약 세션이 만료되어 로그인 페이지로 리다이렉트 되었다면?
            if "login" in self.page.url.lower():
                logger.warning("세션이 만료되어 로그인 페이지로 리다이렉트 되었습니다. 재로그인 시도...")
                success, self.cookies = await self.login()
                if not success: return None
                await self.page.goto(attendance_url)

            table = await self.page.query_selector('#objTblBody')
            if not table:
                logger.error('[ERROR] 출석 테이블(#objTblBody) 없음')
                return None

            html = await table.inner_html()
            return BeautifulSoup(f"<table>{html}</table>", "html.parser")

        except Exception as e:
            logger.error(f"[ERROR] table 크롤링 실패: {e}")
            return None

    @staticmethod
    def expand_header_row(header_cells):
        names = []
        rowspans = []
        for cell in header_cells:
            name = cell.get_text(strip=True)
            colspan = int(cell.get('colspan', 1))
            rowspan = int(cell.get('rowspan', 1))
            names.extend([name] * colspan)
            rowspans.extend([rowspan] * colspan)
        return names, rowspans

    @staticmethod
    def expand_second_header_row(header_cells, rowspan1):
        names = []
        idx = 0
        for rs in rowspan1:
            if rs == 2:
                names.append('')
            else:
                cell = header_cells[idx]
                name = cell.get_text(strip=True)
                colspan = int(cell.get('colspan', 1))
                names.extend([name] * colspan)
                idx += 1
        return names

    @staticmethod
    def make_columns(h1, h2):
        columns = []
        used = {}
        for main, sub in zip(h1, h2):
            col = sub if sub else main
            if col in used:
                used[col] += 1
                col = f"{col}_{used[col]}"
            else:
                used[col] = 0
            columns.append(col)
        return columns

    @staticmethod
    def parse_data_rows(data_rows, columns):
        results = []
        for row in data_rows:
            tds = row.find_all(['td', 'th'])
            row_data = {}
            for idx, cell in enumerate(tds):
                col_name = columns[idx] if idx < len(columns) else f"col{idx}"
                row_data[col_name] = cell.get_text(strip=True)
            if any(row_data.values()):
                results.append(row_data)
        return results

    def parse_attendance_table_dynamic(self, soup):
        trs = soup.find_all('tr')
        header_rows = trs[:2]
        data_rows = trs[2:]

        h1_cells = header_rows[0].find_all(['th', 'td'])
        h1, rowspan1 = self.expand_header_row(h1_cells)

        h2_cells = header_rows[1].find_all(['th', 'td'])
        h2 = self.expand_second_header_row(h2_cells, rowspan1)

        columns = self.make_columns(h1, h2)
        results = self.parse_data_rows(data_rows, columns)
        return results

    @staticmethod
    def map_attendance_keys(results):
        # 위에서 설명한 convert_row 전체 리스트 변환 코드
        column_map = {
            "조직도": "member",
            "근무구분": "planType",
            "출근~퇴근": "planInOut",
            "근무시간": "planWorkHour",
            "자동퇴근": "planAutoOut",
            "근무구분_1": "actualType",
            "출근시분": "actualIn",
            "퇴근시분": "actualOut",
            "근무시간_1": "actualWorkHour",
            "지각": "late",
            "예외근무": "exceptionWork",
            "조퇴": "earlyLeave",
            "OT": "ot",
            "휴가": "vacation",
            "변경신청": "approvalRequest",
        }

        def split_name_position(member_full):
            positions = ["팀장", "부장", "차장", "과장", "대리", "주임", "사원"]
            for pos in positions:
                if member_full.endswith(pos):
                    return member_full[:-len(pos)].strip(), pos
            return member_full, ""

        def convert_row(row):
            mapped = {column_map.get(k.strip(), k.strip()): v.strip() for k, v in row.items()}
            # member → name/position 분리
            if "member" in mapped:
                # ... 제거
                member_full = re.sub(r"^[.]+", "", mapped["member"]).strip()
                name, position = split_name_position(member_full)
                mapped["memberName"] = name
                mapped["position"] = position
                del mapped["member"]  # 기존 member 필드는 제거
            return mapped

        return [convert_row(row) for row in results]

    @staticmethod
    def group_by_team(rows):
        result = {}
        current_team = None
        for row in rows:
            member_name = row.get("memberName", "")
            position = row.get("position", "")
            # 팀 구분행 조건: memberName이 있고, position은 없으며, 나머지 모든 필드가 비어있을 때
            if (
                    member_name
                    and not position
                    and all(not v for k, v in row.items() if k not in ("memberName", "position"))
            ):
                # 팀명은 memberName 필드로
                current_team = member_name
                if current_team not in result:
                    result[current_team] = []
                continue
            # 실제 유저 데이터라면, 현재 팀에 추가
            if current_team:
                result[current_team].append(row)
        return result
