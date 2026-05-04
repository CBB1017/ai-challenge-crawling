import asyncio
import re
from bs4 import BeautifulSoup
from loguru import logger
from app.core.config import LOGIN_INFO
from app.crawler.base import BaseCrawler
from app.session.session_manage_decorator import requires_system_session


class EtcCrawler(BaseCrawler):
    def __init__(self, cookies: list = None, user_id: str = None):
        super().__init__(cookies, user_id=user_id, password=LOGIN_INFO["password"])

    @requires_system_session
    async def fetch_birthdays(self, cookies: list = None):
        """
        /Etc/Birthday 페이지에서 해당 달의 생일자 목록을 가져옵니다.
        """
        if cookies:
            self.cookies = cookies

        if not self.cookies:
            logger.warning("유효한 세션(쿠키)이 없습니다.")
            return {"status": "fail", "message": "세션이 만료되었습니다.", "code": "SESSION_EXPIRED"}

        try:
            # 1. 페이지 이동 전 세션 체크 및 필요시 메인 페이지 진입
            birthday_url = f"{LOGIN_INFO['domain']}/Etc/Birthday"
            logger.info(f"생일자 페이지 이동 시도: {birthday_url}")
            
            # 직접 이동 시도
            await self.page.goto(birthday_url, wait_until="networkidle")
            
            # 2. 로그인 여부 확인
            content_html = await self.page.content()
            logger.debug(f"초기 페이지 HTML 길이: {len(content_html)} 자")
            
            if "ZioYou Login" in content_html or "UserID" in content_html or len(content_html) < 5000:
                logger.warning("세션이 만료되었거나 로그인 페이지입니다. 재로그인을 시도합니다.")
                success, new_cookies, _ = await self.login()
                if success:
                    from app.session.session_manager import save_session
                    await save_session(self.username, new_cookies)
                    self.cookies = new_cookies
                    # 재로그인 후 다시 이동
                    await self.page.goto(birthday_url, wait_until="networkidle")
                else:
                    return {"status": "fail", "message": "로그인에 실패했습니다."}

            # 3. iframe 구조 내에서 페이지가 로드되었는지 확인
            # 그룹웨어 특성상 goMainUrl() 스크립트를 사용하여 mainFrame의 src를 변경하는 것이 안전함
            await self.page.evaluate(f"if(window.goMainUrl) goMainUrl('{birthday_url}');")
            await asyncio.sleep(2) # 로딩 대기
            
            # 4. 데이터 추출 (mainFrame 우선 탐색)
            frame = self.page.frame(name="mainFrame")
            if frame:
                logger.info("mainFrame을 찾았습니다. 내부 데이터를 탐색합니다.")
                # 특정 데이터가 나타날 때까지 대기
                try:
                    await frame.wait_for_selector('td[align=left]', timeout=5000)
                except:
                    logger.debug("td[align=left] 대기 타임아웃 (데이터가 없을 수 있음)")
                
                content_html = await frame.content()
            else:
                logger.info("mainFrame이 없습니다. 메인 페이지 데이터를 탐색합니다.")
                content_html = await self.page.content()

            logger.debug(f"대상 HTML 길이: {len(content_html)} 자")
            soup = BeautifulSoup(content_html, "html.parser")
            data_cells = soup.find_all('td', align='left')
            logger.info(f"파싱된 td[align=left] 개수: {len(data_cells)}")

            birthday_list = []
            seen_entries = set()  # 중복 제거용

            for i, cell in enumerate(data_cells):
                # 셀 내부의 텍스트를 정규화 (HTML 엔티티 및 공백 처리)
                text = cell.get_text(" ", strip=True).replace('\xa0', ' ')
                if not text:
                    continue
                
                # 정규식 개선: (숫자일) 이름/직급 (소속)
                # (01일) 곽승원 주임 ((주)브라이센코리아->...)
                # 괄호와 텍스트 사이의 유연한 매칭을 위해 수정
                # 소속 정보가 (주)브라이센코리아 처럼 중첩된 괄호를 가질 수 있으므로 탐색 범위 확장
                # finditer를 사용하여 한 셀 내에 여러 명이 있는 경우 모두 추출
                matches = re.finditer(r'\((\d+일)\)\s*([^\(]+?)\s*\((.+?)\)(?=\s*\(\d+일\)|\s*$)', text)
                
                found_in_cell = False
                for match in matches:
                    found_in_cell = True
                    day = match.group(1).strip()
                    name_pos = match.group(2).strip()
                    dept_path = match.group(3).strip()
                    
                    # 이름과 직급 분리 (마지막 공백 기준)
                    name_parts = name_pos.rsplit(maxsplit=1)
                    name = name_parts[0].strip()
                    position = name_parts[1].strip() if len(name_parts) > 1 else ""

                    # 부서명 간소화: (주)브라이센코리아 제외 및 공백으로 연결
                    dept_parts = [p.strip() for p in dept_path.split("->")]
                    simplified_dept = " ".join([p for p in dept_parts if p != "(주)브라이센코리아" and p])

                    entry_key = f"{day}-{name}-{position}"
                    if entry_key not in seen_entries:
                        birthday_list.append({
                            "day": day,
                            "name": name,
                            "position": position,
                            "department": simplified_dept
                        })
                        seen_entries.add(entry_key)
                        logger.debug(f"Row {i}: 추출 성공 - {day} {name} {position}")

                if not found_in_cell:
                    # 기존 방식 (단일 매칭) 보조
                    match = re.search(r'\((\d+일)\)\s*([^\(]+?)\s*\((.+)\)', text)
                    if match:
                         # ... (생략 가능하지만 안전을 위해 유지)
                         pass

            logger.success(f"최종 추출된 생일자 수: {len(birthday_list)}")
            return {
                "status": "success",
                "message": f"생일자 {len(birthday_list)}명 조회 성공",
                "data": birthday_list
            }

        except Exception as e:
            logger.error(f"[ERROR] 생일자 크롤링 실패: {e}")
            return {"status": "error", "message": str(e)}