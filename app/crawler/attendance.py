import asyncio
import json
from datetime import date
from typing import Any

from bs4 import BeautifulSoup
import re
from loguru import logger

from app.core.config import LOGIN_INFO
from app.crawler.base import BaseCrawler
from app.session.session_manager import get_session

# 부서명과 별칭들을 하나의 튜플로 묶어서 관리합니다.
DEPT_MAP_CONFIG = [
    {"code": "9", "names": ("DX사업부", "디엑스사업부", "dx본부")},
    {"code": "17", "names": ("DX 1Team", "DX1팀", "디엑스1팀", "1팀")},
    {"code": "29", "names": ("DX 2Team", "DX2팀", "디엑스2팀", "2팀")},
    {"code": "32", "names": ("DX 3Team", "DX3팀", "디엑스3팀", "3팀")},
    {"code": "33", "names": ("DX 4Team", "DX4팀", "디엑스4팀", "4팀")},
    {"code": "30", "names": ("Design Team", "디자인팀", "디자인", "design")},
    {"code": "34", "names": ("서비스기획팀", "서비스기획팀", "기획", "서비스")},
    {"code": "31", "names": ("AI사업부", "AI", "AI사업", "에이아이")},
]


def find_dept_code(user_input: str) -> None | str | tuple[str, str, str] | Any:
    if not user_input:
        return None

    clean_input = user_input.replace(" ", "").lower()

    # --- 1단계: 완전 일치 우선 검색 (이건 그대로 유지) ---
    for dept in DEPT_MAP_CONFIG:
        for alias in dept["names"]:
            if clean_input == alias.replace(" ", "").lower():
                return dept["code"]

    # --- 2단계: 부분 일치 (긴 이름부터 검사하여 '가로채기' 방지) ---
    # 모든 별칭을 (이름, 코드) 튜플 리스트로 평탄화한 뒤, 이름이 긴 순서대로 정렬합니다.
    all_aliases = []
    for dept in DEPT_MAP_CONFIG:
        for alias in dept["names"]:
            all_aliases.append((alias.replace(" ", "").lower(), dept["code"]))

    # 이름 길이 기준 내림차순 정렬 (예: 'dx2team'이 'dx'보다 먼저 검사됨)
    all_aliases.sort(key=lambda x: len(x[0]), reverse=True)

    for alias_name, code in all_aliases:
        # 입력값이 별칭에 포함되거나, 별칭이 입력값에 포함되는지 확인
        if clean_input in alias_name or alias_name in clean_input:
            return code

    return None
class AttendanceCrawler(BaseCrawler):
    def __init__(self, cookies: list = None):
        super().__init__(cookies)

    async def fetch_attendance(self, ot_date: str = None, dept_name: str = None):
        """0. 페이지 진입 전, 근태 데이터 사전 검증 (Fail-Fast)"""
        if not self.cookies:
            # 쿠키가 없으면 무조건 에러를 뱉고 뻗습니다.
            # 프론트엔드는 이 에러를 받아 사용자를 로그인 창으로 튕겨냅니다.
            logger.warning("유효한 세션(쿠키)이 없습니다. 프론트엔드 리다이렉트 필요.")
            return {"status": "fail", "message": "세션이 만료되었습니다. 다시 로그인해주세요.", "code": "SESSION_EXPIRED"}

        # 1. 필수 파라미터 체크
        if not dept_name:
            raise ValueError("부서명(dept_name)은 필수 입력 항목입니다.")
        logger.info(f"부서 : {dept_name}, 날짜={ot_date}")

        # 2. 유연한 부서 코드 검색
        org_code = find_dept_code(dept_name)

        if not org_code:
            logger.error(f"부서를 찾을 수 없음: {dept_name}")
            return {"status": "fail", "message": f"'{dept_name}'에 해당하는 부서를 찾을 수 없습니다.", "data": None}

        # 3. 날짜 설정 (없으면 오늘)
        target_date = ot_date if ot_date else date.today().isoformat()

        logger.info(f"조회 시작: 부서={dept_name}(코드:{org_code}), 날짜={target_date}")

        soup = await self.get_attendance_table_html(target_date, org_code)

        if not soup:
            return {"status": "fail", "message": f"{target_date} / {dept_name} 데이터 없음", "data": None}

        # 6. 데이터 파싱
        daily_results = self.parse_attendance_table_dynamic(soup)
        mapped_rows = self.map_attendance_keys(daily_results)
        team_grouped = self.group_by_team(mapped_rows)

        return {
            "status": "success",
            "message": f"{target_date} 조회 성공",
            "data": team_grouped,
            "cookies": self.cookies
        }


    async def get_attendance_table_html(self, target_date: str = None, org_code: str = None):
        """
        target_date: '2026-03-23' 형식 (None이면 오늘)
        org_code: '9' (DX사업부), '17' (DX 1Team) 등 (None이면 기본값)
        """
        try:
            attendance_url = f"{LOGIN_INFO["domain"]}/AttendR2/AttendRegist"
            await self.page.goto(attendance_url)

            # 2. 부서(조직) 선택 (org_code가 있을 경우)
            if org_code:
                logger.info(f"부서 변경 시도: {org_code}")
                # select 태그의 value 값을 선택하고 페이지 로딩 대기
                await asyncio.gather(
                    self.page.select_option('select[name="LookupOrgCode"]', value=str(org_code)),
                    self.page.wait_for_load_state("networkidle")
                )

            # 3. 날짜 변경 로직 (target_date가 있을 경우)
            if target_date:
                logger.info(f"날짜 변경 시도: {target_date}")
                # goToDay 함수 호출 후 페이지 로딩 대기
                await asyncio.gather(
                    self.page.evaluate(f"goToDay('{target_date}')"),
                    self.page.wait_for_load_state("networkidle")
                )

            # 4. 최종 테이블 데이터 추출
            # 페이지 로딩 후 테이블이 나타날 때까지 확실히 대기
            await self.page.wait_for_selector('#objTblBody', timeout=5000)
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
