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
    async def fetch_recent_posts(self, cookies: list = None):
        """
        /Main/iFrame/BbsNewList 페이지에서 최근 게시물 중 읽지 않은 게시물을 가져옵니다.
        """
        if cookies:
            self.cookies = cookies
            # 데코레이터에서 주입된 쿠키를 브라우저 컨텍스트에 적용
            if self.context:
                await self.context.add_cookies(self.cookies)
                logger.info("데코레이터로부터 받은 세션 쿠키를 적용했습니다.")

        if not self.cookies:
            logger.warning("유효한 세션(쿠키)이 없습니다.")
            return {"status": "fail", "message": "세션이 만료되었습니다.", "code": "SESSION_EXPIRED"}

        try:
            url = f"{LOGIN_INFO['domain']}/Main/iFrame/BbsNewList"
            logger.info(f"최근 게시물 페이지 이동 시도: {url}")
            
            await self.page.goto(url, wait_until="networkidle")
            content_html = await self.page.content()
            logger.debug(f"최근 게시물 HTML 길이: {len(content_html)}")
            
            # 로그인 여부 확인
            is_login_page = ("ZioYou Login" in content_html) or ("UserPass" in content_html)
            if is_login_page:
                logger.warning(f"최근 게시물: 세션이 만료되어 로그인 페이지로 리다이렉트되었습니다. (판정: {'ZioYou Login' if 'ZioYou Login' in content_html else 'UserPass'})")
                return {"status": "fail", "message": "세션이 만료되었습니다.", "code": "SESSION_EXPIRED"}
            
            soup = BeautifulSoup(content_html, "html.parser")
            
            # 모든 a 태그를 먼저 찾아서 로그 기록
            all_links = soup.find_all('a')
            logger.debug(f"전체 a 태그 개수: {len(all_links)}")
            
            # HTML 내용의 일부를 로그로 출력 (디버깅용)
            logger.debug(f"최근 게시물 HTML 앞부분: {content_html[:500]}")

            # class가 font-weight-bold인 a 태그 추출 (공백 포함 여부와 상관없이)
            post_links = soup.find_all('a', class_=re.compile(r'font-weight-bold', re.I))
            logger.info(f"읽지 않은 게시물(font-weight-bold) 후보 개수: {len(post_links)}")
            
            results = []
            for a in post_links:
                href = a.get('href', '')
                logger.debug(f"게시물 후보 추출: text='{a.get_text(strip=True)}', href='{href}'")
                
                if not href or href.startswith('javascript'):
                    continue
                
                href = href.replace('&amp;', '&')
                full_url = f"{LOGIN_INFO['domain']}{href}"
                title = a.get_text(strip=True)
                
                parent_td = a.find_parent('td')
                date_text = ""
                if parent_td:
                    next_td = parent_td.find_next_sibling('td')
                    if next_td:
                        date_text = next_td.get_text(strip=True)
                
                logger.debug(f"추출 성공: {title} ({date_text})")
                results.append({
                    "title": title,
                    "url": full_url,
                    "date": date_text
                })

            logger.success(f"최근 게시물 {len(results)}건 추출 성공")
            return {
                "status": "success",
                "data": results
            }

        except Exception as e:
            logger.error(f"[ERROR] 최근 게시물 크롤링 실패: {e}")
            return {"status": "error", "message": str(e)}

    @requires_system_session
    async def fetch_component_posts(self, com_seq: str, cookies: list = None):
        """
        /Main/DefaultComponentiFrame?ComSeq={com_seq} 페이지에서 읽지 않은 게시물을 가져옵니다.
        """
        if cookies:
            self.cookies = cookies
            # 데코레이터에서 주입된 쿠키를 브라우저 컨텍스트에 적용
            if self.context:
                await self.context.add_cookies(self.cookies)
                logger.info(f"컴포넌트({com_seq}): 데코레이터로부터 받은 세션 쿠키를 적용했습니다.")

        if not self.cookies:
            logger.warning("유효한 세션(쿠키)이 없습니다.")
            return {"status": "fail", "message": "세션이 만료되었습니다.", "code": "SESSION_EXPIRED"}

        try:
            url = f"{LOGIN_INFO['domain']}/Main/DefaultComponentiFrame?ComSeq={com_seq}"
            logger.info(f"컴포넌트 게시판({com_seq}) 페이지 이동 시도: {url}")
            
            # networkidle 대기 시간 증가 및 명시적 대기
            await self.page.goto(url, wait_until="networkidle", timeout=30000)
            await asyncio.sleep(2) # JS 렌더링 추가 대기
            
            content_html = await self.page.content()
            logger.debug(f"컴포넌트({com_seq}) HTML 길이: {len(content_html)}")
            
            # 로그인 여부 확인
            is_login_page = ("ZioYou Login" in content_html) or ("UserPass" in content_html)
            if is_login_page:
                logger.warning(f"컴포넌트({com_seq}): 세션이 만료되어 로그인 페이지로 리다이렉트되었습니다. (판정: {'ZioYou Login' if 'ZioYou Login' in content_html else 'UserPass'})")
                return []
            
            # HTML 내용의 일부를 로그로 출력 (디버깅용)
            logger.debug(f"컴포넌트({com_seq}) HTML 앞부분: {content_html[:500]}")
            
            soup = BeautifulSoup(content_html, "html.parser")
            
            # span 태그 중 onclick에 document.location.href가 있는 것 모두 탐색 (대소문자 및 공백 유연하게)
            all_spans = soup.find_all('span', onclick=re.compile(r'document\.location\.href|location\.href', re.I))
            logger.debug(f"컴포넌트({com_seq}) 내 클릭 가능한 span 개수: {len(all_spans)}")
            
            # span이 없으면 a 태그나 다른 요소도 확인 (혹시 구조가 다를 경우 대비)
            if not all_spans:
                logger.debug(f"컴포넌트({com_seq}) span을 찾지 못해 전체 onclick 속성 조사")
                all_onclick_elements = soup.find_all(True, onclick=re.compile(r'location\.href', re.I))
                logger.debug(f"컴포넌트({com_seq}) onclick 속성 가진 전체 요소 개수: {len(all_onclick_elements)}")
                all_spans = all_onclick_elements # 일단 후보로 등록
            
            results = []
            for span in all_spans:
                # b 태그가 있는지 확인 (읽지 않은 글)
                b_tag = span.find('b')
                
                # 로그를 위해 텍스트 추출
                span_text = span.get_text(strip=True)
                has_b = b_tag is not None
                logger.debug(f"span 확인: text='{span_text}', has_b={has_b}")

                if not b_tag:
                    continue
                
                onclick = span.get('onclick', '')
                url_match = re.search(r"document\.location\.href=['\"]([^'\"]*)['\"]", onclick)
                if not url_match:
                    logger.debug(f"URL 매칭 실패: onclick='{onclick}'")
                    continue
                
                href = url_match.group(1).replace('&amp;', '&')
                full_url = f"{LOGIN_INFO['domain']}{href}"
                title = b_tag.get_text(strip=True)
                
                # 날짜 추출 (span이 포함된 td의 다음 td에 날짜가 있음)
                parent_td = span.find_parent('td')
                date_text = ""
                if parent_td:
                    next_td = parent_td.find_next_sibling('td')
                    if next_td:
                        date_text = next_td.get_text(strip=True)

                logger.debug(f"추출 성공: {title} ({date_text})")
                results.append({
                    "title": title,
                    "url": full_url,
                    "date": date_text
                })

            logger.success(f"컴포넌트({com_seq}) 게시물 {len(results)}건 추출 성공")
            return results

        except Exception as e:
            logger.error(f"[ERROR] 컴포넌트({com_seq}) 크롤링 실패: {e}")
            return []

    @requires_system_session
    async def fetch_birthdays(self, cookies: list = None):
        """
        /Etc/Birthday 페이지에서 해당 달의 생일자 목록을 가져옵니다.
        """
        if cookies:
            self.cookies = cookies
            # 데코레이터에서 주입된 쿠키를 브라우저 컨텍스트에 적용
            if self.context:
                await self.context.add_cookies(self.cookies)
                logger.info("생일자: 데코레이터로부터 받은 세션 쿠키를 적용했습니다.")

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
            
            # HTML 내용의 일부를 로그로 출력 (디버깅용)
            logger.debug(f"초기 페이지 HTML 앞부분: {content_html[:500]}")
            
            is_login_page = ("ZioYou Login" in content_html) or ("UserPass" in content_html) or (len(content_html) < 2000)
            if is_login_page:
                reason = "길이 부족" if len(content_html) < 2000 else ("ZioYou Login" if "ZioYou Login" in content_html else "UserPass")
                logger.warning(f"생일자: 세션이 만료되었거나 로그인 페이지입니다. (판정: {reason})")
                return {"status": "fail", "message": "세션이 만료되었습니다.", "code": "SESSION_EXPIRED"}

            # 3. iframe 구조 내에서 페이지가 로드되었는지 확인
            # 그룹웨어 특성상 goMainUrl() 스크립트를 사용하여 mainFrame의 src를 변경하는 것이 안전함
            await self.page.evaluate(f"if(window.goMainUrl) goMainUrl('{birthday_url}');")
            await asyncio.sleep(2) # 로딩 대기
            
            # 4. 데이터 추출 (mainFrame 우선 탐색)
            content_html = await self.page.content()

            # logger.debug(f"대상 HTML 길이: {len(content_html)} 자")
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