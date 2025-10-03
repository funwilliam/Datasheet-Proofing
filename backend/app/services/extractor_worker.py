# backend/app/services/extractor_worker.py

import asyncio
import traceback
from datetime import datetime, timezone
from typing import Optional, Tuple, Dict

from sqlalchemy.orm import Session

from ..db import SessionLocal
from ..models import ExtractionTask
from .openai_service import extract_with_openai

# 佇列項目：(task_id, force_rerun)
QueueItem = Tuple[int, bool]


class ExtractorWorker:
    def __init__(self, max_concurrency: int = 1, queue_maxsize: int = 0):
        self.queue: asyncio.Queue[QueueItem] = asyncio.Queue(maxsize=queue_maxsize)
        self._workers: list[asyncio.Task] = []
        self._running = False
        self._max_concurrency = max(1, max_concurrency)

        # 關閉流程/中止控制
        self._shutting_down: bool = False

        # 正在執行中的任務：task_id -> asyncio.Task (wrap 的 executor future)
        self._inflight: Dict[int, asyncio.Task] = {}

    # ─────────────────────────────────────────────────────────
    # 啟動/停止

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._shutting_down = False
        for _ in range(self._max_concurrency):
            self._workers.append(asyncio.create_task(self._worker_loop()))

    async def stop(self, drain: bool = False, timeout_s: float = 5.0) -> None:
        """
        停止 worker：
          - 若 drain=True：等 queue 跑完（不建議在 Ctrl+C 場景）
          - 若 drain=False（預設）：不等 OpenAI 回來
              * 先把 queue 尚未開跑的任務標記 canceled
              * 等待正在執行的任務至多 timeout_s 秒；逾時將其標記 canceled
        """
        if not self._running:
            return

        self._shutting_down = True

        if drain:
            # 等到 queue 全部完成
            await self.queue.join()
        else:
            # 1) 清空 queue，將尚未開跑者設為 canceled
            await self._cancel_all_queued()

            # 2) 等待 inflight 中的執行至多 timeout_s
            await self._await_inflight_with_timeout(timeout_s)

        # 通知 worker 退出
        for _ in self._workers:
            await self.queue.put((-1, False))
        await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers.clear()
        self._running = False

    async def _cancel_all_queued(self) -> None:
        """
        取出 queue 中尚未開始的任務（不阻塞），逐一標記 canceled。
        """
        while True:
            try:
                task_id, _force = self.queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            try:
                if task_id != -1:
                    self._mark_canceled_before_start(task_id)
            finally:
                self.queue.task_done()

    async def _await_inflight_with_timeout(self, timeout_s: float) -> None:
        """
        等待 inflight 任務至多 timeout_s 秒；超時將其 DB 狀態標記 canceled。
        """
        if not self._inflight:
            return

        # 建一個 snapshot，避免遍歷過程字典變動
        snapshot = list(self._inflight.items())

        try:
            # 等待所有 inflight 結束或 timeout
            await asyncio.wait(
                [task for _, task in snapshot],
                timeout=timeout_s,
                return_when=asyncio.ALL_COMPLETED,
            )
        finally:
            # 標記尚未完成者為 canceled
            for task_id, task in snapshot:
                if not task.done():
                    self._mark_aborted_by_shutdown(task_id)

    # ─────────────────────────────────────────────────────────
    # 公開 API：入列時就先建 DB row（/tasks 立即可見 queued）

    async def enqueue(self, file_hash: str, force_rerun: bool = False) -> int:
        """
        建立一筆 ExtractionTask(status='queued')，回傳 task_id，
        並把 (task_id, force_rerun) 丟進 queue。
        若正在關閉中，仍會建 row 以保留記錄，但不會讓 worker 開跑（稍後 stop 會統一 canceled）。
        """
        db: Session = SessionLocal()
        try:
            t = ExtractionTask(
                file_hash=file_hash,
                mode="sync",
                status="queued",
                created_at=datetime.now(timezone.utc),
            )
            db.add(t)
            db.commit()
            task_id = t.id
        finally:
            db.close()

        # 若已進入關閉流程，讓 stop() 統一處理取消
        if not self._shutting_down:
            await self.queue.put((task_id, force_rerun))
        return task_id

    # ─────────────────────────────────────────────────────────
    # 內部工具：DB 標記

    def _mark_failed(self, db: Session, t: ExtractionTask, err: str) -> None:
        t.status = "failed"
        t.error = err
        t.completed_at = datetime.now(timezone.utc)
        db.commit()

    def _mark_canceled_before_start(self, task_id: int) -> None:
        """
        尚未開始的任務（仍在 queue），標記為 canceled。
        """
        db: Session = SessionLocal()
        try:
            t: Optional[ExtractionTask] = db.get(ExtractionTask, task_id)
            if not t:
                return
            if t.status not in ("queued",):
                return
            t.status = "canceled"
            t.error = "canceled before start due to shutdown"
            t.completed_at = datetime.now(timezone.utc)
            db.commit()
        finally:
            db.close()

    def _mark_aborted_by_shutdown(self, task_id: int) -> None:
        """
        已經開始執行但未在 timeout 內完成的任務，標記為 canceled。
        """
        db: Session = SessionLocal()
        try:
            t: Optional[ExtractionTask] = db.get(ExtractionTask, task_id)
            if not t:
                return
            # 僅針對 running 态做中止標記；避免覆寫已完成/失敗/取消
            if t.status == "running":
                t.status = "canceled"
                t.error = "aborted by shutdown (timed out waiting)"
                t.completed_at = datetime.now(timezone.utc)
                db.commit()
        finally:
            db.close()

    # ─────────────────────────────────────────────────────────
    # 核心：由 task_id 執行一次擷取

    def _run_one_by_id(self, task_id: int, force_rerun: bool) -> None:
        """
        根據 task_id 執行一次同步擷取（覆用 extract_with_openai）。
        注意：此方法在 ThreadPoolExecutor 中執行。
        """
        db: Session = SessionLocal()
        t: Optional[ExtractionTask] = None
        try:
            t = db.get(ExtractionTask, task_id)
            if not t:
                # 找不到任務，無法處理
                return

            # 僅允許 queued/failed 重新執行
            if t.status not in ("queued", "failed"):
                return

            if not t.file_hash:
                self._mark_failed(db, t, "file_hash is empty on task")
                return

            # 進入執行中
            t.status = "running"
            t.started_at = datetime.now(timezone.utc)
            db.commit()

            # 呼叫最新的 extract_with_openai（回傳 dict）
            res = extract_with_openai(
                db,
                t.file_hash,
                force_rerun,
                model_name="gpt-5",
                mode=t.mode or "sync",
                service_tier=t.service_tier,  # 若之前有指定 tier，沿用；否則 None
            )

            # 再次讀取最新狀態，避免與關閉流程的 canceled 衝突
            db.refresh(t)
            if t.status == "canceled":
                # 已被 stop() 標記中止，不覆寫狀態
                return

            # 寫回各欄位
            t.status = res.get("status")
            t.response_path = res.get("out_path")
            t.cost_usd = res.get("cost_usd")
            t.prompt_tokens = res.get("prompt_tokens")           # input + cached_input
            t.completion_tokens = res.get("completion_tokens")   # output
            t.openai_model = res.get("model") or t.openai_model
            t.service_tier = res.get("service_tier") or t.service_tier

            # 細項 tokens（若你已在 models.py 新增欄位）
            usage = (res.get("usage") or {})
            t.input_tokens = usage.get("input")
            t.cached_input_tokens = usage.get("cached_input")
            t.output_tokens = usage.get("output")

            t.completed_at = datetime.now(timezone.utc)
            db.commit()

        except Exception as e:
            if t is not None:
                # 再次讀取，避免覆寫 canceled
                try:
                    db.refresh(t)
                    if t.status == "canceled":
                        return
                except Exception:
                    pass
                self._mark_failed(db, t, f"{e}\n{traceback.format_exc()}")
            else:
                # 連任務都取不到時，不再嘗試建新紀錄（task_id 既已存在）
                pass
        finally:
            db.close()

    # ─────────────────────────────────────────────────────────
    # Worker 迴圈

    async def _worker_loop(self) -> None:
        while True:
            task_id, force_rerun = await self.queue.get()
            try:
                if task_id == -1:
                    return

                # 若已進入關閉流程，不再開跑；這筆任務交給 stop() 統一 canceled
                if self._shutting_down:
                    continue

                # 正確做法：用 to_thread 產生 coroutine，再 create_task
                coro = asyncio.to_thread(self._run_one_by_id, task_id, force_rerun)
                atask = asyncio.create_task(coro)
                self._inflight[task_id] = atask

                try:
                    await atask
                finally:
                    self._inflight.pop(task_id, None)

            except asyncio.CancelledError:
                return
            finally:
                self.queue.task_done()


extractor_worker = ExtractorWorker()
