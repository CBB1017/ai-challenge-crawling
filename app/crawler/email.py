import os
from typing import List, Dict
from bs4 import BeautifulSoup
from loguru import logger
from app.crawler.base import BaseCrawler


class EmailCrawler(BaseCrawler):

    async def fetch_recent_emails(self) -> List[Dict]:
        """최신 이메일 목록을 가져와서 파싱합니다."""
        groupware_domain = os.environ["GROUPWARE_DOMAIN"]

        url = f"{groupware_domain}/email/Dashboard"
        logger.info(f"이메일 대시보드 접속 시도: {url}")

        await self.page.goto(url)
        # 테이블이 로드될 때까지 대기
        await self.page.wait_for_selector("#tblRecvMailList")

        content = await self.page.content()
        soup = BeautifulSoup(content, "html.parser")
        table = soup.find("table", id="tblRecvMailList")

        emails = []
        if not table:
            logger.warning("이메일 목록 테이블을 찾을 수 없습니다.")
            return emails

        rows = table.find("tbody").find_all("tr")
        for row in rows:
            cols = row.find_all("td")
            if len(cols) < 5:
                continue

            # 보낸 사람 추출
            sender_col = cols[2]
            sender_name = sender_col.get_text(strip=True)
            sender_email = sender_col.find("span")["title"] if sender_col.find("span") else ""

            # 제목 추출
            subject_col = cols[3]
            subject_link = subject_col.find("a", class_="clsEmailPreCheckDocView")
            subject = subject_link.get_text(strip=True) if subject_link else ""

            # 읽음 상태 확인 (font-weight:bold 면 안읽음)
            is_unread = False
            if subject_link and subject_link.find("span", style=lambda x: x and "font-weight:bold" in x):
                is_unread = True

            # 날짜/시간 추출
            date_time = cols[4].get_text(strip=True)

            emails.append({
                "sender": f"{sender_name} ({sender_email})",
                "subject": subject,
                "date_time": date_time,
                "is_unread": is_unread
            })

        return emails