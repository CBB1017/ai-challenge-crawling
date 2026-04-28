import re
from bs4 import BeautifulSoup
from loguru import logger
from app.core.config import LOGIN_INFO
from app.crawler.base import BaseCrawler


class EtcCrawler(BaseCrawler):
    def __init__(self, cookies: list = None, user_id: str = None):
        super().__init__(cookies, user_id)

    async def fetch_birthdays(self):
        """
        /Etc/Birthday 페이지에서 해당 달의 생일자 목록을 가져옵니다.
        """
        if not self.cookies:
            logger.warning("유효한 세션(쿠키)이 없습니다.")
            return {"status": "fail", "message": "세션이 만료되었습니다.", "code": "SESSION_EXPIRED"}

        try:
            birthday_url = f"{LOGIN_INFO['domain']}/Etc/Birthday"
            await self.page.goto(birthday_url, wait_until="networkidle")

            # 테이블 본문 추출
            content_html = await self.page.content()
            soup = BeautifulSoup(content_html, "html.parser")

            # td[align=left] 내부의 테이블들이 개별 생일자 정보임
            rows = soup.find_all('td', align='left')

            birthday_list = []
            for row in rows:
                text = row.get_text(strip=True)
                if not text:
                    continue

                # 정규식으로 (일자), 이름/직급, 소속 분리
                # 예: (04일) 장정훈 사원 ((주)브라이센코리아->...)
                match = re.search(r'\((\d+일)\)\s*([^\(]+)\s*\((.+)\)', text)
                if match:
                    day = match.group(1).strip()
                    name_pos = match.group(2).strip()
                    dept_path = match.group(3).strip()

                    # 이름과 직급 분리 (마지막 공백 기준)
                    name_parts = name_pos.rsplit(maxsplit=1)
                    name = name_parts[0] if len(name_parts) > 1 else name_pos
                    position = name_parts[1] if len(name_parts) > 1 else ""

                    birthday_list.append({
                        "day": day,
                        "name": name,
                        "position": position,
                        "department": dept_path
                    })

            return {
                "status": "success",
                "message": f"생일자 {len(birthday_list)}명 조회 성공",
                "data": birthday_list
            }

        except Exception as e:
            logger.error(f"[ERROR] 생일자 크롤링 실패: {e}")
            return {"status": "error", "message": str(e)}