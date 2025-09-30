# backend/app/services/downloader_worker.py
from urllib.parse import unquote, urlparse, parse_qs
from datetime import datetime, timezone
from urllib.parse import urlparse
from typing import Optional
import traceback
import posixpath
import asyncio
import aiohttp
import hashlib
import time
import re
import io

from sqlalchemy.orm import Session
from ..db import SessionLocal
from ..models import DownloadTask
from .file_store import persist_bytes_to_store
from ..crawlers.scrape_session import aiohttp_hsd_session_manager

_FN_TOKEN = r"[^;]+"
_QUOTED_FN_RE = re.compile(r'filename="((?:\\.|[^"\\])*)"')
_UNQUOTED_FN_RE = re.compile(r'filename=([^\s;]+)')
_FNSTAR_RE = re.compile(r"filename\*\s*=\s*([^']*)'([^']*)'(.+)")  # charset'lang'value

def _sanitize_filename(name: str, default_ext: str | None = None) -> str:
    # 去掉路徑分隔與控制字元，避免 traversal
    name = name.replace("\\", "/")
    name = posixpath.basename(name)
    name = name.strip().strip(".")  # 去掉前後點與空白
    # 避免空名
    if not name:
        name = "file"
    # 長度限制（自行調整政策）
    if len(name) > 180:
        base, dot, ext = name.rpartition(".")
        if dot:
            base = base[:160]
            name = f"{base}.{ext}"
        else:
            name = name[:180]
    # 若需要補副檔名
    if default_ext and "." not in posixpath.basename(name):
        name = f"{name}{default_ext}"
    return name

def _extract_filename_from_content_disposition(cd: str) -> str | None:
    if not cd:
        return None

    # 1) RFC 5987 / 6266：filename*=
    m = _FNSTAR_RE.search(cd)
    if m:
        charset, _lang, value = m.groups()
        try:
            # value 是 percent-encoded
            value_pct = unquote(value)
            if charset:
                try:
                    # 嘗試依 charset decode
                    value_pct = value_pct.encode("latin-1", "ignore").decode(charset, "ignore")
                except Exception:
                    # 解不動就維持 percent-decoded 結果
                    pass
            return value_pct
        except Exception:
            pass

    # 2) 傳統 filename="..."（支援跳脫字元）
    m = _QUOTED_FN_RE.search(cd)
    if m:
        val = m.group(1)
        val = val.encode("latin-1", "ignore").decode("utf-8", "ignore")
        # 取消跳脫的 \" \\ 等
        val = val.replace(r'\"', '"').replace(r"\\", "\\")
        return val

    # 3) 無引號 filename=xxx.pdf
    m = _UNQUOTED_FN_RE.search(cd)
    if m:
        val = m.group(1)
        # 有些伺服器會 percent-encode
        try:
            val = unquote(val)
        except Exception:
            pass
        return val

    return None

def _guess_filename(response, url: str) -> str:
    """
    response: aiohttp.ClientResponse
    """
    cd = response.headers.get("Content-Disposition")
    # 先從 Content-Disposition 拿
    name = _extract_filename_from_content_disposition(cd) if cd else None

    # 再從 URL 推測
    if not name:
        parsed = urlparse(url)
        # 先看 query 內常見參數（有些站用 ?filename=xxx）
        q = parse_qs(parsed.query)
        for key in ("filename", "file", "name", "download"):
            if key in q and q[key]:
                name = q[key][-1]
                break

    if not name:
        # 用 path 最後一段
        segment = posixpath.basename(urlparse(url).path)
        # 有些 clean URL 不帶副檔名，保留原樣，後續再補 .pdf
        name = segment or "datasheet"

    # 如果沒有副檔名，但 Content-Type 是 pdf，就補 .pdf
    ct = (response.headers.get("Content-Type") or "").lower()
    default_ext = ".pdf" if "application/pdf" in ct and "." not in name else None

    return _sanitize_filename(name, default_ext=default_ext)



QueueItem = int  # download_task.id

class HashableBytesIO(io.BytesIO):
    """擴展 io.BytesIO，確保 hash 會根據內容變動"""

    def __init__(self, source=None):
        self.name: str | None = None

        # 如果 `source` 是 `bytes`，直接初始化
        if isinstance(source, bytes):
            super().__init__(source)
        # 如果 `source` 是 `io.BytesIO` 或 `HashableBytesIO`，複製內容
        elif isinstance(source, io.BytesIO):
            super().__init__(source.getvalue())  # 取得 BytesIO 內容
            if hasattr(source, 'name'):
                self.name = source.name
        # 如果是其他 file-like object（如 open() 的結果）
        elif hasattr(source, 'read'):
            content = source.read()
            super().__init__(content)
            self.name = getattr(source, 'name', None)
        # 如果 `source` 為 `None`，則初始化為空 `BytesIO`
        else:
            super().__init__()
                 
    @property
    def hash(self) -> str:
        """動態計算 SHA-256 hash"""
        hasher = hashlib.sha256()
        hasher.update(self.getvalue())  # 取得內容計算
        return hasher.hexdigest()

class DownloaderWorker:
    def __init__(self, max_concurrency: int = 3, queue_maxsize: int = 0):
        self.queue: asyncio.Queue[QueueItem] = asyncio.Queue(maxsize=queue_maxsize)
        self._workers: list[asyncio.Task] = []
        self._running = False
        self._max_concurrency = max(1, max_concurrency)

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        for _ in range(self._max_concurrency):
            self._workers.append(asyncio.create_task(self._worker_loop()))

    async def stop(self, drain: bool = False) -> None:
        if not self._running:
            return
        self._running = False
        if drain:
            await self.queue.join()
        for _ in self._workers:
            await self.queue.put(-1)  # 停止訊號
        await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers.clear()

    async def enqueue(self, task_id: int) -> None:
        await self.queue.put(task_id)

    async def _worker_loop(self) -> None:
        while True:
            task_id = await self.queue.get()
            try:
                if task_id == -1:
                    return
                await self._run(task_id)
            except asyncio.CancelledError:
                return
            finally:
                self.queue.task_done()

    async def _run(self, task_id: int) -> None:
        db: Session = SessionLocal()
        try:
            t: Optional[DownloadTask] = db.get(DownloadTask, task_id)
            if not t:
                return
            if t.status not in ("queued", "failed"):
                return

            t.status = "running"
            t.started_at = datetime.now(timezone.utc)
            t.error = None
            db.commit()

            # 簡單重試
            attempts, max_retries = 0, 2
            last_exc = None
            while attempts <= max_retries:
                try:
                    datasheet = await self._download_datasheet(datasheet_url=t.source_url, site_name=t.hsd_name)
                    if not datasheet:
                        raise RuntimeError("empty content")
                    file_hash = await self._persist_sync(db, datasheet.getvalue(), datasheet.name, t)
                    t.file_hash = file_hash
                    t.status = "success"
                    t.completed_at = datetime.now(timezone.utc)
                    db.commit()
                    return
                except Exception as e:
                    last_exc = e
                    attempts += 1
                    time.sleep(0.6 * attempts)

            t.status = "failed"
            t.error = f"{last_exc}\n{traceback.format_exc()}"
            t.completed_at = datetime.now(timezone.utc)
            db.commit()

        finally:
            db.close()
            
    async def _download_datasheet(self, datasheet_url: str, site_name: str | None = None, session: aiohttp.ClientSession | None = None) -> HashableBytesIO | None:
        if not session and site_name:
            try:
                session = await aiohttp_hsd_session_manager.get_session(site_name)
            except:
                return None

        async with session.get(datasheet_url) as response:
            if response.status != 200:
                print(f"⚠️ Failed to download {datasheet_url}, status code: {response.status}")
                return None

            # 檢查 Content-Length 是否為 0
            content_length = response.headers.get("Content-Length")
            if content_length and int(content_length) == 0:
                print(f"⚠️ Warning: {datasheet_url} 下載失敗，Content-Length 為 0！")
                return None

            # 讀取內容
            datasheet_bytes = await response.read()
            if not datasheet_bytes:  # 確保不會是空的
                print(f"⚠️ Warning: {datasheet_url} 下載內容為空！")
                return None

            # 轉換為 BytesIO
            datasheet_io = io.BytesIO(datasheet_bytes)

            filename = _guess_filename(response, datasheet_url)

            # # 嘗試從 Header (`Content-Disposition`) 取得檔名
            # content_disposition = response.headers.get("Content-Disposition")
            # filename = None
            # if content_disposition:
            #     match = re.search(r'filename="([^"]+)"', content_disposition)
            #     if match:
            #         filename = match.group(1)

            # # 如果 Header 沒有檔名，嘗試從 URL 提取
            # if not filename:
            #     parsed_url = urlparse(datasheet_url)
            #     path = parsed_url.path
            #     possible_filename = path.split("/")[-1]
            #     if "." in possible_filename:
            #         filename = possible_filename

            # # 如果以上方法都失敗，使用預設名稱
            # if not filename:
            #     filename = "datasheet.pdf"

            datasheet_io.name = filename  # 設定檔案名稱
            return HashableBytesIO(datasheet_io)

    async def _persist_sync(self, db: Session, content: bytes, filename: str, t: DownloadTask) -> str:
        # 走跟 /api/files/upload 相同的入庫流程，維持一致性
        return await persist_bytes_to_store(db, content, filename, source_url=t.source_url)

downloader_worker = DownloaderWorker()
