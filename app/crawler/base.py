import asyncio
import os
from typing import Optional

from loguru import logger
from playwright.async_api import async_playwright

from app.core.config import LOGIN_INFO

# ---------------------------------------------------------
# 1. 전역(Global) CDP 커넥션 관리자
# ---------------------------------------------------------
_global_playwright = None
_global_browser = None
_cdp_lock = asyncio.Lock()


async def get_shared_cdp_browser():
    """서버 기동 시 (또는 최초 호출 시) 단 한 번만 CDP 웹소켓을 연결합니다."""
    global _global_playwright, _global_browser

    async with _cdp_lock:
        # 브라우저 커넥션이 없거나 끊어졌다면 재연결
        if _global_browser is None or not _global_browser.is_connected():
            logger.info("🚀 원격 CDP 브라우저에 연결을 시도합니다...")

            if _global_playwright is None:
                _global_playwright = await async_playwright().start()

            cdp_endpoint = os.environ.get("CDP_ENDPOINT", f"ws://localhost:3001?token={os.environ.get('TOKEN', '')}")

            for attempt in range(3):
                try:
                    _global_browser = await _global_playwright.chromium.connect_over_cdp(cdp_endpoint, timeout=10000)
                    logger.info("✅ CDP 웹소켓 연결 성공!")
                    break
                except Exception as e:
                    if attempt == 2:
                        logger.error(f"CDP 연결 최종 실패: {e}")
                        raise
                    await asyncio.sleep(1)

    return _global_browser

class BaseCrawler:
    def __init__(self,
                 cookies: list = None,
                 username: Optional[str] = None,
                 password: Optional[str] = None):
        self.username = username
        self.password = password
        self.cookies = cookies or []  # 외부에서 전달받은 세션 쿠키

        self.browser = None
        self.context = None
        self.page = None

    async def __aenter__(self):
        # self.playwright = await async_playwright().start()
        # 1. 매번 connect_over_cdp를 하지 않고, 살아있는 글로벌 커넥션을 가져옵니다.
        self.browser = await get_shared_cdp_browser()

        # 2. 이 요청만을 위한 시크릿 창(컨텍스트) 생성
        self.context = await self.browser.new_context(service_workers='allow')
        #1. 원격 CDP가 아닌 로컬 브라우저를 강제로 띄웁니다.
        # self.browser = await self.playwright.chromium.launch(
        #     headless=False,  # 브라우저 숨김 해제
        #     slow_mo=1000,  # 마우스/키보드 동작마다 1초씩 대기 (엄청 천천히 움직임)
        #     channel="chrome",  # PC에 설치된 실제 크롬 브라우저 사용 (호환성 좋음)
        #     args=["--start-maximized"]  # 창을 최대화해서 띄움
        # )
        #
        # # 2. 창 최대화 유지를 위해 no_viewport 적용
        # self.context = await self.browser.new_context(no_viewport=True)

        # 전달받은 쿠키가 있다면 컨텍스트에 주입 (로그인 상태 복원)
        if self.cookies:
            await self.context.add_cookies(self.cookies)
            logger.info("기존 세션 쿠키를 주입했습니다.")

        self.page = await self.context.new_page()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        # 자원 해제 타임아웃 적용
        async def _cleanup():
            if self.page: await self.page.close()
            if self.context: await self.context.close()
            # if self.browser: await self.browser.close()
            # if self.playwright: await self.playwright.stop()

        try:
            await asyncio.wait_for(_cleanup(), timeout=5.0)
        except asyncio.TimeoutError:
            logger.warning("[WARNING] 컨텍스트 자원 정리 타임아웃")

    async def wait_for_frame(self, name, timeout=10):
        interval = 0.2
        for _ in range(int(timeout / interval)):
            frame = self.page.frame(name=name)
            if frame: return frame
            await asyncio.sleep(interval)
        return None

    async def login(self, frame_name="mainFrame"):
        """최초 로그인 전용 메서드 (프론트엔드에서 로그인 API 호출 시에만 사용)"""
        if not self.username or not self.password:
            logger.error("[ERROR] 로그인 정보가 제공되지 않았습니다.")
            return False, [], "", {}

        try:
            await self.page.goto(LOGIN_INFO["domain"])
            frame = await self.wait_for_frame(frame_name)
            if not frame:
                logger.error("[ERROR] 프레임을 찾을 수 없습니다.")
                return False, [], "", {}

            await frame.fill('input[name="UserID"]', self.username)
            await frame.fill('input[name="UserPass"]', self.password)
            await frame.click('button[type="button"].ibtn')

            try:
                # 1. 로그인 성공 여부 확인
                await frame.wait_for_selector('img[src*="btn_logout.gif"]', timeout=5000)
                logger.info("[INFO] 로그인 성공 및 세션 발급 완료")

                # 2. 이름 및 부서 정보 추출 (HTML 구조 기반)
                user_info = {"username": "", "dept": ""}
                try:
                    # h6 컨텐트 정확한 이름+직급 추출 (예: "문병찬 대리")
                    name_locator = frame.locator("h6#loginUserName")
                    user_info["username"] = (await name_locator.text_content()).strip()

                    # h6 바로 다음에 오는 p 태그에서 부서명 추출 (예: "DX 2Team")
                    dept_locator = frame.locator("h6#loginUserName + p")
                    user_info["dept"] = (await dept_locator.text_content()).strip()

                    logger.info(f"[INFO] 사용자 정보 추출 성공: {user_info['username']} / {user_info['dept']}")

                    access_token = await frame.evaluate("""() => {
                        return new Promise((resolve) => {
                            const token = localStorage.getItem('accessToken');
                            if (token) return resolve(token);

                            // 0.5초마다 확인하는 인터벌
                            const interval = setInterval(() => {
                                const t = localStorage.getItem('accessToken');
                                if (t) {
                                    clearInterval(interval);
                                    resolve(t);
                                }
                            }, 500);
                            setTimeout(() => { clearInterval(interval); resolve(null); }, 5000);
                        });
                    }""")

                    # 3. 만약 'accessToken'이라는 키가 아니라 다른 키라면 확인 필요
                    if not access_token:
                        logger.error("localStorage에서 accessToken을 찾을 수 없습니다.")
                        return False, [], "", {}

                    # 4. 세션 쿠키 추출
                    cookies = await self.context.cookies()
                except Exception as e:
                    logger.error(f"데이터 추출 중 에러 발생: {e}")
                    return False, [], "", {}

                # 성공, 쿠키리스트, 토큰, 유저정보(dict) 반환
                return True, cookies, access_token, user_info

            except Exception:
                logger.error("[ERROR] 로그인 실패 (로그아웃 버튼 없음)")
                return False, [], "", {}
        except Exception as e:
            logger.error(f"[ERROR] 로그인 페이지 접근 실패: {e}")
            return False, [], "", {}

    async def recover_doc_write_page(self, keyword: str = "Doc_Write", timeout: float = 5.0):
        import time

        start_time = time.time()
        while time.time() - start_time < timeout:
            # context 내의 모든 페이지를 역순으로 검사 (보통 최신 페이지가 뒤에 있음)
            pages = self.context.pages
            for p in reversed(pages):
                logger.info(f"Page : {p}")

                try:
                    if p.is_closed():
                        continue

                    # URL뿐만 아니라 로드 상태도 확인
                    current_url = p.url
                    if keyword in current_url:
                        # 페이지를 활성화하고 self.page 업데이트
                        self.page = p
                        await p.bring_to_front()
                        logger.info(f"Page 복구 완료: {current_url}")
                        return p
                except Exception as e:
                    # 페이지가 검사 도중 닫히는 경우 대비
                    continue

            await asyncio.sleep(0.5)

        raise Exception(f"'{keyword}' 패턴의 페이지를 찾을 수 없습니다. (현재 페이지 수: {len(self.context.pages)})")