import asyncio
import os
import time
from typing import Optional

from loguru import logger
from playwright.async_api import async_playwright

from app.core.config import LOGIN_INFO

class UserLockPool:
    _locks = {}
    _global_lock = asyncio.Lock()

    @classmethod
    async def get_lock(cls, user_key):
        async with cls._global_lock:
            if user_key not in cls._locks:
                cls._locks[user_key] = asyncio.Lock()
            return cls._locks[user_key]

class ContextPool:
    _pool = {}
    _lock = asyncio.Lock()
    TTL_SECONDS = 600  # 10분
    MAX_POOL = 50

    @classmethod
    async def get_or_create(cls, user_key, browser, cookies=None):
        async with cls._lock:
            session = cls._pool.get(user_key)

            if session:
                context = session["context"]
                try:
                    # 1. 브라우저 세션 자체가 바뀌었는지(재연결) 체크
                    if context.browser != browser:
                        raise Exception("Browser instance changed")

                    # 2. 브라우저 연결 상태 확인
                    if not browser.is_connected():
                        raise Exception("Browser disconnected")

                    # 3. 파이썬 메모리만 믿지 않고, 실제 컨텍스트가 살아있는지 브라우저에 확인
                    _ = context.pages

                    session["last_used"] = time.time()
                    return context
                except Exception as e:
                    # 객체가 죽어있다면 미련 없이 버립니다.
                    logger.warning(f"⚠️ stale(죽은) context 감지됨, 제거합니다: {user_key} ({e})")
                    await cls.remove(user_key)

            if len(cls._pool) >= cls.MAX_POOL:
                oldest_key = min(
                    cls._pool.keys(),
                    key=lambda k: cls._pool[k]["last_used"]
                )

                logger.info(f"♻️ oldest context 제거: {oldest_key}")
                await cls.remove(oldest_key)

            context = await browser.new_context(service_workers="block")

            if cookies:
                await context.add_cookies(cookies)

            cls._pool[user_key] = {
                "context": context,
                "last_used": time.time()
            }

            return context

    @classmethod
    async def remove(cls, user_key):
        session = cls._pool.pop(user_key, None)
        if session:
            try:
                await session["context"].close()
            except:
                pass

    @classmethod
    async def cleanup(cls):
        async with cls._lock:
            now = time.time()
            expired = []

            for key, session in cls._pool.items():
                if now - session["last_used"] > cls.TTL_SECONDS:
                    expired.append(key)

            for key in expired:
                logger.info(f"🧹 expired context 제거: {key}")
                await cls.remove(key)

class _CDPConnectionManager:
    def __init__(self):
        self._lock = asyncio.Lock()
        self.playwright = None
        self.browser = None
        self._reconnecting = False

    async def start(self):
        """서버 시작 시 호출하여 Playwright와 CDP를 미리 연결해둡니다."""
        async with self._lock:
            if self.playwright is None:
                logger.info("🎭 Playwright engine starting...")
                self.playwright = await async_playwright().start()
            
            if self.browser is None or not self.browser.is_connected():
                logger.info("🚀 Pre-connecting to CDP...")
                await self._connect_internal()

    async def _connect_internal(self):
        """내부 연결 로직 (락 없이 호출됨)"""
        cdp_endpoint = os.environ.get("CDP_ENDPOINT", "ws://localhost:3001")
        token = os.environ.get("TOKEN", "")
        if token:
            cdp_endpoint = f"{cdp_endpoint}?token={token}"

        try:
            self.browser = await self.playwright.chromium.connect_over_cdp(
                cdp_endpoint,
                timeout=20000  # 20초로 단축
            )
            self.browser.on(
                "disconnected",
                lambda _: asyncio.create_task(self._handle_disconnect())
            )
            logger.info("✅ CDP connected")
        except Exception as e:
            logger.error(f"❌ CDP 연결 실패: {e}")
            self.browser = None
            raise

    async def _handle_disconnect(self):
        logger.warning("⚠️ CDP disconnected")
        self.browser = None
        # 연결이 끊어지면 즉시 백그라운드 재연결 시도
        if not self._reconnecting:
            asyncio.create_task(self.reconnect_background())

    async def reconnect_background(self):
        """백그라운드에서 연결을 복구합니다."""
        if self._reconnecting:
            return
        
        self._reconnecting = True
        logger.info("♻️ Background CDP reconnection started...")
        
        try:
            async with self._lock:
                if self.browser and self.browser.is_connected():
                    return
                
                for attempt in range(5):
                    try:
                        await self._connect_internal()
                        break
                    except Exception:
                        wait_time = min(2 ** attempt, 30) # 지수 백오프
                        await asyncio.sleep(wait_time)
        finally:
            self._reconnecting = False

    async def get_browser(self):
        async with self._lock:
            # 1. 기존 브라우저가 있고 연결된 것처럼 보인다면 Health Check
            if self.browser:
                try:
                    if self.browser.is_connected():
                        # 실제 통신이 가능한지 확인 (timeout을 짧게 주어 체크)
                        await asyncio.wait_for(self.browser.version(), timeout=2.0)
                        return self.browser
                except Exception as e:
                    logger.warning(f"⚠️ 브라우저 Health Check 실패 (재연결 필요): {e}")
                    try:
                        await self.browser.close()
                    except:
                        pass
                    self.browser = None

            # 2. 연결이 없거나 실패한 경우 새로 연결
            logger.info("🚀 CDP connecting (on-demand or recovery)...")
            if self.playwright is None:
                self.playwright = await async_playwright().start()

            await self._connect_internal()
            return self.browser

# 전역 싱글톤 인스턴스 생성
cdp_manager = _CDPConnectionManager()

# 동시에 실행될 수 있는 최대 크롤러 수를 10개로 제한
MAX_CONCURRENT_CRAWLERS = 10
crawler_semaphore = asyncio.Semaphore(MAX_CONCURRENT_CRAWLERS)

class BaseCrawler:
    def __init__(self,
                 cookies: list = None,
                 user_id: Optional[str] = None,
                 password: Optional[str] = None):
        self.username = user_id
        self.password = password
        self.cookies = cookies or []  # 외부에서 전달받은 세션 쿠키

        self.browser = None
        self.context = None
        self.page = None

        self.user_key = user_id or "anonymous"
        self.user_lock = None
    async def __aenter__(self):
        await crawler_semaphore.acquire()
        max_retries = 2
        for attempt in range(max_retries):
            try:
                logger.debug(f"🚦 세마포어 획득 (시도 {attempt + 1}/{max_retries})")
                await ContextPool.cleanup()

                logger.debug(f"1. 유저 락 획득 시도: {self.user_key}")
                self.user_lock = await UserLockPool.get_lock(self.user_key)
                await self.user_lock.acquire()

                logger.debug("2. 브라우저 연결 시도")
                self.browser = await cdp_manager.get_browser()

                logger.debug("3. 컨텍스트 생성/조회 시도")
                self.context = await ContextPool.get_or_create(
                    self.user_key,
                    self.browser,
                    self.cookies
                )

                logger.debug("4. 새 페이지 생성 시도")
                self.page = await self.context.new_page()

                logger.debug("5. 모든 준비 완료")
                return self

            except Exception as e:
                logger.warning(f"⚠️ Crawler 진입 실패 (시도 {attempt + 1}): {e}")
                
                # 락 해제
                if self.user_lock and self.user_lock.locked():
                    self.user_lock.release()
                
                # 닫힌 페이지/브라우저 관련 에러라면 풀에서 제거하여 다음 시도 때 새로 만들게 함
                if "closed" in str(e).lower() or "disconnected" in str(e).lower():
                    await ContextPool.remove(self.user_key)
                
                if attempt == max_retries - 1:
                    # 마지막 시도 실패 시 자원 반납 후 에러 발생
                    crawler_semaphore.release()
                    raise
                
                await asyncio.sleep(1) # 잠시 대기 후 재시도
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
            # if self.cookies:
            #     await self.context.add_cookies(self.cookies)
            #     logger.info("기존 세션 쿠키를 주입했습니다.")

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        try:
            # 페이지 종료 (Context는 Pool이 관리하므로 닫지 않음)
            if self.page:
                await asyncio.wait_for(self.page.close(), timeout=2.0)
        except Exception as e:
            logger.warning("[WARNING] 컨텍스트 자원 정리 타임아웃")
            logger.debug(f"Page close error: {e}")
            pass
        finally:
            # 락 해제 순서: Lock -> Semaphore
            if self.user_lock and self.user_lock.locked():
                self.user_lock.release()

            crawler_semaphore.release()
            logger.debug(f"🚦 자원 반납 완료 ({self.user_key})")

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
            return False, [], {"error": "Credentials missing"}

        try:
            logger.debug("페이지 이동 시작")
            await self.page.goto(LOGIN_INFO["domain"], timeout=20000, wait_until="domcontentloaded")
            logger.debug(f"프레임 대기: {frame_name}")
            frame = await self.wait_for_frame(frame_name, timeout=10)
            if not frame:
                logger.error("[ERROR] 프레임을 찾을 수 없습니다.")
                return False, [], {"error": "Frame not found"}

            # 1. Alert 메시지를 담을 Future 객체 생성
            alert_future = asyncio.get_event_loop().create_future()

            async def handle_dialog(dialog):
                # 어떤 Alert이든 메시지를 Future에 기록하고 닫음
                if not alert_future.done():
                    alert_future.set_result(dialog.message)
                asyncio.create_task(dialog.dismiss())

            # 2. 리스너 등록
            self.page.on("dialog", handle_dialog)

            # 3. 로그인 시도
            await frame.fill('input[name="UserID"]', self.username)
            await frame.fill('input[name="UserPass"]', self.password)
            await frame.click('button[type="button"].ibtn')

            # 코루틴을 Task로 변환하여 등록
            # wait_for를 호출한 상태의 코루틴을 Task로 감싸야 합니다.
            success_task = asyncio.create_task(
                frame.locator('img[src*="btn_logout"]').wait_for(state="visible", timeout=10000)
            )

            try:
                # 4. Alert 발생 또는 로그인 성공(로그아웃 버튼 출현) 중 먼저 일어나는 것을 대기
                # 7초 동안 Alert이 뜨는지 감시 (보통 클릭 직후 바로 뜸)
                done, pending = await asyncio.wait(
                    [alert_future, success_task],
                    return_when=asyncio.FIRST_COMPLETED,
                    timeout=8.0
                )

                # 1. 미완료된 태스크만 취소
                for task in pending:
                    if not task.done():
                        task.cancel()

                # 2. Alert 결과 확인 (CancelledError 방지)
                if alert_future.done() and not alert_future.cancelled():
                    try:
                        error_msg = alert_future.result()
                        logger.error(f"[로그인 실패 - Alert 발생] {error_msg}")
                        return False, [], {"error": error_msg}
                    except asyncio.CancelledError:
                        # 혹시나 그 사이 취소되었다면 무시
                        pass

                if success_task.done() and not success_task.cancelled():
                    # 로그인이 성공한 경우
                    logger.info("[INFO] 로그인 성공 및 세션 발급 완료")
                # 2. 이름 및 부서 정보 추출 (HTML 구조 기반)
                user_info = {"username": "", "dept": "", "userId": self.username}
                try:
                    # h6 컨텐트 정확한 이름+직급 추출 (예: "문병찬 대리")
                    name_locator = frame.locator("h6#loginUserName")
                    user_info["username"] = (await name_locator.text_content()).strip()

                    # h6 바로 다음에 오는 p 태그에서 부서명 추출 (예: "DX 2Team")
                    dept_locator = frame.locator("h6#loginUserName + p")
                    user_info["dept"] = (await dept_locator.text_content()).strip()

                    logger.info(f"[INFO] 사용자 정보 추출 성공: {user_info['username']} / {user_info['dept']}")

                    # 4. 세션 쿠키 추출
                    cookies = await self.context.cookies()
                except Exception as e:
                    logger.error(f"데이터 추출 중 에러 발생: {e}")
                    return False, [], {}

                # 성공, 쿠키리스트, 토큰, 유저정보(dict) 반환
                return True, cookies, user_info

            except Exception as e:
                # 5. Alert도 없고 버튼도 안 나온 경우 (타임아웃 등)
                if alert_future.done():
                    return False, [], {"error": alert_future.result()}
                logger.error(f"[ERROR] 로그인 프로세스 중 에러: {e}")
                return False, [], {}

            finally:
                # 대기 중인 다른 작업들 정리 (메모리 누수 방지)
                if not success_task.done():
                    success_task.cancel()
                self.page.remove_listener("dialog", handle_dialog)
        except Exception as e:
            logger.error(f"[ERROR] 로그인 페이지 접근 실패: {e}")
            return False, [], {}

    async def recover_doc_write_page(self, keyword: str = "Doc_Write", timeout: float = 5.0):
        start_time = time.time()
        while time.time() - start_time < timeout:
            for p in reversed(self.context.pages):
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