import os

from dotenv import load_dotenv
from playwright.async_api import async_playwright
import asyncio
from loguru import logger

load_dotenv()


class BaseCrawler:
    def __init__(self, login_info: dict):
        self.login_url = login_info.get("login_url")
        self.username = login_info.get("username")
        self.password = login_info.get("password")
        self.playwright = None
        self.browser = None
        self.page = None

    async def setup_driver(self):
        self.playwright = await async_playwright().start()
        # chromium을 headless로 실행
        cdp_endpoint = os.environ.get("CDP_ENDPOINT", "ws://localhost:3001?token=" + os.environ["TOKEN"])
        try:
            self.browser = await self.playwright.chromium.connect_over_cdp(cdp_endpoint)
            logger.debug(f"CDP 연결 성공: {cdp_endpoint}")
        except Exception as e:
            logger.error(f"CDP 연결 실패: {e}")
            raise
        self.page = await self.browser.contexts[0].new_page()

    async def wait_for_frame(self, name, timeout=10):
        interval = 0.2
        tries = int(timeout / interval)
        for _ in range(tries):
            # frame 에는 await 금지
            frame = self.page.frame(name=name)
            if frame:
                return frame
            await asyncio.sleep(interval)
        return None

    async def login(self, frame_name="mainFrame"):
        try:
            await self.page.goto(self.login_url)
            frame = await self.wait_for_frame(frame_name)  # 수정: self.page X, frame_name만!
            if not frame:
                logger.error("[ERROR] 프레임을 찾을 수 없습니다.")
                return False
            await frame.fill('input[name="UserID"]', self.username)
            await frame.fill('input[name="UserPass"]', self.password)
            await frame.click('button[type="button"].ibtn')
            try:
                await frame.wait_for_selector('img[src*="btn_logout.gif"]', timeout=5000)
                logger.info("[INFO] 로그인 성공")
                return True
            except Exception:
                logger.error("[ERROR] 로그인 실패 (로그아웃 버튼 없음)")
                return False
        except Exception as e:
            logger.error(f"[ERROR] 로그인 실패: {e}")
            return False

    async def close(self):
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()
