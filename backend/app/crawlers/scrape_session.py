# backend/app/crawlers/scrape_session.py
from typing import Dict
import aiohttp
import asyncio
import certifi
import ssl
import os

from .site_profiles import base_url_map, headers_map, cookies_map, cmd_map

class SiteSessionManager:
    """ 管理各個HSD的 aiohttp.ClientSession，確保每個HSD只創建一個 Session（Singleton 模式）。 """
    
    def __init__(self):
        self._sessions: Dict[str, aiohttp.ClientSession] = {}  # 存放 hsd_name -> aiohttp.ClientSession
        self._lock = asyncio.Lock()

    async def get_session(self, hsd_name: str) -> aiohttp.ClientSession:
        """ 取得指定HSD的 `ClientSession`，如果不存在則創建一個 """
        async with self._lock:  # 確保只有一個協程能同時進入
            session = self._sessions.get(hsd_name)
            if not session or session.closed:
                self._sessions[hsd_name] = await self._create_session(hsd_name)
            return self._sessions[hsd_name]

    async def _create_session(self, hsd_name: str) -> aiohttp.ClientSession:
        """ 根據HSD需求創建不同的 session """

        # 加上正確的 SSL context，避免系統 CA 過舊
        ssl_ctx = ssl.create_default_context(cafile=certifi.where())
        connector = aiohttp.TCPConnector(ssl=ssl_ctx)

        # if hsd_name not in base_url_map:
        #     raise ValueError(f"❌ 錯誤：未支援的HSD `{hsd_name}`")

        # 需要透過 `requests` 獲取 cookies
        if hsd_name in {"Mouser", "Future", "Sager"}:
            base_url = base_url_map[hsd_name]

            # session_tmp = requests.Session()
            # session_tmp.headers.update(headers_map.get(hsd_name, {}))

            # # 確保 cookies_map 存在才更新
            # if hsd_name in cookies_map:
            #     session_tmp.cookies.update(cookies_map[hsd_name])

            # # 將同步 HTTP 呼叫丟到 thread pool，避免阻塞 event loop
            # # 讓 requests 執行一次請求以獲取 Set-Cookie
            # await asyncio.to_thread(session_tmp.get, base_url)

            # # 建立 aiohttp 的 cookie_jar，並同步 requests 的 cookies
            # jar = aiohttp.CookieJar(unsafe=True)
            # for key, value in session_tmp.cookies.items():
            #     jar.update_cookies({key: value})

            # # 創建 session
            # return aiohttp.ClientSession(
            #     headers=headers_map.get(hsd_name, {}),
            #     cookie_jar=jar,
            #     timeout=aiohttp.ClientTimeout(15),
            #     connector=connector,
            # )


            jar = aiohttp.CookieJar(unsafe=True)
            # 確保 cookies_map 存在才更新
            if hsd_name in cookies_map:
                for key, value in cookies_map[hsd_name].items():
                    jar.update_cookies({key: value})

            tmp_timeout = aiohttp.ClientTimeout(total=15, connect=5, sock_read=10)
            session = aiohttp.ClientSession(
                headers=headers_map.get(hsd_name, {}),
                timeout=tmp_timeout,
                connector=connector,
                cookie_jar=jar,
            )
            # await session.get(base_url, allow_redirects=True)
            async with session.get(base_url, allow_redirects=True) as resp:
                await resp.release()

            return session


        # 需要執行 `curl` 來獲取 cookies
        elif hsd_name in {"DigiKey", "Avnet"}:
            cookies_path = f"tmp/{hsd_name}_cookies.txt"

            # 檢查暫存 cookies.txt
            if not os.path.isfile(cookies_path):
                 # 確保tmp資料夾存在
                os.makedirs("tmp", exist_ok=True)

                # 執行 curl
                proc = await asyncio.create_subprocess_exec(
                    *cmd_map[hsd_name],
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await proc.communicate()

                if proc.returncode != 0:
                    raise RuntimeError(f"❌ `curl` 指令執行失敗，HSD: {hsd_name}")

            # 解析 cookies.txt
            cookies = {}
            with open(cookies_path, "r", encoding="utf-8") as f:
                for line in f:
                    if not line.startswith("#") and line.strip():
                        parts = line.split("\t")
                        if len(parts) >= 7:
                            cookies[parts[5]] = parts[6].strip()

            # 建立 aiohttp 的 cookie_jar，並同步 requests 的 cookies
            jar = aiohttp.CookieJar(unsafe=True)
            for key, value in cookies.items():
                jar.update_cookies({key: value})

            # 創建 session
            return aiohttp.ClientSession(
                headers=headers_map.get(hsd_name, {}),
                cookie_jar=jar,
                connector=connector,
                timeout=aiohttp.ClientTimeout(10),
            )
        
        # Arrow 反爬蟲策略太嚴格，這個session只能用來載靜態資源，無法用來爬蟲
        # RS 似乎沒有反爬蟲機制，因此用最原始的session去請求即可
        elif hsd_name in {"Arrow", "RS", "Farnell"}:
            return aiohttp.ClientSession(
                headers=headers_map.get(hsd_name, {}),
                connector=connector,
                timeout=aiohttp.ClientTimeout(15),
            )
        
        else:
            raise ValueError(f'hsd_name = {hsd_name} is not in [Mouser, Future, Sager, DigiKey, Avnet, Arrow, RS, Farnell]')

    async def close_session(self, hsd_name: str):
        async with self._lock:
            s = self._sessions.pop(hsd_name, None)
            if s:
                await s.close()

    async def close_all_sessions(self):
        """ 關閉所有 `ClientSession`，釋放資源 """
        async with self._lock:
            for session in self._sessions.values():
                await session.close()
            self._sessions.clear()

aiohttp_hsd_session_manager = SiteSessionManager()
