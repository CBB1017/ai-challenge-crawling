import asyncio
import os

from loguru import logger
from playwright.async_api import async_playwright


class BaseCrawler:
    def __init__(self, login_url: str, username: str, password: str, cookies: list = None):
        self.login_url = login_url
        self.username = username
        self.password = password
        self.cookies = cookies or []  # 외부에서 전달받은 세션 쿠키

        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None

    async def __aenter__(self):
        self.playwright = await async_playwright().start()
        cdp_endpoint = os.environ.get("CDP_ENDPOINT", f"ws://localhost:3001?token={os.environ.get('TOKEN', '')}")

        for attempt in range(3):
            try:
                self.browser = await self.playwright.chromium.connect_over_cdp(cdp_endpoint, timeout=10000)
                break
            except Exception as e:
                if attempt == 2:
                    logger.error(f"CDP 연결 최종 실패: {e}")
                    await self.__aexit__(None, None, None)
                    raise
                await asyncio.sleep(1)

        self.context = await self.browser.new_context()

        # 💡 핵심: 전달받은 쿠키가 있다면 컨텍스트에 주입 (로그인 상태 복원)
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
            if self.browser: await self.browser.close()
            if self.playwright: await self.playwright.stop()

        try:
            await asyncio.wait_for(_cleanup(), timeout=5.0)
        except asyncio.TimeoutError:
            logger.warning("[WARNING] 브라우저 자원 정리 타임아웃")

    async def wait_for_frame(self, name, timeout=10):
        interval = 0.2
        for _ in range(int(timeout / interval)):
            frame = self.page.frame(name=name)
            if frame: return frame
            await asyncio.sleep(interval)
        return None

    async def login(self, frame_name="mainFrame"):
        try:
            await self.page.goto(self.login_url)
            frame = await self.wait_for_frame(frame_name)
            if not frame:
                logger.error("[ERROR] 프레임을 찾을 수 없습니다.")
                return False, []

            await frame.fill('input[name="UserID"]', self.username)
            await frame.fill('input[name="UserPass"]', self.password)
            await frame.click('button[type="button"].ibtn')

            try:
                await frame.wait_for_selector('img[src*="btn_logout.gif"]', timeout=5000)
                logger.info("[INFO] 로그인 성공")

                # 로그인 성공 후 세션 쿠키 추출
                cookies = await self.context.cookies()
                return True, cookies

            except Exception:
                logger.error("[ERROR] 로그인 실패 (로그아웃 버튼 없음)")
                return False, []
        except Exception as e:
            logger.error(f"[ERROR] 로그인 실패: {e}")
            return False, []