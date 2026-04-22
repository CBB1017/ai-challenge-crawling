import re
import asyncio
from typing import List, Dict
from bs4 import BeautifulSoup
from loguru import logger
from app.crawler.base import BaseCrawler

class EmailCrawler(BaseCrawler):
    async def fetch_email_list(self, limit: int = 10) -> List[Dict]:
        """이메일 대시보드에서 최근 수신 이메일 목록을 가져옵니다."""
        url = f"{self.base_url}/email/Dashboard"
        logger.info(f"이메일 목록 조회 시도: {url}")
        
        try:
            # 타임아웃 20초 설정
            await self.page.goto(url, timeout=20000, wait_until="domcontentloaded")
            await self.page.wait_for_selector("#tblRecvMailList", timeout=10000)
            logger.debug("이메일 목록 테이블 로드 완료")
        except Exception as e:
            logger.error(f"이메일 목록 페이지 로딩 실패: {e}")
            return []
        
        content = await self.page.content()
        soup = BeautifulSoup(content, "html.parser")
        table = soup.find("table", id="tblRecvMailList")
        
        emails = []
        if not table:
            logger.warning("이메일 목록 테이블(#tblRecvMailList)을 찾을 수 없습니다.")
            return emails

        tbody = table.find("tbody")
        if not tbody:
            logger.warning("이메일 목록 tbody를 찾을 수 없습니다.")
            return emails

        rows = tbody.find_all("tr")[:limit]
        logger.debug(f"파싱할 이메일 행 개수: {len(rows)}")
        for i, row in enumerate(rows):
            cols = row.find_all("td")
            if len(cols) < 5:
                logger.trace(f"행 {i}: 컬럼 부족으로 건너뜀")
                continue
            
            # 보낸 사람 (3번째 td)
            sender_col = cols[2]
            sender_name = sender_col.get_text(strip=True)
            
            # 제목 및 CSRF 토큰(param1) 추출 (4번째 td)
            subject_col = cols[3]
            subject_link = subject_col.find("a", class_="clsEmailPreCheckDocView")
            subject = subject_link.get_text(strip=True) if subject_link else "제목 없음"
            
            # PopupMailView('토큰', 0); 에서 토큰 추출
            img_tag = subject_col.find("img", onclick=True)
            csrf_token = ""
            if img_tag:
                onclick_val = img_tag["onclick"]
                match = re.search(r"PopupMailView\('([^']*)'", onclick_val)
                if match:
                    csrf_token = match.group(1)
            
            # 수신 시간 (5번째 td)
            date_time = cols[4].get_text(strip=True)
            
            logger.debug(f"[{i+1}/{len(rows)}] 메일 정보 추출: {sender_name} - {subject[:20]}...")
            emails.append({
                "sender": sender_name,
                "subject": subject,
                "date_time": date_time,
                "csrf_token": csrf_token
            })
        
        logger.info(f"이메일 {len(emails)}건 수집 완료")
        return emails

    async def fetch_email_detail(self, csrf_token: str) -> Dict:
        """이메일 상세 본문(Iframe)에서 텍스트와 첨부파일을 추출합니다."""
        if not csrf_token:
            logger.warning("CSRF 토큰이 없어 상세 정보를 가져올 수 없습니다.")
            return {"content": "상세 내용을 가져올 수 없습니다. (토큰 없음)", "attachments": []}

        # 사용자가 지정한 Iframe 주소로 이동
        detail_url = f"{self.base_url}/email/DocViewBodyIframe?csrf={csrf_token}&logdown=N"
        logger.info(f"이메일 상세 본문(Iframe) 요청: {detail_url}")
        
        try:
            await self.page.goto(detail_url, timeout=15000, wait_until="domcontentloaded")
            await asyncio.sleep(0.5)
            logger.debug("상세 본문 페이지 로드 완료 및 파싱 시작")
            content_html = await self.page.content()
        except Exception as e:
            logger.error(f"이메일 상세 본문 로딩 실패: {e}")
            return {"content": "본문 로딩 실패", "attachments": []}
            
        soup = BeautifulSoup(content_html, "html.parser")
        
        # 1. 이미지, 스크립트, 스타일 제거
        logger.debug("본문 정제 시작 (스크립트/스타일/이미지 제거)")
        for s in soup(["script", "style", "img"]):
            s.decompose()
            
        # 2. 본문 300자 요약 (이미지는 무시됨)
        text = soup.get_text(separator=" ", strip=True)
        summary = text[:300] + ("..." if len(text) > 300 else "")
        logger.debug(f"본문 요약 완료 (약 {len(summary)}자)")
        
        # 3. 첨부파일 추출
        logger.debug("첨부파일 추출 시도")
        attachments = []
        attach_links = soup.find_all("a", href=re.compile(r"download|file|attach", re.I))
        for link in attach_links:
            href = link.get("href", "")
            if not href:
                continue
                
            # 상대 경로 처리
            if href.startswith("./"):
                href = f"{self.base_url}/email/{href[2:]}"
            elif href.startswith("/"):
                domain_match = re.match(r"(https?://[^/]+)", self.base_url)
                if domain_match:
                    href = f"{domain_match.group(1)}{href}"
            
            name = link.get_text(strip=True) or link.get("title", "첨부파일")
            attachments.append({"name": name, "url": href})
            
        logger.success(f"상세 정보 추출 성공: 본문 요약 및 첨부파일 {len(attachments)}건")
        return {
            "content": summary,
            "attachments": attachments
        }

    @property
    def base_url(self) -> str:
        """그룹웨어 기본 URL을 반환합니다."""
        from app.core.config import LOGIN_INFO
        return LOGIN_INFO["domain"].rstrip("/")
