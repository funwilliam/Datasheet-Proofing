# Datasheet Proofing

以 OpenAI 結構化擷取為基礎的 **Datasheet 校對系統**。支援 PDF 預覽、全文搜尋、AI 擷取依據（logs）提示、逐型號欄位校對、驗證者與時間追蹤、JSON/CSV 匯出，以及指定型號清單的高容量匯出。

---

## 功能總覽（Features）

- **PDF 上傳 / URL 下載**：支援自動檔名推斷、SHA-256 去重。
- **非同步解析佇列**：下載、擷取與匯入以佇列執行，不阻塞前端。
- **嚴格 JSON Schema**：擷取結果對應固定 schema，前端 tooltip 以嚴格鍵路徑讀取。
- **校對頁（Review）**：
  - 左側 PDF.js 預覽 + 全文搜尋（頁碼跳轉）。
  - 右側格線校對：**雙擊 td 編輯**；**Enter 提交 / Esc 取消**。
  - `Applications` 欄位：**每行一個**；**Shift+Enter 換行**。
  - **logs tooltip**（滑鼠 hover td 顯示 AI 擷取依據）。
  - 行底色：`verified（綠）/ unverified（白）`。
  - 有「快照」比對：原本 verified 若內容異動 → UI 暫顯 unverified，改回等價值恢復。
- **驗證流程（Governance）**：
  - 未驗證 → 儲存時自動標記 `verified` 並寫入 reviewer/UTC 時間。
  - 已驗證 + 本次有改 → 維持 `verified`（避免被自動打回）。
  - 已驗證 + 無改 → 保持原驗證資訊不變動。
- **匯出**：
  - 全庫匯出 `/api/export?fmt=json|csv`。
  - **指定型號清單匯出**（大清單 OK）：`POST /api/export/by-models`，支援保序與串流 CSV。
- **全文搜尋**：以 `pdf_text_index` 建立簡易索引，頁面中可關鍵字跳頁。

---

## 系統架構（Architecture）

```
FastAPI (backend)
 ├─ routers/
 │   ├─ files.py          # 檔案清單/上傳/URL 下載入列、全文搜尋、檔案-型號關聯
 │   ├─ models.py         # 型號 CRUD + 驗證與 applications 全量替換
 │   ├─ tasks.py          # 下載/擷取 任務入列與狀態
 │   ├─ extractions.py    # 擷取摘要/擷取檔案回傳（白名單）
 │   ├─ export.py         # 匯出（全庫與 by-models）
 │   └─ static_proxy.py   # 工作目錄白名單靜態檔傳回
 ├─ services/
 │   ├─ downloader_worker.py  # aiohttp 下載 + 去重 + 檔名推斷
 │   ├─ openai_service.py     # 兩階段擷取 + 嚴格 schema 對映 + 成本估算
 │   └─ pdf_text_index.py     # 解析 PDF 文字並建立索引
 ├─ models.py / schemas.py    # SQLAlchemy ORM / Pydantic Schemas
 ├─ templates/
 │   ├─ files.html        # 檔案列表/上傳
 │   ├─ file_detail.html  # 檔案明細/關聯型號
 │   └─ review.html       # 校對頁（PDF + 格線 + logs tooltip）
 └─ main.py               # FastAPI 啟動、Jinja2 設定、頁面路由
```

**資料儲存**：預設 SQLite；工作檔案（PDF、擷取 JSON、匯出）置於 `workspace/`。

---

## 安裝與啟動（Quickstart）

1. 需求：Python 3.10+、`pip`。
2. 建立虛擬環境並安裝：
   ```bash
   python -m venv .venv
   source .venv/bin/activate       # Windows: .venv\Scripts\activate
   pip install -r requirements.txt
   ```
3. 設定環境：
   ```bash
   cp .env.example .env
   # 編輯 .env，至少填入 OPENAI_API_KEY；其餘可用預設
   ```
4. 啟動開發伺服器：
   ```bash
   uvicorn backend.app.main:app --host 127.0.0.1 --port 8000 --reload
   ```
5. 開瀏覽器至 `http://127.0.0.1:8000/`。

---

## 設定檔（Configuration）

`.env` 主要變數（細節以 `settings.py` 為準）：

- `OPENAI_API_KEY`：OpenAI API 金鑰（擷取用）。
- `WORKSPACE_DIR`：工作目錄（預設 `workspace/`）。
- `SQLITE_PATH`：SQLite DB 路徑（預設 `workspace/store/app.db`）。
- 其他：下載/擷取並行數、timeout、模型名稱等。

`settings.py` 啟動時會自動建立必要的子目錄：`inbox/`, `store/`, `extractions/`, `exports/`。

---

## 頁面（Pages）

### 1) 檔案列表（`/`）
- 上傳 PDF、輸入 URL 批次下載。
- 顯示：檔名、hash、大小、建立時間、狀態。
- 操作：解析入列、匯出、進入明細/校對。

### 2) 檔案明細（`/files/{file_hash}`）
- 檔案基本資訊。
- 型號關聯：新增/移除與此檔案的關聯。
- 下載擷取 JSON。

### 3) 校對頁（`/review/{file_hash}`）
- **左側**：PDF.js 頁面預覽、全文搜尋與跳頁。
- **右側**：格線校對
  - **雙擊 td 編輯**；**Enter 提交**、**Esc 取消**。
  - `Applications` 欄位：**每行一個**；**Shift+Enter 換行**；比對採**集合等價**（忽略大小寫與順序）。
  - **logs tooltip**（嚴格 schema 路徑）；hover `td` 顯示。
  - 行底色：`verified`（綠）/ `unverified`（白）；快照比對決定暫時視覺狀態。
- 工具列：輸入當前 reviewer、**儲存全部變更**。

---

## 驗證規則（Review Governance）

- **未驗證** → 按「儲存全部」時，後端自動標記 `verified`，寫入 `reviewer`（若提供）與 `reviewed_at=UTC`。
- **已驗證 + 本次有改** → 前端會在 PATCH 內帶入 `verify_status="verified"`（避免後端打回）；後端更新 `reviewed_at`。
- **已驗證 + 無改** → 前端不送 PATCH，維持原驗證者與時間。

後端 `/api/models/{model_number}` 的 `PATCH` 邏輯：  
- 若 **未傳** `verify_status` 且欄位/Applications 有變動，**且原本是 verified** → 會自動打回 `unverified`。  
- 若 **傳** `verify_status="verified"` → 允許併送 `reviewer`，`reviewed_at` 自動寫入。  
- `applications` 一律 **全量替換**（大小寫正規化 + 去重）。

---

## API 總表（Endpoints）

> 開發時也可參考 `http://127.0.0.1:8000/docs`。以下僅列常用摘要。

### 檔案 / 下載 / 擷取
- `GET  /api/files/`：檔案列表。
- `POST /api/files/upload`（multipart）：上傳一個或多個 PDF。
- `POST /api/files/urls`：提交 URL 清單 → 下載任務入列。
- `GET  /api/files/{file_hash}`：單檔資訊（含關聯型號）。
- `GET  /api/files/{file_hash}/search?q=...`：全文搜尋（回傳頁碼與 snippet）。
- `POST /api/files/{file_hash}/models/{model_number}`：建立檔案與型號的關聯。
- `DELETE /api/files/{file_hash}/models/{model_number}`：解除關聯。

- `POST /api/downloads/enqueue`：下載任務入列（低階）。
- `POST /api/tasks/extractions/queue`：針對檔案清單入列擷取任務（去重）。
- `GET  /api/extractions/{file_hash}`：最近一次擷取摘要。
- `GET  /api/extractions/{file_hash}/file`：回傳擷取 JSON（白名單檢查）。

### 型號資料（Models）
- `GET    /api/models/{model_number}`：查單筆型號。
- `PATCH  /api/models/{model_number}`：更新欄位與驗證狀態、applications（全量替換）。
- `DELETE /api/models/{model_number}`：刪整筆（含 applications 與 file link）。

> 建議：未來可新增 `PATCH /api/models/_bulk` 批次更新。

### 匯出（Export）
- **全庫匯出**：`GET /api/export?status=verified&fmt=json|csv`
- **指定型號清單匯出（大清單 OK）**：
  - `POST /api/export/by-models`
  - Request JSON：
    ```json
    {
      "model_numbers": ["ABC-100","XYZ-999","..."],
      "status": "verified",
      "fmt": "json",
      "preserve_order": true
    }
    ```
  - `fmt=csv` 走串流輸出，不吃記憶體。

#### cURL 範例
```bash
# 全庫 CSV（僅 verified）
curl "http://127.0.0.1:8000/api/export?status=verified&fmt=csv" -o models.csv

# 指定清單 JSON（依序輸出）
curl -X POST "http://127.0.0.1:8000/api/export/by-models" \
  -H "Content-Type: application/json" \
  -d '{"model_numbers":["A-100","B-200","C-300"],"fmt":"json","preserve_order":true}'
```

---

## 資料模型（Data Model 梗概）

- `ModelItem`：
  - `model_number: str`
  - `input_voltage_range, output_voltage, output_power, package, isolation, insulation, dimension, notes`
  - `verify_status: "unverified"|"verified"`
  - `reviewer: Optional[str]`
  - `reviewed_at: Optional[datetime(UTC)]`
  - 關聯：`applications: List[ModelApplicationTag]`、`files: List[FileAsset]`
- `ModelApplicationTag`：
  - `app_tag: str`、`app_tag_canon: str`（lowercase）
  - 關聯至 `ModelItem`
- `FileAsset`：
  - `file_hash, filename, size, created_at...`
  - 關聯：`models: List[ModelItem]`
- 其他：`FileModelAppearance`（關聯表）、`DownloadTask`、`ExtractionTask` 等。

> 詳細欄位以 `backend/app/models.py` 為準。

---

## 擷取流程（Extraction Pipeline）

1. 上傳或下載 PDF → 建立 `FileAsset`（SHA-256 去重）。
2. 入列擷取任務（手動或批次）。
3. `openai_service`：
   - 第一步抽出 `Model Number` 列表。
   - 第二步對每個型號抽出欄位，對應嚴格 schema。
   - 計算 token 成本，寫入 DB 與 `extractions/` JSON。

---

## 安全與部署（Security/Deployment）

- 預設 **無認證**，適用內網。若開放外網，建議加上：
  - 反向代理 + Basic Auth 或 OIDC。
  - 限制檔案大小、來源網域白名單。
- `static_proxy` 與 `extractions.get_file` 使用 **白名單路徑**，避免任意讀檔。

部署建議：
- 將 `workspace/` 與 DB 目錄掛載到永續儲存。
- 若資料量大，升級至 Postgres；`applications` 可改 JSONB。

---

## 開發建議（Development）

- 加入 pre-commit：`ruff`, `black`, `isort`, `mypy`。
- 增加 API 測試（pytest + httpx）。
- `review.html`：將 `document.execCommand('insertLineBreak')` 改為 Range API 插入 `\n`。
- 後端新增 `PATCH /api/models/_bulk`，減少多筆更新的 round-trip。

---

## 疑難排解（Troubleshooting）

- 「匯出 500」：請確認 `export.py` 無使用不存在欄位排序；已修正為 `model_number.asc()`。
- 「logs tooltip 沒出現」：檢視模式下 hover `td` 才會顯示；請確認擷取 JSON 的鍵路徑符合 `review.html` 的 `LOG_SRC_PATH`。
- 「Applications 改了順序卻被判定有變更」：已實作集合等價比較（忽略大小寫/順序）；若仍出現，請檢查是否多了空白字元或不同分隔。

---

## 授權（License）

建議 MIT（視你的專案政策而定）。

---

## 致謝（Acknowledgements）

- PDF.js
- FastAPI / Starlette
- SQLAlchemy / Pydantic
- OpenAI API
