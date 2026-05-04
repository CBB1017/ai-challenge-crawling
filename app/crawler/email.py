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
            
            # 첨부파일 아이콘 확인 (보통 2번째 또는 3번째 td)
            # 사용자의 설명에 따르면 <td class="table_list" ...><img src="...Attachment.gif"> 가 존재
            has_attachment = False
            for col in cols[:3]:
                if col.find("img", src=re.compile(r"Attachment\.gif", re.I)):
                    has_attachment = True
                    break

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
            
            logger.debug(f"[{i+1}/{len(rows)}] 메일 정보 추출: {sender_name} - {subject[:20]}... (첨부: {has_attachment})")
            emails.append({
                "sender": sender_name,
                "subject": subject,
                "date_time": date_time,
                "csrf_token": csrf_token,
                "has_attachment": has_attachment
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
            # await asyncio.sleep(0.5)
            logger.debug("상세 본문 페이지 로드 완료 및 파싱 시작")
            content_html = await self.page.content()
        except Exception as e:
            logger.error(f"이메일 상세 본문 로딩 실패: {e}")
            return {"content": "본문 로딩 실패", "attachments": []}
            
        soup = BeautifulSoup(content_html, "html.parser")

        # 0. 대용량 첨부파일 추출 (ico_clip_orange.gif 확인)
        attachments = []
        large_attachment_icons = soup.find_all("img", src=re.compile(r"ico_clip_orange\.gif", re.I))
        for icon in large_attachment_icons:
            # 보통 img 태그 근처의 <a> 태그를 찾음
            # 사용자 예시: <tr> <img ...> <td>&nbsp;</td> <td><a href="...">...</a> ...
            parent_tr = icon.find_parent("tr")
            if parent_tr:
                links = parent_tr.find_all("a")
                for link in links:
                    href = link.get("href", "")
                    if href and not href.startswith("javascript"):
                        name = link.get_text(strip=True)
                        if not name:
                            # 텍스트가 없는 경우 (이미지 링크 등)
                            continue
                        
                        if not any(a["url"] == href for a in attachments):
                            attachments.append({"name": f"[대용량] {name}", "url": href})
                            logger.info(f"대용량 첨부파일 발견: {name}")

        # 3. 첨부파일 추출을 먼저 수행 (기존 0번은 유지하되 1, 2번 위치를 뒤로 밀어 정보를 보존)
        # 3-1. 일반 첨부파일 추출
        logger.debug("일반 첨부파일 추출 시도 (패턴: download|file|attach|zip|pdf|docx|xlsx|pptx)")
        
        # <a> 태그 중 href나 onclick에 키워드가 포함된 것들을 찾음
        potential_links = soup.find_all("a")
        pattern = re.compile(r"download|file|attach|\.(zip|pdf|docx?|xlsx?|pptx?)$", re.I)
        
        for link in potential_links:
            href = link.get("href", "")
            onclick = link.get("onclick", "")
            
            is_attachment = False
            if href and pattern.search(href.split("?")[0]): # 쿼리 스트링 제외하고 확장자 체크
                is_attachment = True
            elif onclick and pattern.search(onclick):
                is_attachment = True
                # onclick에서 URL 추출 시도
                if not href or href.startswith("javascript") or href == "#":
                    url_match = re.search(r"['\"]([^'\"]*(?:download|file|attach|zip|pdf)[^'\"]*)['\"]", onclick, re.I)
                    if url_match:
                        href = url_match.group(1)
                    else:
                        href = f"javascript:{onclick}"

            # 추가: a 태그 내부의 img 태그 src나 alt 등에서 확장자나 키워드가 발견되는 경우 보완
            if not is_attachment:
                img_child = link.find("img")
                if img_child:
                    img_src = img_child.get("src", "")
                    img_alt = img_child.get("alt", "")
                    if pattern.search(img_src) or pattern.search(img_alt):
                        is_attachment = True

            if not is_attachment or not href:
                continue
                
            # 상대 경로 처리
            if href.startswith("./"):
                href = f"{self.base_url}/email/{href[2:]}"
            elif href.startswith("/") and not href.startswith("//"):
                domain_match = re.match(r"(https?://[^/]+)", self.base_url)
                if domain_match:
                    href = f"{domain_match.group(1)}{href}"
            
            # 이름 추출: 텍스트 -> alt -> title -> "첨부파일"
            name = link.get_text(strip=True)
            if not name:
                img_child = link.find("img")
                if img_child:
                    name = img_child.get("alt") or img_child.get("title")
            if not name:
                name = link.get("title", "첨부파일")
            
            # 중복 제거 (URL 기준)
            if not any(a["url"] == href for a in attachments):
                attachments.append({"name": name, "url": href})
                logger.info(f"첨부파일 발견: {name} (URL: {href})")

        # 1. 이미지, 스크립트, 스타일 제거 (첨부파일 추출 후 수행)
        logger.debug("본문 정제 시작 (스크립트/스타일/이미지 제거)")
        for s in soup(["script", "style", "img"]):
            s.decompose()
            
        # 2. 본문 300자 요약
        text = soup.get_text(separator=" ", strip=True)
        summary = text[:300] + ("..." if len(text) > 300 else "")
        logger.debug(f"본문 요약 완료 (약 {len(summary)}자)")
            
        if not attachments:
            if "첨부" in text:
                logger.warning("본문에 '첨부' 단어가 있으나 추출된 첨부파일이 없습니다. HTML 구조 확인이 필요할 수 있습니다.")
                # 분석을 위해 <a> 태그들이나 특정 영역의 HTML 일부를 로그로 남김 (보안 주의)
                # soup.find_all("a")[:5] 등
            else:
                logger.debug("검색된 첨부파일이 없습니다.")

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
